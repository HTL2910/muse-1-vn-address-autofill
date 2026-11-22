"""Input guard — hard gate trước mọi provider call, đếm cấp từ
ParsedAddress đã phân loại theo đoạn, không substring."""
from __future__ import annotations
import re
from dataclasses import dataclass
from .schemas import ReasonCode, normalize_text
from .structured_parser import ParsedAddress
NUMBER_ONLY_RE=re.compile(r"^\d{1,6}$")
MEANINGFUL_TOKEN_RE=re.compile(r"[a-z]{3,}")
GENERIC_TOKENS={"xom","bi","soi","nha","duong","so","ngo"}
@dataclass
class GuardDecision:
    passed: bool; reason_code: ReasonCode=ReasonCode.OK; detail: str=""
class InputGuard:
    def check(self, raw_input: str, parsed: ParsedAddress) -> GuardDecision:
        raw=(raw_input or "").strip(); norm=normalize_text(raw)
        if not raw or len(norm)<2: return GuardDecision(False,ReasonCode.INPUT_TOO_SHORT,"Input too short.")
        if not MEANINGFUL_TOKEN_RE.findall(norm): return GuardDecision(False,ReasonCode.INPUT_TOO_SHORT,"No meaningful token.")
        if NUMBER_ONLY_RE.match(norm.replace(" ","")): return GuardDecision(False,ReasonCode.NUMBER_ONLY,"Number-only input.")
        tokens=[t for t in norm.split() if t not in GENERIC_TOKENS]
        if len(tokens)<=1 and parsed.admin_level_count==0: return GuardDecision(False,ReasonCode.GENERIC_TOKEN_ONLY,"Single generic token.")
        if parsed.admin_level_count<2: return GuardDecision(False,ReasonCode.INSUFFICIENT_INPUT,f"Only {parsed.admin_level_count} admin level(s); need at least 2.")
        return GuardDecision(True,ReasonCode.OK,"")
