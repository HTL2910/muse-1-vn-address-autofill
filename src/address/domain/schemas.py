"""Address Contract duy nhất — approach_01, canonical.
Phân cấp KHÔNG kiểm tra bằng prefix chuỗi (mã thật 66/652, 01/001
không có tính prefix); parent_code trong AdminIndex mới là nguồn
sự thật. PipelineResult.reject giữ mã/tên đã resolve.

Fix fix_schemas_forbidden_default_crash:
Schema cấm địa chỉ mặc định bằng STATUS thay vì raise exception.
__post_init__ không bao giờ raise nữa: mã sai định dạng, confidence
ngoài [0, 1] hay địa chỉ mặc định cấm đều trở thành một contract
fail-closed (status=REJECTED, confidence=0.0, reason_code rõ ràng),
để pipeline/gateway/adapter luôn nhận được kết quả và xử lý tiếp,
không sập vì ValueError.
"""
from __future__ import annotations
import math
import re, unicodedata
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Optional
FORBIDDEN_DEFAULT_ADDRESSES = frozenset({"xa dak ya mang yang gia lai", "dak ya mang yang gia lai", "xa dak ya"})
PROVINCE_CODE_RE = re.compile(r"^\d{2}$")
DISTRICT_CODE_RE = re.compile(r"^\d{3}$")
WARD_CODE_RE = re.compile(r"^\d{5}$")
def normalize_text(value: str) -> str:
    if not value: return ""
    text = unicodedata.normalize("NFD", value.strip().lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()
def is_forbidden_default(value: str) -> bool:
    return normalize_text(value) in FORBIDDEN_DEFAULT_ADDRESSES
class AddressStatus(str, Enum):
    PENDING="PENDING"; VERIFIED="VERIFIED"; NEEDS_REVIEW="NEEDS_REVIEW"; REJECTED="REJECTED"
class GateStatus(str, Enum):
    VERIFIED="VERIFIED"; NEED_CONFIRMATION="NEED_CONFIRMATION"; REJECTED_INSUFFICIENT="REJECTED_INSUFFICIENT"
def gate_to_address_status(gate: GateStatus) -> AddressStatus:
    return {GateStatus.VERIFIED: AddressStatus.VERIFIED, GateStatus.NEED_CONFIRMATION: AddressStatus.NEEDS_REVIEW, GateStatus.REJECTED_INSUFFICIENT: AddressStatus.REJECTED}[gate]
class ReasonCode(str, Enum):
    OK="OK"; INSUFFICIENT_INPUT="INSUFFICIENT_INPUT"; INPUT_TOO_SHORT="INPUT_TOO_SHORT"; NUMBER_ONLY="NUMBER_ONLY"; GENERIC_TOKEN_ONLY="GENERIC_TOKEN_ONLY"; MISSING_PROVINCE="MISSING_PROVINCE"; MISSING_DISTRICT="MISSING_DISTRICT"; MISSING_WARD="MISSING_WARD"; MISSING_HOUSE_OR_STREET="MISSING_HOUSE_OR_STREET"; PROVINCE_MISMATCH="PROVINCE_MISMATCH"; DISTRICT_MISMATCH="DISTRICT_MISMATCH"; WARD_MISMATCH="WARD_MISMATCH"; HIERARCHY_CONFLICT="HIERARCHY_CONFLICT"; LOW_MARGIN="LOW_MARGIN"; PROVIDER_DISAGREEMENT="PROVIDER_DISAGREEMENT"; FORBIDDEN_DEFAULT="FORBIDDEN_DEFAULT"; LOW_CONFIDENCE="LOW_CONFIDENCE"; PROVIDER_UNAVAILABLE="PROVIDER_UNAVAILABLE"; INVALID_CODE="INVALID_CODE"
class ProviderSource(str, Enum):
    GOONG="goong"; PANCAKE="pancake"; INTERNAL_ADMIN_DB="internal_admin_db"; VIETTEL_MAPS="viettel_maps"; OSM_NOMINATIM="osm_nominatim"; CUSTOMER_CONFIRMED="customer_confirmed"; MANUAL_REVIEW="manual_review"
class PipelineStage(str, Enum):
    NORMALIZE="normalize"; PARSE="parse"; ANCHOR="anchor"; MATCH="match"; GEOCODE="geocode"; RANK="rank"; VALIDATE="validate"; GATE="gate"; COMMIT="commit"
@dataclass(frozen=True)
class AddressContract:
    raw_input: str
    province: Optional[str]=None; district: Optional[str]=None; ward: Optional[str]=None
    street: Optional[str]=None; house_number: Optional[str]=None; hamlet: Optional[str]=None
    province_code: Optional[str]=None; district_code: Optional[str]=None; ward_code: Optional[str]=None
    lat: Optional[float]=None; lng: Optional[float]=None; confidence: float=0.0
    source: ProviderSource=ProviderSource.INTERNAL_ADMIN_DB; status: AddressStatus=AddressStatus.PENDING
    reason_code: ReasonCode=ReasonCode.OK; matched_tokens: list[str]=field(default_factory=list); unmatched_tokens: list[str]=field(default_factory=list)
    def __post_init__(self) -> None:
        # Không raise: mọi dữ liệu xấu đều thành status REJECTED.
        # 1. Chuẩn hoá mã: int 64 -> "64", strip khoảng trắng; sai định dạng -> None.
        invalid = False
        for field_name, pattern in (
            ("province_code", PROVINCE_CODE_RE),
            ("district_code", DISTRICT_CODE_RE),
            ("ward_code", WARD_CODE_RE),
        ):
            value = getattr(self, field_name)
            if value is None or value == "":
                if value == "":
                    object.__setattr__(self, field_name, None)
                continue
            text = str(value).strip()
            if pattern.match(text):
                object.__setattr__(self, field_name, text)
            else:
                object.__setattr__(self, field_name, None)
                invalid = True
        # 2. Clamp confidence về [0, 1] thay vì raise.
        try:
            conf = float(self.confidence)
        except (TypeError, ValueError):
            conf = 0.0
        if math.isnan(conf) or math.isinf(conf):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        object.__setattr__(self, "confidence", conf)
        # 3. Địa chỉ mặc định cấm ở dạng trần (không số nhà/đường):
        #    đây là fallback do hệ thống sinh ra, không phải khách ghi.
        #    Địa chỉ đầy đủ có số nhà/đường thì không reject ở schema.
        forbidden = is_forbidden_default(self.raw_input or "") and not self.has_house_or_street
        if forbidden:
            object.__setattr__(self, "status", AddressStatus.REJECTED)
            object.__setattr__(self, "reason_code", ReasonCode.FORBIDDEN_DEFAULT)
            object.__setattr__(self, "confidence", 0.0)
        elif invalid:
            object.__setattr__(self, "status", AddressStatus.REJECTED)
            object.__setattr__(self, "reason_code", ReasonCode.INVALID_CODE)
            object.__setattr__(self, "confidence", 0.0)
    @classmethod
    def rejected(cls, raw_input: str, reason: ReasonCode = ReasonCode.FORBIDDEN_DEFAULT, **kwargs: Any) -> "AddressContract":
        """Factory cho contract đã reject — không raise, dùng cho
        gate/adapter khi cần tạo nhanh một kết quả fail-closed."""
        kwargs.setdefault("status", AddressStatus.REJECTED)
        kwargs.setdefault("reason_code", reason)
        kwargs.setdefault("confidence", 0.0)
        return cls(raw_input=raw_input, **kwargs)
    def validate(self) -> list[str]:
        """Kiểm tra không-raise, dùng khi caller muốn liệt kê lỗi
        thay vì chỉ đọc status/reason_code."""
        errors: list[str] = []
        if self.status == AddressStatus.REJECTED:
            errors.append(self.reason_code.value)
        if self.province_code and not PROVINCE_CODE_RE.match(self.province_code):
            errors.append(ReasonCode.INVALID_CODE.value)
        if self.district_code and not DISTRICT_CODE_RE.match(self.district_code):
            errors.append(ReasonCode.INVALID_CODE.value)
        if self.ward_code and not WARD_CODE_RE.match(self.ward_code):
            errors.append(ReasonCode.INVALID_CODE.value)
        return errors
    @property
    def is_forbidden_default(self) -> bool: return is_forbidden_default(self.raw_input or "")
    @property
    def has_valid_codes(self) -> bool:
        return bool(self.province_code and self.district_code and self.ward_code and PROVINCE_CODE_RE.match(self.province_code) and DISTRICT_CODE_RE.match(self.district_code) and WARD_CODE_RE.match(self.ward_code))
    @property
    def is_hierarchy_complete(self) -> bool: return bool(self.province and self.district and self.ward and self.has_valid_codes)
    @property
    def has_house_or_street(self) -> bool: return bool((self.house_number or "").strip() or (self.street or "").strip())
    @property
    def completeness(self) -> float:
        parts=[bool(self.province),bool(self.district),bool(self.ward),bool(self.street),bool(self.house_number)]
        return sum(parts)/len(parts)
    @property
    def is_shippable(self) -> bool: return self.status==AddressStatus.VERIFIED and self.is_hierarchy_complete
    def to_display(self) -> str: return ", ".join(p for p in [self.house_number,self.street,self.hamlet,self.ward,self.district,self.province] if p)
    def to_dict(self, redact_detail: bool=False) -> dict[str, Any]:
        return {"raw_input":"***" if redact_detail else self.raw_input,"province":self.province,"district":self.district,"ward":self.ward,"street":self.street,"house_number":"***" if redact_detail else self.house_number,"hamlet":self.hamlet,"province_code":self.province_code,"district_code":self.district_code,"ward_code":self.ward_code,"lat":self.lat,"lng":self.lng,"confidence":round(self.confidence,4),"source":self.source.value,"status":self.status.value,"reason_code":self.reason_code.value,"matched_tokens":self.matched_tokens,"unmatched_tokens":self.unmatched_tokens}
@dataclass
class AddressCandidate:
    address: AddressContract; provider: ProviderSource; provider_score: float=0.0; rank_score: float=0.0
@dataclass
class PipelineResult:
    contract: AddressContract; stage_reached: PipelineStage; stage_errors: dict[str, str]=field(default_factory=dict); candidates: list[AddressCandidate]=field(default_factory=list)
    @property
    def rejected(self) -> bool: return self.contract.status==AddressStatus.REJECTED
    def reject(self, stage: PipelineStage, reason: ReasonCode) -> "PipelineResult":
        self.stage_reached=stage; self.stage_errors[stage.value]=reason.value
        self.contract=replace(self.contract, status=AddressStatus.REJECTED, reason_code=reason, confidence=0.0)
        return self
