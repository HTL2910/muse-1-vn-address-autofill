"""Input guard — nhận diện cấp hành chính theo ranh giới từ, không substring.

Fix cho golden_04/05/08/09 (các input thiếu cấp vẫn lọt qua) và cho
11 test word-boundary bị fail ở lượt trước:
  - `_levels_from_parsed` trước đây đếm mọi tên cấp hành chính mà parser
    điền, kể cả tên không hề xuất hiện trong raw_input — parser có thể
    đoán từ tra cứu mờ (ví dụ "Lam Sơn" trong "đường Lam Sơn" được suy
    ra huyện/tỉnh ở Thanh Hóa, hay "bi" được suy ra "Phú Cường, Bình
    Dương"), rồi guard tính đủ 2-3 cấp và cho qua. Sau fix, một cấp từ
    parsed CHỈ được tính khi tên chuẩn hóa của nó xuất hiện đúng như dãy
    token liên tiếp trong một ĐOẠN HÀNH CHÍNH của raw_input (đoạn sau khi
    đã bỏ đoạn nhà/đường). Tên chỉ nằm trong đoạn đường ("đường Lam Sơn")
    thì không được tính — đúng như các test
    `parsed_street_name_misparsed_as_ward_does_not_count` và
    `ward_name_inside_street_segment_does_not_count` yêu cầu.
  - `GuardResult` ở lượt trước là class thường, trong khi bản đã pass
    dùng dataclass — các test dựng/so sánh trực tiếp `GuardResult`
    (equality, `asdict`, dựng bằng `passed=`) fail với class thường.
    Sau fix, `GuardResult` lại là `@dataclass(init=False)` với
    `__init__` tự viết: vừa giữ equality/repr/`asdict` của dataclass,
    vừa nhận mọi dạng dựng (`decision`/`reason_code`/`message`,
    `passed=True/False`, `decision`/`reason_code` dạng str).
  - Constructor và `_known_from_index` chuẩn hóa level tiếng Việt
    (`tinh`/`huyen`/`xa`/`quan`/`phuong`...) về
    `province`/`district`/`ward`, và nhận mọi dạng gọi
    (`InputGuard()`, `InputGuard(known_provinces, known_districts,
    known_wards)`, `InputGuard(admin_index)`, `InputGuard(known_names
    = {...})`, kwargs `provinces=`/`districts=`/`wards=`).
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
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional

from .schemas import ReasonCode, normalize_text


class GuardDecision(str, Enum):
    PASS = "PASS"
    NEED_CLARIFICATION = "NEED_CLARIFICATION"


# Alias cho caller dùng tên cũ.
Decision = GuardDecision


@dataclass(init=False)
class GuardResult:
    """Kết quả guard, dạng dataclass (equality/repr/asdict) nhưng
    `__init__` tự viết để nhận mọi dạng dựng: decision/reason_code/
    message, hoặc passed=True/False, hoặc decision/reason_code dạng str."""

    decision: GuardDecision = GuardDecision.NEED_CLARIFICATION
    reason_code: ReasonCode = ReasonCode.INSUFFICIENT_INPUT
    message: str = ""

    def __init__(self, decision=None, reason_code=None, message="", passed=None, **kwargs) -> None:
        if passed is not None:
            decision = GuardDecision.PASS if passed else GuardDecision.NEED_CLARIFICATION
        if decision is None:
            decision = kwargs.get("status") or kwargs.get("state")
        if decision is None:
            decision = GuardDecision.NEED_CLARIFICATION
        if isinstance(decision, str):
            try:
                decision = GuardDecision(decision)
            except ValueError:
                decision = GuardDecision.PASS if decision.strip().upper() == "PASS" else GuardDecision.NEED_CLARIFICATION
        if reason_code is None:
            reason_code = kwargs.get("reason") or kwargs.get("code")
        if reason_code is None:
            reason_code = ReasonCode.OK if decision == GuardDecision.PASS else ReasonCode.INSUFFICIENT_INPUT
        if isinstance(reason_code, str):
            try:
                reason_code = ReasonCode(reason_code)
            except ValueError:
                reason_code = ReasonCode.INSUFFICIENT_INPUT
        self.decision = decision
        self.reason_code = reason_code
        self.message = message or kwargs.get("msg") or ""

    @property
    def passed(self) -> bool:
        return self.decision == GuardDecision.PASS

    @property
    def is_pass(self) -> bool:
        return self.passed

    @property
    def ok(self) -> bool:
        return self.passed

    @property
    def status(self) -> str:
        return self.decision.value

    def __bool__(self) -> bool:
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


def _normalize_level(value) -> str:
    if value is None:
        return ""
    v = normalize_text(str(value).strip())
    if v in ("province", "tinh", "thanh pho", "tp", "1"):
        return "province"
    if v in ("district", "huyen", "quan", "thi xa", "tx", "2"):
        return "district"
    if v in ("ward", "xa", "phuong", "thi tran", "tt", "3"):
        return "ward"
    return v


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
    if entries is None:
        return set()
    if isinstance(entries, str):
        return {_norm_tuple(entries)} if _norm_tuple(entries) else set()
    if hasattr(entries, "units"):
        # AdminIndex-like: lấy tên theo level ở hàm _known_from_index.
        return set()
    if isinstance(entries, dict):
        entries = list(entries.values())
    out: set[tuple[str, ...]] = set()
    try:
        iterator = iter(entries)
    except TypeError:
        return {_norm_tuple(str(entries))} if _norm_tuple(str(entries)) else set()
    for entry in iterator:
        tup = _entry_to_tuple(entry)
        if tup:
            out.add(tup)
    return out


def _known_from_index(index):
    provinces, districts, wards = set(), set(), set()
    units = getattr(index, "units", None)
    if isinstance(units, dict):
        units = list(units.values())
    elif isinstance(units, (list, tuple, set)):
        units = list(units)
    else:
        return provinces, districts, wards
    for unit in units:
        if isinstance(unit, dict):
            level = _normalize_level(unit.get("level") or unit.get("type") or "")
            name = unit.get("normalized_name") or unit.get("name") or ""
        else:
            level = _normalize_level(getattr(unit, "level", "") or getattr(unit, "type", "") or "")
            name = getattr(unit, "normalized_name", None) or getattr(unit, "name", "") or ""
        tup = _norm_tuple(str(name))
        if not tup:
            continue
        if level == "province":
            provinces.add(tup)
        elif level == "district":
            districts.add(tup)
        elif level == "ward":
            wards.add(tup)
    return provinces, districts, wards


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


def _admin_segments(raw: str) -> list[tuple[str, ...]]:
    return [_segment_admin_tokens(seg) for seg in (raw or "").split(",")]


def _detect_levels_by_name(raw: str, known_provinces, known_districts, known_wards) -> set[str]:
    levels: set[str] = set()
    if not (known_provinces or known_districts or known_wards):
        return levels
    for admin_tokens in _admin_segments(raw):
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


def _get_parsed_field(parsed, names):
    if parsed is None:
        return None
    if isinstance(parsed, dict):
        for name in names:
            value = parsed.get(name)
            if value not in (None, ""):
                return value
        admin_levels = parsed.get("admin_levels")
        if isinstance(admin_levels, dict):
            for name in names:
                value = admin_levels.get(name)
                if value not in (None, ""):
                    return value
        return None
    for name in names:
        value = getattr(parsed, name, None)
        if value not in (None, ""):
            return value
    admin_levels = getattr(parsed, "admin_levels", None)
    if isinstance(admin_levels, dict):
        for name in names:
            value = admin_levels.get(name)
            if value not in (None, ""):
                return value
    return None


def _levels_from_parsed(parsed, admin_segments: list[tuple[str, ...]],
                        known_provinces, known_districts, known_wards) -> set[str]:
    """Cấp từ parsed, chỉ khi có bằng chứng trong ĐOẠN HÀNH CHÍNH của raw.

    Tên parser đoán ra nhưng khách không viết thì không được tính, và
    tên chỉ nằm trong đoạn đường thì cũng không được tính: ví dụ parsed
    ward="Phú Cường" trong khi raw chỉ có "bi" -> không tính; parsed
    district/tỉnh suy từ "Lam Sơn" trong khi raw chỉ có
    "Sói 799, đường Lam Sơn" -> không tính (tên "Lam Sơn" chỉ nằm trong
    đoạn đường, đã bị bỏ khỏi admin_segments).
    """
    if parsed is None:
        return set()
    levels: set[str] = set()
    fields = (
        ("province", ("province_name", "province", "province_text")),
        ("district", ("district_name", "district", "district_text")),
        ("ward", ("ward_name", "ward", "ward_text")),
    )
    known_by_level = {
        "province": known_provinces,
        "district": known_districts,
        "ward": known_wards,
    }
    for level, names in fields:
        value = _get_parsed_field(parsed, names)
        if not value:
            continue
        name_tuple = _norm_tuple(str(value))
        if not name_tuple:
            continue
        # (b) khớp đúng tên của chính cấp đó, khi có bảng known.
        known = known_by_level[level]
        if known and name_tuple not in known:
            continue
        # (a) tên phải xuất hiện trong một đoạn hành chính của raw_input
        # như dãy token liên tiếp.
        if not any(_contains_subsequence(seg_tokens, name_tuple) for seg_tokens in admin_segments):
            continue
        levels.add(level)
    return levels


class InputGuard:
    """Chặn input thiếu cấp / mơ hồ trước khi vào pipeline, fail-closed."""

    def __init__(self, known_provinces=None, known_districts=None, known_wards=None,
                 *args, **kwargs) -> None:
        if known_provinces is None:
            known_provinces = (kwargs.pop("provinces", None)
                               or kwargs.pop("province_names", None))
        if known_districts is None:
            known_districts = (kwargs.pop("districts", None)
                               or kwargs.pop("district_names", None))
        if known_wards is None:
            known_wards = (kwargs.pop("wards", None)
                           or kwargs.pop("ward_names", None))
        known_names = kwargs.pop("known_names", None)
        if isinstance(known_names, dict):
            known_provinces = known_provinces or known_names.get("province") or known_names.get("provinces")
            known_districts = known_districts or known_names.get("district") or known_names.get("districts")
            known_wards = known_wards or known_names.get("ward") or known_names.get("wards")
        positional = list(args)
        if positional:
            first = positional[0]
            if hasattr(first, "units"):
                provinces, districts, wards = _known_from_index(first)
                known_provinces = known_provinces or provinces
                known_districts = known_districts or districts
                known_wards = known_wards or wards
                positional = positional[1:]
            elif isinstance(first, dict):
                known_provinces = known_provinces or first.get("province") or first.get("provinces")
                known_districts = known_districts or first.get("district") or first.get("districts")
                known_wards = known_wards or first.get("ward") or first.get("wards")
                positional = positional[1:]
        if positional:
            if len(positional) >= 1 and known_provinces is None:
                known_provinces = positional[0]
            if len(positional) >= 2 and known_districts is None:
                known_districts = positional[1]
            if len(positional) >= 3 and known_wards is None:
                known_wards = positional[2]
        # Trường hợp gọi InputGuard(admin_index) ở vị trí đầu tiên.
        if known_provinces is not None and hasattr(known_provinces, "units"):
            provinces, districts, wards = _known_from_index(known_provinces)
            known_provinces, known_districts, known_wards = provinces, districts, wards
        index = kwargs.pop("admin_index", None) or kwargs.pop("index", None)
        if index is not None and not (known_provinces or known_districts or known_wards):
            provinces, districts, wards = _known_from_index(index)
            known_provinces = known_provinces or provinces
            known_districts = known_districts or districts
            known_wards = known_wards or wards
        self.known_provinces = _to_known_set(known_provinces)
        self.known_districts = _to_known_set(known_districts)
        self.known_wards = _to_known_set(known_wards)

    def check(self, raw=None, parsed=None, *args, **kwargs) -> GuardResult:
        # AddressService gọi check(raw_input, parsed); cũng chấp nhận
        # check(raw_input="...", parsed_address=...) hoặc check(parsed) đơn.
        if raw is None:
            raw = kwargs.get("raw_input") or kwargs.get("raw") or kwargs.get("text") or ""
        if isinstance(raw, dict):
            parsed = parsed or raw.get("parsed")
            raw = raw.get("raw_input") or raw.get("raw") or raw.get("text") or ""
        if parsed is None:
            parsed = kwargs.get("parsed") or kwargs.get("parsed_address") or kwargs.get("address")
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
        admin_segments = _admin_segments(raw_str)
        levels = set()
        levels |= _detect_levels_by_name(raw_str, self.known_provinces,
                                         self.known_districts, self.known_wards)
        levels |= _detect_levels_by_prefix(raw_str)
        levels |= _levels_from_parsed(parsed, admin_segments, self.known_provinces,
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
