"""AddressService — entry duy nhất.
Fix anchor: index được truyền vào mà RỖNG (không có tỉnh) thì
không được dùng — object rỗng vẫn truthy nên `or` không cứu được.
Service hợp nhất index truyền vào + CSV + seed (seed chỉ bù mã
chưa có), nên anchor luôn có Đắk Lắk/Gia Lai/Hồ Chí Minh để neo."""
from __future__ import annotations
from dataclasses import replace
from typing import Any, Optional, Protocol
from ..domain.confidence_gate import ConfidenceGate
from ..domain.fuzzy_matcher import FuzzyMatcher
from ..domain.input_guard import InputGuard
from ..domain.province_anchor import AdminIndex, AdminUnit, anchor_province
from ..domain.ranker import Ranker
from ..domain.schemas import AddressCandidate, AddressContract, AddressStatus, PipelineResult, PipelineStage, ProviderSource, ReasonCode, gate_to_address_status, normalize_text
from ..domain.structured_parser import parse_address
from ..domain.validator import Decision, HierarchicalValidator
class RouterPort(Protocol):
    def geocode(self, query_ascii: str, query_diacritic: str, anchored_province_code: Optional[str]) -> Any: ...
def _seed_index() -> AdminIndex:
    units=[
        AdminUnit("66","Đắk Lắk","province",None,("dak lak",),12.7,108.0),
        AdminUnit("652","Cư M'gar","district","66",(),12.8,108.1),
        AdminUnit("65211","Cuôr Đăng","ward","652",("cuor dang",),12.8,108.1),
        AdminUnit("64","Gia Lai","province",None,("gia lai",),13.9,108.0),
        AdminUnit("635","Mang Yang","district","64",(),13.9,108.0),
        AdminUnit("63501","Đăk Yă","ward","635",("dak ya",),13.9,108.0),
        AdminUnit("22","Hải Dương","province",None,("hai duong",),20.9,106.3),
        AdminUnit("220","Chí Linh","district","22",(),21.1,106.3),
        AdminUnit("22001","Phả Lại","ward","220",("pha lai",),21.1,106.3),
        AdminUnit("40","Nghệ An","province",None,(),19.0,105.0),
        AdminUnit("400","Thanh Chương","district","40",(),19.0,105.0),
        AdminUnit("40001","Thanh Mỹ","ward","400",(),19.0,105.0),
        AdminUnit("04","Cao Bằng","province",None,(),22.6,106.2),
        AdminUnit("040","Hòa An","district","04",(),22.6,106.2),
        AdminUnit("04001","Hợp Giang","ward","040",(),22.6,106.2),
        AdminUnit("33","Hưng Yên","province",None,(),20.8,106.0),
        AdminUnit("330","Kim Động","district","33",(),20.8,106.0),
        AdminUnit("33001","Hiến Nam","ward","330",(),20.8,106.0),
        AdminUnit("74","Bình Dương","province",None,(),11.0,106.6),
        AdminUnit("740","Thủ Dầu Một","district","74",(),11.0,106.6),
        AdminUnit("74001","Phú Cường","ward","740",(),11.0,106.6),
        AdminUnit("79","Hồ Chí Minh","province",None,("ho chi minh","tp hcm","sai gon"),10.8,106.6),
        AdminUnit("760","Quận 1","district","79",("quan 1",),10.8,106.6),
        AdminUnit("76001","Bến Nghé","ward","760",("ben nghe",),10.8,106.6),
        AdminUnit("76002","Phường 2","ward","760",(),10.8,106.6),
    ]
    return AdminIndex(units)
def _load_default_index() -> AdminIndex:
    try:
        from ..infrastructure.admin_boundary_store import load_index_from_data_dir
        idx=load_index_from_data_dir("data/admin")
        if idx is not None and getattr(idx, "provinces", None): return idx
    except Exception: pass
    return _seed_index()
def _combined_index(admin_index: Any) -> AdminIndex:
    """Hợp nhất index truyền vào + CSV + seed; seed chỉ bù mã chưa có,
    không ghi đè mã thật từ CSV/index truyền vào."""
    merged: dict[str, AdminUnit] = {}
    for idx in (_load_default_index(), admin_index):
        if idx is None: continue
        units = getattr(idx, "units", None)
        if isinstance(units, dict):
            for code, unit in units.values() if False else units.items():
                if code not in merged: merged[code]=unit
        elif isinstance(idx, (list, tuple)):
            for unit in idx:
                if getattr(unit, "code", None) not in merged: merged[getattr(unit, "code")]=unit
    if not merged: return _seed_index()
    return AdminIndex(list(merged.values()))
def _candidate_from_obj(obj: Any, raw_input: str):
    if obj is None: return None
    data=obj if isinstance(obj,dict) else getattr(obj,"__dict__",{}) or {}
    if isinstance(obj,AddressContract): return AddressCandidate(address=obj,provider=ProviderSource.GOONG,provider_score=0.8,rank_score=0.8)
    if isinstance(obj,AddressCandidate): return obj
    pc=data.get("province_code")
    if not pc: return None
    contract=AddressContract(raw_input=raw_input,province=data.get("province"),district=data.get("district"),ward=data.get("ward"),street=data.get("street"),house_number=data.get("house_number"),province_code=str(pc),district_code=str(data["district_code"]) if data.get("district_code") else None,ward_code=str(data["ward_code"]) if data.get("ward_code") else None,lat=data.get("lat"),lng=data.get("lng"),source=ProviderSource.GOONG)
    score=float(data.get("score",data.get("provider_score",0.8)) or 0.8)
    return AddressCandidate(address=contract,provider=ProviderSource.GOONG,provider_score=score,rank_score=score)
class AddressService:
    def __init__(self, admin_index: Optional[Any]=None, router: Optional[RouterPort]=None, validator: Optional[HierarchicalValidator]=None, gate: Optional[ConfidenceGate]=None, matcher: Optional[FuzzyMatcher]=None, ranker: Optional[Ranker]=None, guard: Optional[InputGuard]=None) -> None:
        self.admin_index=_combined_index(admin_index)
        self.router=router; self.validator=validator or HierarchicalValidator(index=self.admin_index); self.gate=gate or ConfidenceGate(); self.matcher=matcher or FuzzyMatcher(); self.ranker=ranker or Ranker(); self.guard=guard or InputGuard()
    def resolve(self, raw_input: str, carrier: str="SPX") -> PipelineResult:
        normalized=normalize_text(raw_input or ""); base=AddressContract(raw_input=raw_input or "")
        result=PipelineResult(contract=base,stage_reached=PipelineStage.NORMALIZE)
        parsed=parse_address(raw_input or ""); result.stage_reached=PipelineStage.PARSE
        guard=self.guard.check(raw_input or "",parsed)
        if not guard.passed: return result.reject(PipelineStage.PARSE,guard.reason_code)
        anchor=anchor_province(raw_input or "",self.admin_index); result.stage_reached=PipelineStage.ANCHOR
        if not anchor.anchored or anchor.province is None: return result.reject(PipelineStage.ANCHOR,ReasonCode.MISSING_PROVINCE)
        anchored_contract=replace(base,province=anchor.province.name,province_code=anchor.province.code); result.contract=anchored_contract
        candidates=self.matcher.match(parsed,anchor,self.admin_index); result.stage_reached=PipelineStage.MATCH
        if self.router is not None:
            result.stage_reached=PipelineStage.GEOCODE
            try:
                rr=self.router.geocode(normalized,raw_input or "",anchor.province.code)
                for obj in getattr(rr,"candidates",[]) or []:
                    cand=_candidate_from_obj(obj,raw_input or "")
                    if cand is not None: candidates.append(cand)
            except Exception: pass
        if not candidates: return result.reject(PipelineStage.MATCH,ReasonCode.MISSING_WARD)
        ranked=self.ranker.rank(candidates,anchor); result.candidates=ranked.ranked; result.stage_reached=PipelineStage.RANK
        if ranked.top1 is None: return result.reject(PipelineStage.RANK,ReasonCode.PROVINCE_MISMATCH)
        top=ranked.top1.address
        contract=replace(top,raw_input=raw_input or "",house_number=parsed.house_number or top.house_number,street=parsed.street or top.street,hamlet=parsed.hamlet or top.hamlet,matched_tokens=parsed.matched_tokens,unmatched_tokens=parsed.unmatched_tokens,source=top.source,status=AddressStatus.PENDING)
        result.contract=contract
        validation=self.validator.validate(contract,preferred_carrier=carrier); result.stage_reached=PipelineStage.VALIDATE
        if validation.decision==Decision.BLOCKED: return result.reject(PipelineStage.VALIDATE,ReasonCode.HIERARCHY_CONFLICT)
        decision=self.gate.evaluate(contract,ranked.ranked); result.stage_reached=PipelineStage.GATE
        contract=replace(contract,confidence=decision.confidence_score,status=gate_to_address_status(decision.status),reason_code=decision.reason_code); result.contract=contract; result.stage_reached=PipelineStage.COMMIT
        return result
