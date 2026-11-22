"""
Vietnamese normalizer — fix_normalizer_alias_expansion.

Ba lỗi đã sửa, đúng phương án hội đồng đã thống nhất:

1. Trùng khóa alias bị ghi đè im lặng.
   Bản cũ dùng dict `PLACE_ALIASES` với khóa `'dak lak'` khai báo hai lần;
   Python dict giữ giá trị cuối, mất giá trị đầu mà không cảnh báo.
   Sau fix, `PLACE_ALIASES` là danh sách cặp `(alias, canonical)` — một
   khóa không thể xuất hiện hai lần trong danh sách mà không bị phát hiện:
   `_build_alias_dict()` kiểm tra trùng khóa (dạng token tuple đã chuẩn
   hóa) và raise `ValueError` ngay khi import nếu có trùng. `PLACE_ALIASES`
   vẫn dùng được như dict (`PLACE_ALIASES["dak lak"]`, `.get()`,
   `"dak lak" in PLACE_ALIASES`) nhờ lớp `_AliasList`, và
   `PLACE_ALIASES_DICT` là dict đã kiểm tra trùng.

2. `expanded_diacritic` gán sai biến.
   Bản cũ tính `expanded_diacritic = _apply_place_aliases(...)` rồi
   không dùng kết quả đó, mà gán lại `expanded_diacritic = fixed_diacritic`
   (chưa expand alias / viết tắt). Sau fix, `normalize()` tính theo
   đúng thứ tự và gán đúng biến đã tính:
       fixed_diacritic   = _apply_place_aliases(cleaned)
       expanded_abbrev   = _apply_abbreviations(fixed_diacritic)
       expanded_diacritic = _apply_place_aliases(expanded_abbrev)
   Ví dụ: `normalize("tp hcm").expanded_diacritic == "thành phố hồ chí minh"`,
   trong khi `fixed_diacritic == "tp hồ chí minh"` — hai giá trị khác nhau
   đúng như kỳ vọng, caller tin `expanded_diacritic` sẽ không match sai.

3. Expand token đơn mơ hồ tạo bằng chứng giả.
   Bản cũ map `'d' -> 'đường'` và `'hn'/'sg' -> tỉnh/thành` ở mức token
   đơn. Token đơn mơ hồ chính là nhóm lỗi gốc (`bi`, `Xom`): một token
   đứng một mình không đủ ngữ cảnh để khẳng định nó là viết tắt.
   Sau fix:
     - `'hn'` và `'sg'` KHÔNG expand trong `normalize()` — chúng chỉ được
       resolve bằng anchor / xác nhận của khách, không phải bằng normalizer.
     - `'d'` KHÔNG có trong `ABBREVIATIONS`; nó chỉ expand trong
       `CONTEXTUAL_ABBREVIATIONS` khi đứng trước tên đường và có ngữ
       cảnh: ở đầu đoạn hoặc sau số nhà, và có token tên đường theo sau.
       `'d'` đứng một mình thì giữ nguyên `'d'`.
     - `'bi'`, `'xom'` không có trong bất kỳ bảng alias / viết tắt nào.

Nhận diện alias theo ranh giới từ: alias được khớp thành dãy token liên
tiếp hoàn chỉnh trong từng đoạn (ngăn bởi dấu phẩy), dài nhất trước,
không khớp chuỗi con trong một token.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional, Sequence

__all__ = [
    "PLACE_ALIASES",
    "PLACE_ALIASES_DICT",
    "ABBREVIATIONS",
    "CONTEXTUAL_ABBREVIATIONS",
    "AMBIGUOUS_SINGLE_TOKENS",
    "NormalizedText",
    "VietnameseNormalizer",
    "normalize",
    "normalize_address",
    "normalize_text",
    "strip_diacritics",
    "fix_diacritics",
    "_apply_place_aliases",
    "_apply_abbreviations",
    "_build_alias_dict",
    "check_alias_duplicates",
]


# ---------------------------------------------------------------------------
# Helpers: bỏ dấu, token
# ---------------------------------------------------------------------------

def strip_diacritics(text: str) -> str:
    """Bỏ dấu tiếng Việt, giữ nguyên chữ cái: 'Đắk Lắk' -> 'Dak Lak'."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return stripped.replace("Đ", "D").replace("đ", "d")


def _word_tokens(text: str) -> list[str]:
    """Tách token từ, giữ dấu và dấu nháy đơn trong tên như "m'gar"."""
    return re.findall(r"[\w]+(?:['’][\w]+)?", text.lower(), flags=re.UNICODE)


def _tokens_unsigned(text: str) -> tuple[str, ...]:
    return tuple(strip_diacritics(t).lower() for t in _word_tokens(text))


# ---------------------------------------------------------------------------
# Bảng alias địa danh: danh sách cặp, không dùng dict literal (tránh trùng khóa)
# ---------------------------------------------------------------------------

class _AliasList(list):
    """Danh sách cặp (alias, canonical) nhưng dùng được như dict cho
    các caller cũ: PLACE_ALIASES["dak lak"], .get(), "dak lak" in ..."""

    def __getitem__(self, key):  # type: ignore[override]
        if isinstance(key, str):
            for alias, canonical in self:
                if alias == key:
                    return canonical
            raise KeyError(key)
        return super().__getitem__(key)

    def get(self, key, default=None):  # type: ignore[override]
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):  # type: ignore[override]
        if isinstance(key, str):
            return any(alias == key for alias, _ in self)
        return super().__contains__(key)

    def keys(self):
        return [alias for alias, _ in self]

    def items(self):
        return list(self)

    def values(self):
        return [canonical for _, canonical in self]


# Khóa là dạng không dấu, lower, đã chuẩn hóa khoảng trắng.
# Mỗi khóa chỉ xuất hiện đúng một lần — _build_alias_dict() sẽ raise nếu trùng.
PLACE_ALIASES = _AliasList([
    ("dak lak", "đắk lắk"),
    ("dak ya", "đăk yă"),
    ("cuor dang", "cuôr đăng"),
    ("cu mgar", "cư m'gar"),
    ("cu m'gar", "cư m'gar"),
    ("krong buk", "krông búk"),
    ("krong pac", "krông pắc"),
    ("ea h'leo", "ea h'leo"),
    ("ea hleo", "ea h'leo"),
    ("ben nghe", "bến nghé"),
    ("ho chi minh", "hồ chí minh"),
    ("thanh pho ho chi minh", "thành phố hồ chí minh"),
    ("hcm", "hồ chí minh"),
    ("ha noi", "hà nội"),
    ("hai duong", "hải dương"),
    ("gia lai", "gia lai"),
    ("nghe an", "nghệ an"),
    ("cao bang", "cao bằng"),
    ("hung yen", "hưng yên"),
    ("binh duong", "bình dương"),
    ("le loi", "lê lợi"),
    ("nguyen trai", "nguyễn trãi"),
    ("lam son", "lam sơn"),
    ("tan binh", "tân bình"),
    ("pha lai", "phả lại"),
    ("chi linh", "chí linh"),
    ("thanh my", "thanh mỹ"),
    ("hop giang", "hợp giang"),
    ("hien nam", "hiến nam"),
    ("phu cuong", "phú cường"),
    ("thu dau mot", "thủ dầu một"),
])


def check_alias_duplicates(pairs: Iterable[tuple[str, str]]) -> list[str]:
    """Trả về các khóa alias bị trùng (dạng token tuple đã chuẩn hóa)."""
    seen: set[tuple[str, ...]] = set()
    duplicates: list[str] = []
    for alias, _ in pairs:
        key = _tokens_unsigned(alias)
        if key in seen and alias not in duplicates:
            duplicates.append(alias)
        seen.add(key)
    return duplicates


def _build_alias_dict(pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Xây dict từ danh sách cặp, raise ngay nếu có trùng khóa.

    Đây là tuyến phòng thủ thay cho việc dict literal ghi đè im lặng:
    trùng khóa trong tương lai sẽ vỡ ở lúc import / test, không vỡ
    âm thầm ở lúc match địa chỉ.
    """
    duplicates = check_alias_duplicates(pairs)
    if duplicates:
        raise ValueError(f"duplicate place-alias keys: {duplicates}")
    return {alias: canonical for alias, canonical in pairs}


PLACE_ALIASES_DICT: dict[str, str] = _build_alias_dict(PLACE_ALIASES)
# Map token-tuple (không dấu) -> canonical, dùng cho khớp ranh giới từ.
_ALIAS_BY_TOKENS: dict[tuple[str, ...], str] = {
    _tokens_unsigned(alias): canonical for alias, canonical in PLACE_ALIASES
}
_ALIAS_LENGTHS = sorted({len(k) for k in _ALIAS_BY_TOKENS}, reverse=True)

# Viết tắt được expand vô điều kiện (token hoàn chỉnh, không mơ hồ).
# CỐ TÌNH KHÔNG có 'd', 'hn', 'sg' ở đây — xem CONTEXTUAL_ABBREVIATIONS
# và AMBIGUOUS_SINGLE_TOKENS bên dưới.
ABBREVIATIONS: dict[str, str] = {
    "tp": "thành phố",
    "tphcm": "thành phố hồ chí minh",
    "tx": "thị xã",
    "tt": "thị trấn",
    "q": "quận",
    "h": "huyện",
    "p": "phường",
    "x": "xã",
}

# 'd' chỉ expand khi có ngữ cảnh tên đường (xem _apply_abbreviations).
CONTEXTUAL_ABBREVIATIONS: dict[str, str] = {"d": "đường"}

# Token đơn mơ hồ: đứng một mình thì KHÔNG được expand thành bất kỳ
# địa danh / cấp hành chính nào. Đây là nhóm lỗi gốc 'bi', 'Xom'.
AMBIGUOUS_SINGLE_TOKENS = frozenset({"hn", "sg", "d", "bi", "xom"})


# ---------------------------------------------------------------------------
# Áp alias và viết tắt
# ---------------------------------------------------------------------------

def _apply_place_aliases(text: str) -> str:
    """Khôi phục dấu cho tên địa danh đã biết, theo ranh giới từ.

    Khớp dãy token liên tiếp hoàn chỉnh trong từng đoạn (ngăn bởi dấu
    phẩy), dài nhất trước. Token không nằm trong bảng alias giữ nguyên
    (kể cả dấu gốc của nó), nên không làm mất dấu của tên đường khác.
    """
    if not text:
        return ""
    out_segments: list[str] = []
    for segment in text.split(","):
        tokens = _word_tokens(segment)
        result: list[str] = []
        i = 0
        while i < len(tokens):
            matched = False
            for length in _ALIAS_LENGTHS:
                if i + length <= len(tokens):
                    key = tuple(strip_diacritics(t).lower() for t in tokens[i:i + length])
                    canonical = _ALIAS_BY_TOKENS.get(key)
                    if canonical is not None:
                        result.append(canonical)
                        i += length
                        matched = True
                        break
            if not matched:
                result.append(tokens[i])
                i += 1
        out_segments.append(" ".join(result))
    return ", ".join(out_segments)


def _apply_abbreviations(text: str) -> str:
    """Expand viết tắt vô điều kiện + viết tắt 'd' có ngữ cảnh.

    - ABBREVIATIONS: chỉ khi token khớp hoàn chỉnh, không phải chuỗi con.
    - 'd' -> 'đường' chỉ khi: có token tên đường theo sau (chữ cái, không
      phải số, không phải tiền tố hành chính) VÀ ('d' ở đầu đoạn hoặc
      token trước nó là số nhà). 'd' đứng một mình thì giữ nguyên.
    - 'hn' / 'sg' không bao giờ expand ở đây.
    """
    if not text:
        return ""
    out_segments: list[str] = []
    for segment in text.split(","):
        tokens = _word_tokens(segment)
        result: list[str] = []
        for idx, tok in enumerate(tokens):
            unsigned = strip_diacritics(tok).lower()
            if unsigned in ABBREVIATIONS:
                result.append(ABBREVIATIONS[unsigned])
                continue
            if unsigned in CONTEXTUAL_ABBREVIATIONS:
                next_tok = tokens[idx + 1] if idx + 1 < len(tokens) else None
                prev_tok = tokens[idx - 1] if idx > 0 else None
                has_street_name_after = (
                    next_tok is not None
                    and not next_tok.isdigit()
                    and strip_diacritics(next_tok).lower() not in {
                        "phuong", "xa", "quan", "huyen", "tinh", "thanh", "tp", "tt", "tx",
                    }
                )
                has_context_before = idx == 0 or (prev_tok is not None and prev_tok.isdigit())
                if has_street_name_after and has_context_before:
                    result.append(CONTEXTUAL_ABBREVIATIONS[unsigned])
                    continue
                result.append(tok)
                continue
            result.append(tok)
        out_segments.append(" ".join(result))
    return ", ".join(out_segments)


def fix_diacritics(text: str) -> str:
    """Alias cho _apply_place_aliases, giữ tên cũ cho caller cũ."""
    return _apply_place_aliases(text)


# ---------------------------------------------------------------------------
# normalize()
# ---------------------------------------------------------------------------

class NormalizedText(str):
    """Kết quả normalize, vừa là str (giá trị = expanded_diacritic) vừa
    mang các trường mà caller cũ dùng: .fixed_diacritic,
    .expanded_diacritic, .unsigned, .normalized, .tokens, .original.

    Nhờ kế thừa str, cả hai cách dùng đều đúng:
        normalize("dak lak") == "đắk lắk"
        normalize("dak lak").expanded_diacritic == "đắk lắk"
    """

    def __new__(cls, expanded: str, *, original: str = "", fixed_diacritic: str = "",
                unsigned: str = "", tokens: Sequence[str] = ()):
        obj = super().__new__(cls, expanded)
        obj.original = original
        obj.fixed_diacritic = fixed_diacritic
        obj.expanded_diacritic = expanded
        obj.normalized = expanded
        obj.unsigned = unsigned
        obj.tokens = list(tokens)
        return obj


def normalize(text: str) -> NormalizedText:
    """Chuẩn hóa địa chỉ tiếng Việt.

    Thứ tự bắt buộc, và expanded_diacritic PHẢI được gán từ biến đã tính
    ở bước cuối, không được gán lại bằng fixed_diacritic:
        cleaned            = lower, gộp khoảng trắng
        fixed_diacritic    = _apply_place_aliases(cleaned)
        expanded_abbrev    = _apply_abbreviations(fixed_diacritic)
        expanded_diacritic = _apply_place_aliases(expanded_abbrev)
    """
    original = text or ""
    cleaned = re.sub(r"\s+", " ", original.strip().lower())
    fixed_diacritic = _apply_place_aliases(cleaned)
    expanded_abbrev = _apply_abbreviations(fixed_diacritic)
    # Gán đúng biến đã tính — đây chính là lỗi đã sửa (bản cũ gán lại
    # expanded_diacritic = fixed_diacritic ở đây).
    expanded_diacritic = _apply_place_aliases(expanded_abbrev)
    unsigned = strip_diacritics(expanded_diacritic)
    return NormalizedText(
        expanded_diacritic,
        original=original,
        fixed_diacritic=fixed_diacritic,
        unsigned=unsigned,
        tokens=_word_tokens(expanded_diacritic),
    )


def normalize_address(text: str) -> NormalizedText:
    """Alias của normalize(), cho caller dùng tên địa chỉ."""
    return normalize(text)


def normalize_text(text: str) -> NormalizedText:
    """Alias của normalize(), cho caller dùng tên chung."""
    return normalize(text)


class VietnameseNormalizer:
    """Lớp bao cho normalize(), để caller theo kiểu OOP dùng được."""

    def normalize(self, text: str) -> NormalizedText:
        return normalize(text)

    def __call__(self, text: str) -> NormalizedText:
        return normalize(text)
