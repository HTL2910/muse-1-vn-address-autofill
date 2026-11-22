"""Gate fail-closed. Cấm default CHỈ khi trần không detail; địa chỉ
đầy đủ chứa Đăk Yă + số nhà/đường là khách ghi thật. Không bonus
exact-admin làm địa chỉ trần VERIFIED."""
from __future__ import annotations
import math, re
from dataclasses import dataclass, field
from typing import Optional
from .schemas import AddressCandidate, AddressContract, GateStatus, ProviderSource, ReasonCode, is_forbidden_default, normalize_text
NUMBER_ONLY_RE=re.compile(r"^\d{1,6}$")
MEANINGFUL_TOKEN_RE=re.compile(r"[a-z]{3,}")
GENERIC_TOKENS={"xom","bi","soi","nha","duong","so","ngo"}
@dataclass
class GateThresholds:
    auto_approve: float=0.85; confirm_min: float=0.60
@dataclass
class GateFeatures:
    coverage: float=0.0; province_match: float=0.0; district_match: float=0.0; ward_match: float=0.0; provider_agreement: float=0.0; margin_top1_top2: float=0.0; has_house_or_street: float=0.0; hierarchy_conflict: float=0.0
@dataclass
class GateDecision:
    status: GateStatus; confidence_score: float; raw_score: float; is_calibrated: bool=False; matched_tokens: list[str]=field(default_factory=list); unmatched_tokens: list[str]=field(default_factory=list); source: ProviderSource=ProviderSource.INTERNAL_ADMIN_DB; reason_code: ReasonCode=ReasonCode.OK; reason_detail: str=""; features: Optional[GateFeatures]=None
    def to_dict(self) -> dict: return {"status":self.status.value,"confidence_score":round(self.confidence_score,4),"raw_score":round(self.raw_score,4),"is_calibrated":self.is_calibrated,"matched_tokens":self.matched_tokens,"unmatched_tokens":self.unmatched_tokens,"source":self.source.value,"reason_code":self.reason_code.value,"reason_detail":self.reason_detail}
class PlattCalibrator:
    def __init__(self) -> None: self.a=-4.0; self.b=2.0; self.fitted=False
    def fit(self, raw_scores, labels, steps=500, lr=0.1):
        if len(raw_scores)!=len(labels) or len(raw_scores)<10: raise ValueError("Need >=10 labelled samples")
        a,b=self.a,self.b
        for _ in range(steps):
            ga=gb=0.0
            for x,y in zip(raw_scores,labels):
                z=a*x+b; p=1.0/(1.0+math.exp(-z)) if z>=0 else math.exp(z)/(1.0+math.exp(z)); err=p-y; ga+=err*x; gb+=err
            a-=lr*ga/len(raw_scores); b-=lr*gb/len(raw_scores)
        self.a,self.b,self.fitted=a,b,True; return self
    def calibrate(self, raw: float) -> float:
        z=self.a*raw+self.b
        return 1.0/(1.0+math.exp(-z)) if z>=0 else math.exp(z)/(1.0+math.exp(z))
class ConfidenceGate:
    WEIGHTS={"coverage":0.25,"province_match":0.20,"district_match":0.15,"ward_match":0.15,"provider_agreement":0.10,"margin":0.10,"house_or_street":0.05}
    def __init__(self, thresholds: GateThresholds|None=None, calibrator: PlattCalibrator|None=None) -> None:
        self.thresholds=thresholds or GateThresholds(); self.calibrator=calibrator or PlattCalibrator()
    def hard_reject(self, contract: AddressContract) -> Optional[GateDecision]:
        raw=(contract.raw_input or "").strip(); norm=normalize_text(raw)
        if is_forbidden_default(raw) and not contract.has_house_or_street:
            return GateDecision(GateStatus.REJECTED_INSUFFICIENT,0.0,0.0,False,reason_code=ReasonCode.FORBIDDEN_DEFAULT,reason_detail="Bare forbidden-default address with no house/street detail.")
        if not raw or len(norm)<2 or not MEANINGFUL_TOKEN_RE.findall(norm): return GateDecision(GateStatus.REJECTED_INSUFFICIENT,0.0,0.0,False,reason_code=ReasonCode.INPUT_TOO_SHORT)
        if NUMBER_ONLY_RE.match(norm.replace(" ","")): return GateDecision(GateStatus.REJECTED_INSUFFICIENT,0.0,0.0,False,reason_code=ReasonCode.NUMBER_ONLY)
        tokens=[t for t in norm.split() if t not in GENERIC_TOKENS]
        if len(tokens)<=1 and not contract.is_hierarchy_complete: return GateDecision(GateStatus.REJECTED_INSUFFICIENT,0.0,0.0,False,reason_code=ReasonCode.GENERIC_TOKEN_ONLY)
        if not contract.province_code: return GateDecision(GateStatus.REJECTED_INSUFFICIENT,0.0,0.0,False,reason_code=ReasonCode.MISSING_PROVINCE)
        return None
    def evaluate(self, contract: AddressContract, candidates: Optional[list[AddressCandidate]]=None) -> GateDecision:
        candidates=sorted(candidates or [],key=lambda c:c.rank_score,reverse=True)
        hard=self.hard_reject(contract)
        if hard is not None:
            hard.matched_tokens=contract.matched_tokens; hard.unmatched_tokens=contract.unmatched_tokens; hard.source=contract.source; return hard
        top1=candidates[0].rank_score if candidates else 0.0; top2=candidates[1].rank_score if len(candidates)>1 else 0.0
        agreement=0.0
        if candidates:
            top=(candidates[0].address.province_code,candidates[0].address.district_code,candidates[0].address.ward_code)
            agreement=sum(1 for c in candidates if (c.address.province_code,c.address.district_code,c.address.ward_code)==top)/len(candidates)
        f=GateFeatures(coverage=contract.completeness,province_match=1.0 if contract.province_code else 0.0,district_match=1.0 if contract.district_code else 0.0,ward_match=1.0 if contract.ward_code else 0.0,provider_agreement=agreement,margin_top1_top2=max(0.0,top1-top2),has_house_or_street=1.0 if contract.has_house_or_street else 0.0,hierarchy_conflict=1.0 if contract.reason_code==ReasonCode.HIERARCHY_CONFLICT else 0.0)
        w=self.WEIGHTS; raw=w["coverage"]*f.coverage+w["province_match"]*f.province_match+w["district_match"]*f.district_match+w["ward_match"]*f.ward_match+w["provider_agreement"]*f.provider_agreement+w["margin"]*min(1.0,f.margin_top1_top2)+w["house_or_street"]*f.has_house_or_street
        if f.hierarchy_conflict: raw*=0.3
        raw=max(0.0,min(1.0,raw)); is_cal=bool(getattr(self.calibrator,"fitted",False)); cal=self.calibrator.calibrate(raw) if is_cal else raw; cal=max(0.0,min(1.0,cal))
        if len(candidates)>1 and f.margin_top1_top2<0.05: return GateDecision(GateStatus.NEED_CONFIRMATION,cal,raw,is_cal,contract.matched_tokens,contract.unmatched_tokens,contract.source,ReasonCode.LOW_MARGIN,"Top-1 and top-2 too close.",f)
        if cal>=self.thresholds.auto_approve-1e-9 and contract.is_hierarchy_complete: status,reason=GateStatus.VERIFIED,ReasonCode.OK
        elif cal>=self.thresholds.confirm_min-1e-9: status,reason=GateStatus.NEED_CONFIRMATION,ReasonCode.LOW_CONFIDENCE
        else: status,reason=GateStatus.REJECTED_INSUFFICIENT,ReasonCode.LOW_CONFIDENCE
        if status==GateStatus.VERIFIED and not contract.is_hierarchy_complete: status,reason=GateStatus.NEED_CONFIRMATION,ReasonCode.MISSING_WARD
        return GateDecision(status,cal,raw,is_cal,contract.matched_tokens,contract.unmatched_tokens,contract.source,reason,"",f)
