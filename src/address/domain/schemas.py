"""Address Contract duy nhất — approach_01, canonical cho fix_missing_pipeline_core.

Mọi module parser, anchor, matcher, ranker, validator, gate đều giao
tiếp qua contract này. Không raise cho lỗi nghiệp vụ: địa chỉ cấm,
thiếu cấp, sai phân cấp đều trả về bằng status + reason_code, không
làm sập pipeline. Chỉ validate định dạng mã và lat/lng bằng exception.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Optional

FORBIDDEN_DEFAULT_ADDRESSES = frozenset(
    {"xa dak ya mang yang gia lai", "dak ya mang yang gia lai", "xa dak ya"}
)
PROVINCE_CODE_RE = re.compile(r"^\d{2}$")
DISTRICT_CODE_RE = re.compile(r"^\d{3}$")
WARD_CODE_RE = re.compile(r"^\d{5}$")


def normalize_text(value: str) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFD", value.strip().lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_forbidden_default(value: str) -> bool:
    return normalize_text(value) in FORBIDDEN_DEFAULT_ADDRESSES


class AddressStatus(str, Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"


class GateStatus(str, Enum):
    VERIFIED = "VERIFIED"
    NEED_CONFIRMATION = "NEED_CONFIRMATION"
    REJECTED_INSUFFICIENT = "REJECTED_INSUFFICIENT"


def gate_to_address_status(gate: GateStatus) -> AddressStatus:
    return {
        GateStatus.VERIFIED: AddressStatus.VERIFIED,
        GateStatus.NEED_CONFIRMATION: AddressStatus.NEEDS_REVIEW,
        GateStatus.REJECTED_INSUFFICIENT: AddressStatus.REJECTED,
    }[gate]


class ReasonCode(str, Enum):
    OK = "OK"
    INSUFFICIENT_INPUT = "INSUFFICIENT_INPUT"
    INPUT_TOO_SHORT = "INPUT_TOO_SHORT"
    NUMBER_ONLY = "NUMBER_ONLY"
    GENERIC_TOKEN_ONLY = "GENERIC_TOKEN_ONLY"
    MISSING_PROVINCE = "MISSING_PROVINCE"
    MISSING_DISTRICT = "MISSING_DISTRICT"
    MISSING_WARD = "MISSING_WARD"
    MISSING_HOUSE_OR_STREET = "MISSING_HOUSE_OR_STREET"
    PROVINCE_MISMATCH = "PROVINCE_MISMATCH"
    DISTRICT_MISMATCH = "DISTRICT_MISMATCH"
    WARD_MISMATCH = "WARD_MISMATCH"
    HIERARCHY_CONFLICT = "HIERARCHY_CONFLICT"
    LOW_MARGIN = "LOW_MARGIN"
    PROVIDER_DISAGREEMENT = "PROVIDER_DISAGREEMENT"
    FORBIDDEN_DEFAULT = "FORBIDDEN_DEFAULT"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"


class ProviderSource(str, Enum):
    GOONG = "goong"
    PANCAKE = "pancake"
    INTERNAL_ADMIN_DB = "internal_admin_db"
    VIETTEL_MAPS = "viettel_maps"
    OSM_NOMINATIM = "osm_nominatim"
    CUSTOMER_CONFIRMED = "customer_confirmed"
    MANUAL_REVIEW = "manual_review"


class PipelineStage(str, Enum):
    NORMALIZE = "normalize"
    PARSE = "parse"
    ANCHOR = "anchor"
    MATCH = "match"
    GEOCODE = "geocode"
    RANK = "rank"
    VALIDATE = "validate"
    GATE = "gate"
    COMMIT = "commit"


@dataclass(frozen=True)
class AddressContract:
    raw_input: str
    province: Optional[str] = None
    district: Optional[str] = None
    ward: Optional[str] = None
    street: Optional[str] = None
    house_number: Optional[str] = None
    hamlet: Optional[str] = None
    province_code: Optional[str] = None
    district_code: Optional[str] = None
    ward_code: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    confidence: float = 0.0
    source: ProviderSource = ProviderSource.INTERNAL_ADMIN_DB
    status: AddressStatus = AddressStatus.PENDING
    reason_code: ReasonCode = ReasonCode.OK
    matched_tokens: list[str] = field(default_factory=list)
    unmatched_tokens: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Chỉ lỗi định dạng mới raise. Lỗi nghiệp vụ (default cấm,
        # thiếu cấp) được biểu diễn bằng status/reason_code.
        if self.province_code and not PROVINCE_CODE_RE.match(self.province_code):
            raise ValueError(f"Invalid province_code: {self.province_code}")
        if self.district_code and not DISTRICT_CODE_RE.match(self.district_code):
            raise ValueError(f"Invalid district_code: {self.district_code}")
        if self.ward_code and not WARD_CODE_RE.match(self.ward_code):
            raise ValueError(f"Invalid ward_code: {self.ward_code}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        if self.lat is not None and not -90.0 <= self.lat <= 90.0:
            raise ValueError("lat out of range")
        if self.lng is not None and not -180.0 <= self.lng <= 180.0:
            raise ValueError("lng out of range")

    @property
    def is_forbidden_default(self) -> bool:
        return is_forbidden_default(self.raw_input or "")

    @property
    def has_valid_codes(self) -> bool:
        return bool(
            self.province_code and self.district_code and self.ward_code
            and self.district_code.startswith(self.province_code)
            and self.ward_code.startswith(self.district_code)
        )

    @property
    def is_hierarchy_complete(self) -> bool:
        return bool(self.province and self.district and self.ward and self.has_valid_codes)

    @property
    def has_house_or_street(self) -> bool:
        return bool((self.house_number or "").strip() or (self.street or "").strip())

    @property
    def completeness(self) -> float:
        parts = [bool(self.province), bool(self.district), bool(self.ward),
                 bool(self.street), bool(self.house_number)]
        return sum(parts) / len(parts)

    @property
    def is_shippable(self) -> bool:
        return self.status == AddressStatus.VERIFIED and self.is_hierarchy_complete

    def to_display(self) -> str:
        return ", ".join(p for p in [self.house_number, self.street, self.hamlet,
                                     self.ward, self.district, self.province] if p)

    def to_dict(self, redact_detail: bool = False) -> dict[str, Any]:
        return {
            "raw_input": "***" if redact_detail else self.raw_input,
            "province": self.province, "district": self.district, "ward": self.ward,
            "street": self.street,
            "house_number": "***" if redact_detail else self.house_number,
            "hamlet": self.hamlet, "province_code": self.province_code,
            "district_code": self.district_code, "ward_code": self.ward_code,
            "lat": self.lat, "lng": self.lng, "confidence": round(self.confidence, 4),
            "source": self.source.value, "status": self.status.value,
            "reason_code": self.reason_code.value,
            "matched_tokens": self.matched_tokens, "unmatched_tokens": self.unmatched_tokens,
        }


@dataclass
class AddressCandidate:
    address: AddressContract
    provider: ProviderSource
    provider_score: float = 0.0
    rank_score: float = 0.0


@dataclass
class PipelineResult:
    contract: AddressContract
    stage_reached: PipelineStage
    stage_errors: dict[str, str] = field(default_factory=dict)
    candidates: list[AddressCandidate] = field(default_factory=list)

    @property
    def rejected(self) -> bool:
        return self.contract.status == AddressStatus.REJECTED

    def reject(self, stage: PipelineStage, reason: ReasonCode) -> "PipelineResult":
        self.stage_reached = stage
        self.stage_errors[stage.value] = reason.value
        # replace() không raise cho default cấm — lỗi nghiệp vụ trả về status.
        self.contract = replace(self.contract, status=AddressStatus.REJECTED,
                                reason_code=reason, confidence=0.0)
        return self
