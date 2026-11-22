"""Input guard — nhận diện cấp hành chính theo ranh giới từ, không substring.

Fix cho golden_04/05/08/09 (các input thiếu cấp vẫn lọt qua):
`_levels_from_parsed` trước đây đếm mọi tên cấp hành chính mà parser
điền, kể cả tên không hề xuất hiện trong raw_input — parser có thể đoán
từ tra cứu mờ (ví dụ "Lam Sơn" trong "đường Lam Sơn" được suy ra huyện/
tỉnh ở Thanh Hóa, hay "bi" được suy ra "Phú Cường, Bình Dương"), rồi
guard tính đủ 2-3 cấp và cho qua. Sau fix, một cấp từ parsed CHỈ được
tính khi đồng thời:
  (a) tên chuẩn hóa của nó xuất hiện đúng như dãy token liên tiếp trong
      raw_input (bằng chứng trong văn bản khách viết, không phải suy đoán),
  (b) khi có bảng known thì tên đó phải khớp đúng tên của chính cấp đó
      (so sánh tuple token, không substring).
Còn lại giữ nguyên các quy tắc đã nộp: chỉ đếm cấp khi cụm từ hoàn chỉnh
nằm trong một đoạn (ngăn bởi dấu phẩy) sau khi đã bỏ cụm nhà/đường
("đường …", "ngõ …", "hẻm …", "số …"); "đường Đắk Lắk" không tính là
tỉnh; "dak lakx" không khớp "dak lak"; token đơn mơ hồ ("bi", "Xom")
và số-only ("73", "127") bị chặn trước khi đếm cấp.

API giữ nguyên cho AddressService: `check(raw_input, parsed)` trả về
`GuardResult` với `.passed` (True iff decision == PASS), `.is_pass`,
`.reason_code` là `ReasonCode` (không phải str, vì
`PipelineResult.reject` ghi nó vào `AddressContract.reason_code`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional

from .schemas import ReasonCode, normalize_text


class GuardDecision(str, Enum):
    PASS = "PASS"
    NEED_CLARIFICATION = "NEED_CLARIFICATION"


# Alias cho caller dùng tên cũ.
Decision = GuardDecision


@dataclass
class GuardResult:
    decision: GuardDecision
    reason_code: ReasonCode
    message: str = ""

    @property
    def passed(self) -> bool:
        return self.decision == GuardDecision.PASS

    @property
    def is_pass(self) -> bool:
        return self.passed

    @property
    def ok(self) -> bool:
        return self.passed


# Token đơn mơ hồ: đứng một mình thì không đủ bằng chứng cho bất kỳ cấp nào.
_AMBIGUOUS_TOKENS = frozenset({"bi", "xom"})

_STREET_PREFIXES = (
    "duong", "ngõ", "ngo", "hẻm", "hem",
)
_HOUSE_PREFIXES = ("số", "so",)

# Tiền tố hành chính -> cấp, khớp theo token hoàn chỉnh ở đầu đoạn.
_PREFIX_LEVELS: dict[tuple[str, ...], str] = {
    ("tinh",): "province",
    ("thanh", "pho"): "province",
    ("tp",): "province",
    ("huyen",): "district",
    ("quan",): "district",
    ("thi", "xa"): "district",
    ("xa",): "ward",
    ("phuong",): "ward",
    ("thi", "tran"): "ward",
}


def _norm_tuple(text: str) -> tuple[str, ...]:
    return tuple(normalize_text(text or "").split())


def _entry_to_tuple(entry) -> tuple[str, ...]:
    if entry is None:
        return ()
    if isinstance(entry, str):
        return _norm_tuple(entry)
    name = getattr(entry, "normalized_name", None) or getattr(entry, "name", None)
    if name:
        return _norm_tuple(str(name))
    return _norm_tuple(str(entry))


def _to_known_set(entries) -> set[tuple[str, ...]]:
    if not entries:
        return set()
    if isinstance(entries, dict):
        entries = list(entries.keys()) + list(entries.values())
    out: set[tuple[str, ...]] = set()
    for entry in entries:
        tup = _entry_to_tuple(entry)
        if tup:
            out.add(tup)
    return out


def _contains_subsequence(tokens: tuple[str, ...], sub: tuple[str, ...]) -> bool:
    if not sub or len(sub) > len(tokens):
        return False
    return any(tokens[i:i + len(sub)] == sub for i in range(len(tokens) - len(sub) + 1))


def _segment_admin_tokens(segment: str) -> tuple[str, ...]:
    """Token hành chính của một đoạn: bỏ toàn bộ đoạn nhà/đường.

    "đường Lam Sơn" -> () (đây là tên đường, không phải xã "Lam Sơn").
    "Số 12" -> () (đây là số nhà). Các đoạn còn lại giữ nguyên token.
    """
    tokens = tuple(normalize_text(segment or "").split())
    if not tokens:
        return ()
    if tokens[0] in _STREET_PREFIXES:
        return ()
    if tokens[0] in _HOUSE_PREFIXES:
        return ()
    if tokens[0].isdigit():
        # "127 Nguyễn Trãi": số nhà + tên đường, không có cấp hành chính.
        return ()
    return tokens


def _detect_levels_by_name(raw: str, known_provinces, known_districts, known_wards) -> set[str]:
    levels: set[str] = set()
    if not (known_provinces or known_districts or known_wards):
        return levels
    for segment in (raw or "").split(","):
        admin_tokens = _segment_admin_tokens(segment)
        if not admin_tokens:
            continue
        for level, known in (
            ("province", known_provinces),
            ("district", known_districts),
            ("ward", known_wards),
        ):
            if not known:
                continue
            for length in sorted({len(k) for k in known}, reverse=True):
                if length > len(admin_tokens):
                    continue
                if any(admin_tokens[i:i + length] in known
                       for i in range(len(admin_tokens) - length + 1)):
                    levels.add(level)
                    break
    return levels


def _detect_levels_by_prefix(raw: str) -> set[str]:
    levels: set[str] = set()
    for segment in (raw or "").split(","):
        tokens = tuple(normalize_text(segment or "").split())
        if not tokens:
            continue
        for prefix, level in _PREFIX_LEVELS.items():
            if tokens[:len(prefix)] == prefix:
                levels.add(level)
                break
    return levels


def _levels_from_parsed(parsed, raw_tokens: tuple[str, ...],
                        known_provinces, known_districts, known_wards) -> set[str]:
    """Cấp từ parsed, chỉ khi có bằng chứng trong raw_input.

    Tên parser đoán ra nhưng khách không viết thì không được tính:
    ví dụ parsed ward="Phú Cường" trong khi raw chỉ có "bi" -> không tính;
    parsed district/tỉnh suy từ "Lam Sơn" trong khi raw chỉ có
    "Sói 799, đường Lam Sơn" -> không tính.
    """
    if parsed is None:
        return set()
    levels: set[str] = set()
    fields = (
        ("province", ("province_name", "province")),
        ("district", ("district_name", "district")),
        ("ward", ("ward_name", "ward")),
    )
    known_by_level = {
        "province": known_provinces,
        "district": known_districts,
        "ward": known_wards,
    }
    for level, names in fields:
        value = None
        if isinstance(parsed, dict):
            for name in names:
                if parsed.get(name) not in (None, ""):
                    value = parsed.get(name)
                    break
        else:
            for name in names:
                value = getattr(parsed, name, None)
                if value not in (None, ""):
                    break
        if not value:
            continue
        name_tuple = _norm_tuple(str(value))
        if not name_tuple:
            continue
        # (b) khớp đúng tên của chính cấp đó, khi có bảng known.
        known = known_by_level[level]
        if known and name_tuple not in known:
            continue
        # (a) tên phải xuất hiện trong raw_input như dãy token liên tiếp.
        if not _contains_subsequence(raw_tokens, name_tuple):
            continue
        levels.add(level)
    return levels


class InputGuard:
    """Chặn input thiếu cấp / mơ hồ trước khi vào pipeline, fail-closed."""

    def __init__(self, known_provinces=None, known_districts=None,
                 known_wards=None, *args, **kwargs) -> None:
        # Chấp nhận cả cách gọi theo dict known_names / admin_index.
        if kwargs.get("known_names"):
            known = kwargs["known_names"] or {}
            known_provinces = known_provinces or known.get("province") or known.get("provinces")
            known_districts = known_districts or known.get("district") or known.get("districts")
            known_wards = known_wards or known.get("ward") or known.get("wards")
        index = kwargs.get("admin_index") or kwargs.get("index")
        if index is not None and not (known_provinces or known_districts or known_wards):
            units = getattr(index, "units", {}) or {}
            provinces, districts, wards = set(), set(), set()
            for unit in units.values():
                level = getattr(unit, "level", None)
                tup = _entry_to_tuple(unit)
                if not tup:
                    continue
                if level in ("province", "tinh"):
                    provinces.add(tup)
                elif level in ("district", "huyen", "quan"):
                    districts.add(tup)
                elif level in ("ward", "xa", "phuong"):
                    wards.add(tup)
            known_provinces, known_districts, known_wards = provinces, districts, wards
        self.known_provinces = _to_known_set(known_provinces)
        self.known_districts = _to_known_set(known_districts)
        self.known_wards = _to_known_set(known_wards)

    def check(self, raw=None, parsed=None, *args, **kwargs) -> GuardResult:
        # AddressService gọi check(raw_input, parsed); cũng chấp nhận
        # check(raw_input="...", parsed_address=...) hoặc check(parsed) đơn.
        if raw is None and kwargs.get("raw_input") is not None:
            raw = kwargs["raw_input"]
        if parsed is None and kwargs.get("parsed_address") is not None:
            parsed = kwargs["parsed_address"]
        if parsed is None and args:
            parsed = args[0]
        raw_str = raw if isinstance(raw, str) else (str(raw) if raw is not None else "")
        norm = normalize_text(raw_str)
        if not norm:
            return GuardResult(GuardDecision.NEED_CLARIFICATION, ReasonCode.INSUFFICIENT_INPUT,
                               "Input rỗng hoặc không đọc được.")
        if norm.replace(" ", "").isdigit():
            return GuardResult(GuardDecision.NEED_CLARIFICATION, ReasonCode.NUMBER_ONLY,
                               "Chỉ có số, không có cấp hành chính.")
        if norm in _AMBIGUOUS_TOKENS:
            return GuardResult(GuardDecision.NEED_CLARIFICATION, ReasonCode.GENERIC_TOKEN_ONLY,
                               "Token đơn mơ hồ, không đủ bằng chứng địa chỉ.")
        if len(norm) <= 3:
            return GuardResult(GuardDecision.NEED_CLARIFICATION, ReasonCode.INPUT_TOO_SHORT,
                               "Input quá ngắn.")
        raw_tokens = tuple(norm.split())
        levels = set()
        levels |= _detect_levels_by_name(raw_str, self.known_provinces,
                                         self.known_districts, self.known_wards)
        levels |= _detect_levels_by_prefix(raw_str)
        levels |= _levels_from_parsed(parsed, raw_tokens, self.known_provinces,
                                      self.known_districts, self.known_wards)
        if len(levels) < 2:
            if "province" not in levels:
                code = ReasonCode.MISSING_PROVINCE
            elif "district" not in levels:
                code = ReasonCode.MISSING_DISTRICT
            else:
                code = ReasonCode.MISSING_WARD
            return GuardResult(GuardDecision.NEED_CLARIFICATION, code,
                               f"Thiếu cấp hành chính, chỉ xác định được: {sorted(levels)}.")
        return GuardResult(GuardDecision.PASS, ReasonCode.OK, "Đủ cấp hành chính.")
