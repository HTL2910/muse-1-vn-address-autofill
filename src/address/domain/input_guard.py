"""
InputGuard — hard gate chặn input thiếu thông tin (approach_10), bản sửa
theo contract mà test suite yêu cầu:

- Constructor nhận inject tên chuẩn: InputGuard(known_provinces=[...],
  known_districts=[...], known_wards=[...]). Không inject vẫn chạy bằng
  fallback tiền tố hành chính có ranh giới từ.
- check(raw, parsed=None) -> GuardResult có .decision (GuardDecision),
  .passed, .reason_code, .levels. GuardResult so sánh bằng được với
  GuardDecision (== và .value) để caller cũ không vỡ.
- Đếm cấp hành chính CHỈ khi có bằng chứng trong raw:
  * tên chuẩn khớp ở ranh giới từ (không khớp substring/một phần từ),
  * tên nằm NGOÀI đoạn tên đường (đoạn chứa đường/phố/ngõ... bị loại),
  * hoặc tiền tố hành chính đứng đầu một đoạn ngăn bởi dấu phẩy.
- Trường parsed (parser đoán) chỉ được tính khi tên của nó xuất hiện thật
  trong raw ngoài đoạn tên đường — parser đoán xã cho input 'Xom' không
  tạo được bằng chứng.
- Quyết định: >= 2 cấp -> PASS; 1 cấp -> NEED_CLARIFICATION;
  0 cấp -> REJECTED. Input chỉ có số / quá ngắn -> REJECTED.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum


class GuardDecision(str, Enum):
    PASS = "PASS"
    NEED_CLARIFICATION = "NEED_CLARIFICATION"
    REJECTED = "REJECTED"


@dataclass
class GuardResult:
    decision: GuardDecision
    reason_code: str = "OK"
    levels: tuple[str, ...] = ()
    matched: dict = field(default_factory=dict)
    raw_input: str = ""

    @property
    def passed(self) -> bool:
        return self.decision == GuardDecision.PASS

    @property
    def value(self) -> str:
        return self.decision.value

    def __eq__(self, other):
        if isinstance(other, GuardDecision):
            return self.decision == other
        if isinstance(other, str):
            return self.decision.value == other
        if isinstance(other, GuardResult):
            return self.decision == other.decision
        return NotImplemented

    def __hash__(self):
        return hash(self.decision)

    def __bool__(self):
        return self.passed


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", (text or "")).strip().lower()


def _strip_prefix(name: str) -> str:
    return re.sub(
        r"^(thành phố|tỉnh|quận|huyện|thị xã|phường|xã|thị trấn)\s+", "",
        _norm(name)).strip()


_STREET_HINT = re.compile(
    r"\b(đường|phố|ngõ|ngách|hẻm|quốc lộ|tỉnh lộ|đại lộ|xa lộ)\b")
_NUMBER_ONLY = re.compile(r"^[\d\s./-]+$")

_PREFIX_LEVELS = (
    ("province", re.compile(r"^(tỉnh)\s+.+")),
    ("district", re.compile(r"^(quận|huyện|thị xã)\s+.+")),
    ("ward", re.compile(r"^(phường|xã|thị trấn)\s+.+")),
)


def _word_boundary_found(name_norm: str, segment_norm: str) -> bool:
    if not name_norm:
        return False
    pattern = r"(?<!\w)" + re.escape(name_norm) + r"(?!\w)"
    return re.search(pattern, segment_norm) is not None


def _parsed_field(parsed, field_name: str):
    """Lấy (value, raw_span) từ parsed dạng dict/object/FieldValue."""
    if parsed is None:
        return None, None
    value = None
    if isinstance(parsed, dict):
        value = parsed.get(field_name)
    else:
        value = getattr(parsed, field_name, None)
    if value is None:
        return None, None
    if isinstance(value, str):
        return value, None
    inner = getattr(value, "value", None)
    span = getattr(value, "raw_span", None) or getattr(value, "evidence", None)
    return inner, span


class InputGuard:
    def __init__(self, known_provinces=None, known_districts=None,
                 known_wards=None, **_ignored):
        self._known = {
            "province": [_norm(n) for n in (known_provinces or []) if n],
            "district": [_norm(n) for n in (known_districts or []) if n],
            "ward": [_norm(n) for n in (known_wards or []) if n],
        }
        # Biến thể bỏ tiền tố để khớp "Bến Nghé" khi raw ghi "Phường Bến Nghé"
        # và ngược lại.
        self._known_bare = {
            level: sorted({_strip_prefix(n) for n in names if _strip_prefix(n)})
            for level, names in self._known.items()
        }

    # -- phân đoạn ---------------------------------------------------------- #
    @staticmethod
    def _segments(raw_norm: str) -> list[str]:
        return [s.strip() for s in re.split(r"[,;]", raw_norm) if s.strip()]

    @staticmethod
    def _is_street_segment(segment_norm: str) -> bool:
        return bool(_STREET_HINT.search(segment_norm))

    # -- đếm cấp từ raw ------------------------------------------------------ #
    def _levels_from_raw(self, raw_norm: str) -> dict:
        found: dict[str, str] = {}
        segments = self._segments(raw_norm)
        plain_segments = [s for s in segments if not self._is_street_segment(s)]
        # 1) Tên chuẩn ở ranh giới từ, ngoài đoạn tên đường.
        for level in ("province", "district", "ward"):
            candidates = set(self._known[level]) | set(self._known_bare[level])
            for name in candidates:
                if any(_word_boundary_found(name, seg) for seg in plain_segments):
                    found[level] = name
                    break
        # 2) Fallback tiền tố hành chính ở đầu đoạn (khi không có tên chuẩn
        #    hoặc tên chưa khớp): "Phường X, Quận Y" vẫn tính 2 cấp.
        for seg in plain_segments:
            for level, pattern in _PREFIX_LEVELS:
                if level not in found and pattern.match(seg):
                    found[level] = seg
                    break
            # "thành phố <tên>": là huyện (TP thuộc tỉnh) nếu tên không nằm
            # trong danh sách tỉnh chuẩn; nếu có inject tỉnh và khớp thì tỉnh.
            if "district" not in found and seg.startswith("thành phố "):
                city_name = seg[len("thành phố "):].strip()
                if any(_word_boundary_found(n, city_name)
                       for n in self._known["province"]):
                    found.setdefault("province", seg)
                else:
                    found["district"] = seg
        return found

    # -- đếm cấp từ parsed, chỉ khi có bằng chứng raw ------------------------ #
    def _levels_from_parsed(self, parsed, raw_norm: str,
                            already: dict) -> dict:
        found = dict(already)
        segments = self._segments(raw_norm)
        plain_segments = [s for s in segments if not self._is_street_segment(s)]
        for level in ("province", "district", "ward"):
            if level in found:
                continue
            value, span = _parsed_field(parsed, level)
            if not value:
                continue
            value_norm = _norm(str(value))
            bare = _strip_prefix(value_norm)
            evidence_ok = False
            if span and _norm(str(span)) in raw_norm:
                evidence_ok = True
            elif any(_word_boundary_found(candidate, seg)
                     for candidate in {value_norm, bare} if candidate
                     for seg in plain_segments):
                evidence_ok = True
            if evidence_ok:
                found[level] = str(value)
        return found

    # -- API chính ------------------------------------------------------------ #
    def check(self, raw_input, parsed=None, **kwargs) -> GuardResult:
        if parsed is None and "parsed_address" in kwargs:
            parsed = kwargs["parsed_address"]
        raw = raw_input if isinstance(raw_input, str) else str(raw_input or "")
        raw_norm = _norm(raw)
        if not raw_norm:
            return GuardResult(GuardDecision.REJECTED, "INSUFFICIENT_INFO",
                               (), {}, raw)
        if _NUMBER_ONLY.match(raw_norm):
            return GuardResult(GuardDecision.REJECTED, "NUMBER_ONLY",
                               (), {}, raw)
        matched = self._levels_from_raw(raw_norm)
        if parsed is not None:
            matched = self._levels_from_parsed(parsed, raw_norm, matched)
        levels = tuple(
            level for level in ("province", "district", "ward") if level in matched)
        if len(levels) >= 2:
            return GuardResult(GuardDecision.PASS, "OK", levels, matched, raw)
        if len(levels) == 1:
            return GuardResult(GuardDecision.NEED_CLARIFICATION,
                               "MISSING_ADMIN_LEVELS", levels, matched, raw)
        tokens = [t for t in re.split(r"\s+", raw_norm) if t]
        reason = "TOO_SHORT" if len(tokens) <= 2 and len(raw_norm) < 12 \
            else "INSUFFICIENT_INFO"
        return GuardResult(GuardDecision.REJECTED, reason, (), matched, raw)

    # Alias cho caller dùng tên khác.
    def evaluate(self, raw_input, parsed=None, **kwargs) -> GuardResult:
        return self.check(raw_input, parsed=parsed, **kwargs)
