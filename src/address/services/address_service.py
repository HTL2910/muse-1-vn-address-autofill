"""AddressService — entry point duy nhất, approach_12 + fix_missing_pipeline_core.

Thứ tự bắt buộc, không được gọi tắt:
  Normalize -> InputGuard -> Parse -> Anchor -> Match -> Geocode
  -> Rank -> Validate -> Gate -> Commit / NEEDS_REVIEW / REJECTED
Adapter và API không được gọi thẳng provider (Goong/Pancake/SPX).
Router là gợi ý, Canonical Admin DB là nguồn sự thật.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Optional, Protocol

from ..domain.confidence_gate import ConfidenceGate
from ..domain.fuzzy_matcher import FuzzyMatcher
from ..domain.input_guard import InputGuard
from ..domain.province_anchor import AdminIndex, anchor_province
from ..domain.ranker import Ranker
from ..domain.schemas import (AddressCandidate, AddressContract, AddressStatus,
                              GateStatus, PipelineResult, PipelineStage,
                              ProviderSource, ReasonCode, gate_to_address_status,
                              normalize_text)
from ..domain.structured_parser import parse_address
from ..domain.validator import Decision, HierarchicalValidator


class RouterPort(Protocol):
    def geocode(self, query_ascii: str, query_diacritic: str,
                anchored_province_code: Optional[str]) -> Any: ...


def _candidate_from_obj(obj: Any, raw_input: str) -> Optional[AddressCandidate]:
    if obj is None:
        return None
    data = obj if isinstance(obj, dict) else getattr(obj, "__dict__", {}) or {}
    if isinstance(obj, AddressContract):
        return AddressCandidate(address=obj, provider=ProviderSource.GOONG,
                                provider_score=0.8, rank_score=0.8)
    if isinstance(obj, AddressCandidate):
        return obj
    province_code = data.get("province_code")
    if not province_code:
        return None
    contract = AddressContract(
        raw_input=raw_input, province=data.get("province"), district=data.get("district"),
        ward=data.get("ward"), street=data.get("street"), house_number=data.get("house_number"),
        province_code=str(province_code), district_code=str(data["district_code"]) if data.get("district_code") else None,
        ward_code=str(data["ward_code"]) if data.get("ward_code") else None,
        lat=data.get("lat"), lng=data.get("lng"), source=ProviderSource.GOONG)
    score = float(data.get("score", data.get("provider_score", 0.8)) or 0.8)
    return AddressCandidate(address=contract, provider=ProviderSource.GOONG,
                            provider_score=score, rank_score=score)


@dataclass
class AddressService:
    admin_index: AdminIndex
    router: Optional[RouterPort] = None
    validator: Optional[HierarchicalValidator] = None
    gate: Optional[ConfidenceGate] = None
    matcher: Optional[FuzzyMatcher] = None
    ranker: Optional[Ranker] = None
    guard: Optional[InputGuard] = None

    def __post_init__(self) -> None:
        self.validator = self.validator or HierarchicalValidator(index=self.admin_index)
        self.gate = self.gate or ConfidenceGate()
        self.matcher = self.matcher or FuzzyMatcher()
        self.ranker = self.ranker or Ranker()
        self.guard = self.guard or InputGuard()

    def resolve(self, raw_input: str, carrier: str = "SPX") -> PipelineResult:
        # 1. Normalize (không đoán, chỉ chuẩn hóa để so khớp)
        normalized = normalize_text(raw_input or "")
        base = AddressContract(raw_input=raw_input or "")
        result = PipelineResult(contract=base, stage_reached=PipelineStage.NORMALIZE)
        # 2. Parse trước guard để guard đếm cấp từ đoạn đã phân loại.
        parsed = parse_address(raw_input or "")
        result.stage_reached = PipelineStage.PARSE
        # 3. InputGuard — chặn trước mọi provider call.
        guard = self.guard.check(raw_input or "", parsed)
        if not guard.passed:
            return result.reject(PipelineStage.PARSE, guard.reason_code)
        # 4. Anchor tỉnh.
        anchor = anchor_province(raw_input or "", self.admin_index)
        result.stage_reached = PipelineStage.ANCHOR
        if not anchor.anchored or anchor.province is None:
            return result.reject(PipelineStage.ANCHOR, ReasonCode.MISSING_PROVINCE)
        # 5. Match trong tỉnh đã khóa (Canonical DB).
        candidates = self.matcher.match(parsed, anchor, self.admin_index)
        result.stage_reached = PipelineStage.MATCH
        # 6. Geocode — router chỉ là gợi ý, luôn qua veto tỉnh ở Ranker.
        if self.router is not None:
            result.stage_reached = PipelineStage.GEOCODE
            try:
                router_result = self.router.geocode(normalized, raw_input or "", anchor.province.code)
                for obj in getattr(router_result, "candidates", []) or []:
                    cand = _candidate_from_obj(obj, raw_input or "")
                    if cand is not None:
                        candidates.append(cand)
            except Exception:
                # Provider lỗi thì fail sang matcher nội bộ, không fail-open.
                pass
        if not candidates:
            return result.reject(PipelineStage.MATCH, ReasonCode.MISSING_WARD)
        # 7. Rank — veto lệch tỉnh, tính margin.
        ranked = self.ranker.rank(candidates, anchor)
        result.candidates = ranked.ranked
        result.stage_reached = PipelineStage.RANK
        if ranked.top1 is None:
            return result.reject(PipelineStage.RANK, ReasonCode.PROVINCE_MISMATCH)
        top = ranked.top1.address
        # Điền chi tiết từ parser (số nhà/đường/thôn) lên ứng viên top-1;
        # hành chính lấy từ candidate, không lấy từ provider thô.
        contract = replace(
            top, raw_input=raw_input or "",
            house_number=parsed.house_number or top.house_number,
            street=parsed.street or top.street, hamlet=parsed.hamlet or top.hamlet,
            matched_tokens=parsed.matched_tokens, unmatched_tokens=parsed.unmatched_tokens,
            source=top.source, status=AddressStatus.PENDING)
        result.contract = contract
        # 8. Validate phân cấp + hãng + GPS.
        validation = self.validator.validate(contract, preferred_carrier=carrier)
        result.stage_reached = PipelineStage.VALIDATE
        if validation.decision == Decision.BLOCKED:
            return result.reject(PipelineStage.VALIDATE, ReasonCode.HIERARCHY_CONFLICT)
        # 9. Confidence Gate — chốt chặn cuối.
        decision = self.gate.evaluate(contract, ranked.ranked)
        result.stage_reached = PipelineStage.GATE
        contract = replace(contract, confidence=decision.confidence_score,
                           status=gate_to_address_status(decision.status),
                           reason_code=decision.reason_code)
        result.contract = contract
        # 10. Commit — chỉ VERIFIED mới shippable; NEEDS_REVIEW/REJECTED không tạo nhãn.
        result.stage_reached = PipelineStage.COMMIT
        return result
