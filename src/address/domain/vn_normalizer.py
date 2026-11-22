"""
Vietnamese address normalizer - approach_03
Branch: expert/backend-sec-m1
Pure domain logic: no provider, no DB, no I/O. Deterministic and testable.

Principles (backend-sec):
- Fail-closed: unknown tokens are KEPT in `unmatched_tokens` for risk scoring,
  never silently dropped and replaced by a guess.
- No default address ever. This module only normalizes, it never invents
  province/district/ward.
- Parallel forms: keep diacritic form AND ascii form for matching, because
  customers type both "Đắk Lắk" and "Dak Lak / Dak Lak / Đăk Lăk".
Status: chua chay - CI se bao ket qua py_compile / pytest.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

ALIAS_DICT_VERSION = "2025.07-v1"  # bump when admin units / aliases change

# ---------------------------------------------------------------------------
# Diacritic removal
# ---------------------------------------------------------------------------
def strip_diacritics(text: str) -> str:
    # đ/Đ do not decompose in NFD, handle explicitly first.
    text = text.replace("đ", "d").replace("Đ", "D")
    nfd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def to_nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


# ---------------------------------------------------------------------------
# Abbreviations - expanded ONLY when token-boundary safe
# ---------------------------------------------------------------------------
ABBREVIATIONS: Dict[str, str] = {
    "tp": "thành phố", "t.p": "thành phố", "tp.": "thành phố",
    "q": "quận", "q.": "quận",
    "p": "phường", "p.": "phường",
    "h": "huyện", "h.": "huyện",
    "x": "xã", "x.": "xã",
    "tx": "thị xã", "t.x": "thị xã",
    "tt": "thị trấn", "t.t": "thị trấn",
    "đ": "đường", "d": "đường", "đ.": "đường",
    "ng": "ngõ", "ng.": "ngõ",
    "kdc": "khu dân cư", "kđt": "khu đô thị", "kdt": "khu đô thị",
    "tdp": "tổ dân phố", "kp": "khu phố",
    "hn": "hà nội", "hcm": "hồ chí minh", "sg": "hồ chí minh",
    "dn": "đà nẵng", "hp": "hải phòng", "ct": "cần thơ",
    "bd": "bình dương", "dna": "đồng nai",
}

# Place-name aliases: canonical ascii -> canonical diacritic form.
# Versioned, extendable from carrier return feedback. Keys are ascii-normalized.
PLACE_ALIASES: Dict[str, str] = {
    # Dak Lak family - the reported failure case
    "dak lak": "đắk lắk", "daklac": "đắk lắk", "dac lac": "đắk lắk",
    "dak lac": "đắk lắk", "daclac": "đắk lắk", "dak lak": "đắk lắk",
    "cuor dang": "cuôr đăng", "cuor dang": "cuôr đăng",
    "cuo dang": "cuôr đăng", "kuor dang": "cuôr đăng",
    "ea": "ea", "krong": "krông", "krong pak": "krông pắc",
    "krong ana": "krông ana", "buon ma thuot": "buôn ma thuột",
    "buon": "buôn",
    # Gia Lai / Mang Yang - must NEVER be used as a default, only as alias
    "dak ya": "đăk yă", "dac ya": "đăk yă", "mang yang": "mang yang",
    # Common confusions
    "gia lai": "gia lai", "hai duong": "hải dương", "chi linh": "chí linh",
    "pha lai": "phả lại", "nghe an": "nghệ an", "cao bang": "cao bằng",
    "hung yen": "hưng yên", "thu dau mot": "thủ dầu một",
    "tan binh": "tân bình", "lam son": "lam sơn",
}

# Telex / VNI artefact fixes: only conservative, word-level patterns.
# Full Telex decoding is ambiguous, so we fix frequent address tokens only.
TELEX_FIXES: Dict[str, str] = {
    "dakws": "đắk", "dakslakw": "đắk lắk", "cuoor": "cuôr",
    "phuwowng": "phường", "xax": "xã", "huyeen": "huyện",
    "tinh": "tỉnh", "duowng": "đường",
}
VNI_FIXES: Dict[str, str] = {
    "da8k": "đăk", "da81k": "đắk", "la81k": "lắk",
    "phu7o7ng": "phường", "xa5": "xã",
}

ADMIN_PREFIXES = (
    "thành phố", "thanh pho", "tỉnh", "tinh", "quận", "quan",
    "huyện", "huyen", "thị xã", "thi xa", "thị trấn", "thi tran",
    "phường", "phuong", "xã", "xa",
)

_PUNCT_RE = re.compile(r"[.,;:!?/\\|()\[\]{}<>\"']+")
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class NormalizedAddress:
    raw: str
    nfc: str                    # NFC, original casing cleaned
    lower_diacritic: str        # lowercase, diacritics kept
    ascii_form: str             # lowercase, diacritics stripped - matching key
    tokens_diacritic: Tuple[str, ...]
    tokens_ascii: Tuple[str, ...]
    expanded_diacritic: str     # abbreviations expanded, aliases applied
    expanded_ascii: str
    unmatched_tokens: Tuple[str, ...] = field(default_factory=tuple)
    alias_dict_version: str = ALIAS_DICT_VERSION
    warnings: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def char_len(self) -> int:
        return len(self.ascii_form.replace(" ", ""))

    @property
    def is_too_short(self) -> bool:
        """Golden regression guard: '73', '127', 'bi', 'Xom' must be flagged."""
        return self.char_len < 10 or len(self.tokens_ascii) < 3

    @property
    def is_numeric_only(self) -> bool:
        return bool(re.fullmatch(r"[\d\s]+", self.ascii_form.strip() or " "))


def _expand_abbreviations(tokens: List[str]) -> List[str]:
    out: List[str] = []
    for tok in tokens:
        replacement = ABBREVIATIONS.get(tok)
        if replacement:
            out.extend(replacement.split())
        else:
            out.append(tok)
    return out


def _apply_place_aliases(ascii_text: str) -> str:
    # Longest alias first so 'buon ma thuot' wins over 'buon'.
    for alias in sorted(PLACE_ALIASES, key=len, reverse=True):
        canonical_ascii = strip_diacritics(PLACE_ALIASES[alias]).lower()
        ascii_text = re.sub(rf"\b{re.escape(alias)}\b", canonical_ascii, ascii_text)
    return ascii_text


def _fix_input_method(tokens: List[str]) -> Tuple[List[str], List[str]]:
    warnings: List[str] = []
    out: List[str] = []
    for tok in tokens:
        low = tok.lower()
        if low in TELEX_FIXES:
            out.append(TELEX_FIXES[low]); warnings.append(f"telex_fix:{low}")
        elif low in VNI_FIXES:
            out.append(VNI_FIXES[low]); warnings.append(f"vni_fix:{low}")
        else:
            out.append(tok)
    return out, warnings


def normalize(raw: str, known_ascii_vocab: set[str] | None = None) -> NormalizedAddress:
    """
    Normalize without inventing information.
    known_ascii_vocab: optional set of known place/street tokens (ascii).
    Tokens not in vocab and not generic are preserved as unmatched_tokens.
    """
    if raw is None:
        raw = ""
    nfc = _SPACE_RE.sub(" ", to_nfc(raw)).strip()
    cleaned = _PUNCT_RE.sub(" ", nfc)
    cleaned = _SPACE_RE.sub(" ", cleaned).strip()

    lower_diacritic = cleaned.lower()
    ascii_form = strip_diacritics(lower_diacritic).lower()
    ascii_form = _SPACE_RE.sub(" ", ascii_form).strip()

    tokens_ascii_raw = ascii_form.split() if ascii_form else []
    tokens_diacritic_raw = lower_diacritic.split() if lower_diacritic else []

    # Input-method fixes operate on diacritic tokens where possible.
    fixed_tokens, warnings = _fix_input_method(tokens_diacritic_raw)
    fixed_diacritic = " ".join(fixed_tokens)
    fixed_ascii = strip_diacritics(fixed_diacritic).lower()

    expanded_ascii_tokens = _expand_abbreviations(fixed_ascii.split())
    expanded_ascii = _apply_place_aliases(" ".join(expanded_ascii_tokens))
    expanded_diacritic = _apply_place_aliases(
        strip_diacritics(" ".join(_expand_abbreviations(fixed_tokens))).lower()
    )
    # expanded_diacritic intentionally stores ascii-canonical form when an
    # alias fired; callers should use lower_diacritic for display and
    # expanded_ascii for matching. Kept explicit to avoid silent data loss.

    generic = {"xa", "phuong", "huyen", "quan", "tinh", "thanh", "pho",
               "thi", "tran", "duong", "so", "thon", "xom", "ap", "khu"}
    unmatched: List[str] = []
    if known_ascii_vocab is not None:
        for tok in expanded_ascii.split():
            if tok not in known_ascii_vocab and tok not in generic and not tok.isdigit():
                unmatched.append(tok)

    if not nfc:
        warnings.append("empty_input")
    if len(expanded_ascii.replace(" ", "")) < 10:
        warnings.append("too_short_input")

    return NormalizedAddress(
        raw=raw,
        nfc=nfc,
        lower_diacritic=lower_diacritic,
        ascii_form=ascii_form,
        tokens_diacritic=tuple(fixed_tokens),
        tokens_ascii=tuple(expanded_ascii.split()),
        expanded_diacritic=fixed_diacritic,
        expanded_ascii=expanded_ascii,
        unmatched_tokens=tuple(unmatched),
        warnings=tuple(warnings),
    )


# Convenience for matcher layers in other modules.
def canonical_place_ascii(name: str) -> str:
    return _apply_place_aliases(strip_diacritics(to_nfc(name or "").lower()).strip())
