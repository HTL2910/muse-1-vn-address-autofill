"""Input guard — hard gate trước mọi provider call.
Đếm cấp bằng WORD BOUNDARY + bằng chứng raw: tên tỉnh/xã chỉ đếm
khi nó là cả một đoạn (có prefix hành chính, hoặc đoạn trần khớp
toàn bộ tên), không đếm khi chỉ nằm trong đoạn đường, và KHÔNG
fallback theo parser khi không có bằng chứng raw (parsed đoán
xã từ "Xom" hay từ tên đường thì vẫn reject).
API linh hoạt: check(raw, parsed=None), parsed có thể là
ParsedAddress/dict/object, kwarg parsed/parsed_address/address."""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any, Optional
from .schemas import ReasonCode, normalize_text
from .structured_parser import ParsedAddress, parse_address, _strip_prefix
NUMBER_ONLY_RE=re.compile(r"^\d{1,6}$")
MEANINGFUL_TOKEN_RE=re.compile(r"[a-z]{3,}")
GENERIC_TOKENS={"xom","bi","soi","nha","duong","so","ngo"}
_PROVINCE_PREFIXES={"tỉnh","thành phố","tp","tp."}
_DISTRICT_PREFIXES={"quận","huyện","thị trấn","tt","tt."}
_WARD_PREFIXES={"xã","phường"}
_STREET_PREFIXES={"đường","ngõ","hẻm"}
@dataclass
class GuardDecision:
    passed: bool; reason_code: ReasonCode=ReasonCode.OK; detail: str=""
    @property
    def is_valid(self) -> bool: return self.passed
    @property
    def valid(self) -> bool: return self.passed
    @property
    def ok(self) -> bool: return self.passed
    @property
    def reason(self) -> str: return self.reason_code.value
    @property
    def message(self) -> str: return self.detail or self.reason_code.value
    def __bool__(self) -> bool: return bool(self.passed)
def _as_parsed(raw_input: str, parsed: Any) -> ParsedAddress:
    if parsed is None: return parse_address(raw_input)
    if isinstance(parsed, ParsedAddress): return parsed
    if isinstance(parsed, dict):
        return ParsedAddress(raw_input=raw_input, normalized=normalize_text(raw_input),
            house_number=str(parsed.get("house_number") or ""), street=str(parsed.get("street") or parsed.get("street_name") or ""),
            hamlet=str(parsed.get("hamlet") or ""), ward_name=str(parsed.get("ward_name") or parsed.get("ward") or ""),
            district_name=str(parsed.get("district_name") or parsed.get("district") or ""), province_name=str(parsed.get("province_name") or parsed.get("province") or ""),
            province_source=str(parsed.get("province_source") or ""), district_source=str(parsed.get("district_source") or ""), ward_source=str(parsed.get("ward_source") or ""))
    return ParsedAddress(raw_input=raw_input, normalized=normalize_text(raw_input),
        house_number=str(getattr(parsed, "house_number", "") or ""), street=str(getattr(parsed, "street", "") or getattr(parsed, "street_name", "") or ""),
        ward_name=str(getattr(parsed, "ward_name", "") or getattr(parsed, "ward", "") or ""), district_name=str(getattr(parsed, "district_name", "") or getattr(parsed, "district", "") or ""),
        province_name=str(getattr(parsed, "province_name", "") or getattr(parsed, "province", "") or ""))
def _real_level_count(raw_input: str, parsed: ParsedAddress) -> int:
    segments=[s.strip() for s in (raw_input or "").split(",") if s.strip()]
    levels: set[str]=set()
    for seg in segments:
        prefix, rest=_strip_prefix(seg)
        if prefix in _PROVINCE_PREFIXES: levels.add("province")
        elif prefix in _DISTRICT_PREFIXES: levels.add("district")
        elif prefix in _WARD_PREFIXES: levels.add("ward")
        elif prefix in _STREET_PREFIXES: continue
        elif prefix in ("thôn","xóm"): continue
        norm_rest=normalize_text(rest)
        if not norm_rest or re.search(r"\d", norm_rest): continue
        for level, name in (("province", parsed.province_name), ("district", parsed.district_name), ("ward", parsed.ward_name)):
            if name and normalize_text(name)==norm_rest:
                levels.add(level)
    # Không fallback theo parser: nếu không có bằng chứng raw thì
    # cấp đó không được đếm, dù parser có đoán ra tên.
    return len(levels)
class InputGuard:
    def check(self, raw_input: str, parsed: Any = None, *args: Any, **kwargs: Any) -> GuardDecision:
        if parsed is None and args: parsed=args[0]
        if parsed is None: parsed=kwargs.get("parsed") or kwargs.get("parsed_address") or kwargs.get("address") or kwargs.get("parsed_result")
        parsed_obj=_as_parsed(raw_input or "", parsed)
        raw=(raw_input or "").strip(); norm=normalize_text(raw)
        if not raw or len(norm)<2: return GuardDecision(False,ReasonCode.INPUT_TOO_SHORT,"Input too short.")
        if not MEANINGFUL_TOKEN_RE.findall(norm): return GuardDecision(False,ReasonCode.INPUT_TOO_SHORT,"No meaningful token.")
        if NUMBER_ONLY_RE.match(norm.replace(" ","")): return GuardDecision(False,ReasonCode.NUMBER_ONLY,"Number-only input.")
        tokens=[t for t in norm.split() if t not in GENERIC_TOKENS]
        if len(tokens)<=1 and parsed_obj.admin_level_count==0: return GuardDecision(False,ReasonCode.GENERIC_TOKEN_ONLY,"Single generic token.")
        real=_real_level_count(raw, parsed_obj)
        if real<2: return GuardDecision(False,ReasonCode.INSUFFICIENT_INPUT,f"Only {real} real admin level(s) with word-boundary evidence; need at least 2.")
        return GuardDecision(True,ReasonCode.OK,"")
    def guard(self, raw_input: str, parsed: Any = None) -> GuardDecision: return self.check(raw_input, parsed)
    def validate(self, raw_input: str, parsed: Any = None) -> GuardDecision: return self.check(raw_input, parsed)
def check_input(raw_input: str, parsed: Any = None) -> GuardDecision:
    return InputGuard().check(raw_input, parsed)
guard_input = check_input
