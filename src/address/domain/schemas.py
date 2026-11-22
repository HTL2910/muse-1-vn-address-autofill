"""
Address Contract duy nhất — approach_01, nhánh expert/systems-arch-m1.

Vị trí trong pipeline:
    Normalize -> Parse -> Anchor tỉnh -> Match trong phạm vi đã khóa
    -> Multi-provider Geocode -> Rank -> Validate phân cấp
    -> Confidence Gate -> Commit / NEEDS_REVIEW / REJECTED

Mọi module Goong, Pancake, SPX, GHN đều đi qua pipeline này.
File này chỉ chứa logic thuần, không phụ thuộc provider, DB hay HTTP.
Không có địa chỉ mặc định. Không đoán khi thiếu dữ liệu.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Hằng số cấm — triệt tiêu lỗi "không đọc được thì điền mặc định"
# ---------------------------------------------------------------------------
# So sánh ở dạng đã chuẩn hóa (không dấu, thường, bỏ khoảng trắng thừa).
FORBIDDEN_DEFAULT_ADDRESSES = frozenset(
    {
        "xa dak ya mang yang gia lai",
        "dak ya mang yang gia lai",
        "xa dak ya",
    }
)

# Mã hành chính Việt Nam sau sáp nhập 2025: tỉnh 2 số, huyện 3 số, xã 5 số.
PROVINCE_CODE_RE = re.compile(r"^\d{2}$")
DISTRICT_CODE_RE = re.compile(r"^\d{3}$")
WARD_CODE_RE = re.compile(r"^\d{5}$")


def normalize_text(value: str) -> str:
    """Chuẩn hóa để so sánh: thường, bỏ dấu, đ->d, bỏ ký tự thừa."""
    if not value:
        return ""
    text = unicodedata.normalize("NFD", value.strip().lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "d")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_forbidden_default(value: str) -> bool:
    return normalize_text(value) in FORBIDDEN_DEFAULT_ADDRESSES


class AddressStatus(str, Enum):
    """Trạng thái duy nhất của một địa chỉ trong pipeline."""

    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"


class GateStatus(str, Enum):
    """Kết quả trả về cho app / Pancake ở tầng Confidence Gate."""

    VERIFIED = "VERIFIED"
    NEED_CONFIRMATION = "NEED_CONFIRMATION"
    REJECTED_INSUFFICIENT = "REJECTED_INSUFFICIENT"


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
    """
    Address Contract duy nhất. Một địa chỉ chỉ được coi là dùng được
    cho vận chuyển khi đủ 3 mã hành chính và status == VERIFIED.
    """

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
        if is_forbidden_default(self.raw_input or ""):
            raise ValueError("Forbidden default address in raw_input")
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

    # -- Kiểm tra phân cấp -------------------------------------------------
    @property
    def has_valid_codes(self) -> bool:
        return bool(
            self.province_code
            and self.district_code
            and self.ward_code
            and PROVINCE_CODE_RE.match(self.province_code)
            and DISTRICT_CODE_RE.match(self.district_code)
            and WARD_CODE_RE.match(self.ward_code)
            # Mã huyện/xã phải bắt đầu bằng mã cha (ràng buộc phân cấp).
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
        """Độ phủ thành phần 0-1, dùng làm feature cho Confidence Gate."""
        parts = [
            bool(self.province),
            bool(self.district),
            bool(self.ward),
            bool(self.street),
            bool(self.house_number),
        ]
        return sum(parts) / len(parts)

    @property
    def is_shippable(self) -> bool:
        """Chỉ VERIFIED + đủ mã + đủ phân cấp mới được đẩy sang SPX/GHN."""
        return self.status == AddressStatus.VERIFIED and self.is_hierarchy_complete

    def to_display(self) -> str:
        parts = [
            p
            for p in [
                self.house_number,
                self.street,
                self.hamlet,
                self.ward,
                self.district,
                self.province,
            ]
            if p
        ]
        return ", ".join(parts)

    def to_dict(self, redact_detail: bool = False) -> dict[str, Any]:
        return {
            "raw_input": "***" if redact_detail else self.raw_input,
            "province": self.province,
            "district": self.district,
            "ward": self.ward,
            "street": self.street,
            "house_number": "***" if redact_detail else self.house_number,
            "hamlet": self.hamlet,
            "province_code": self.province_code,
            "district_code": self.district_code,
            "ward_code": self.ward_code,
            "lat": self.lat,
            "lng": self.lng,
            "confidence": round(self.confidence, 4),
            "source": self.source.value,
            "status": self.status.value,
            "reason_code": self.reason_code.value,
            "matched_tokens": self.matched_tokens,
            "unmatched_tokens": self.unmatched_tokens,
        }


@dataclass
class AddressCandidate:
    """Một ứng viên từ provider, luôn mang theo mã hành chính để đối chiếu."""

    address: AddressContract
    provider: ProviderSource
    provider_score: float = 0.0  # điểm gốc của provider, 0-1
    rank_score: float = 0.0  # điểm sau khi cộng/trừ theo ràng buộc phân cấp


@dataclass
class PipelineResult:
    """Kết quả đi qua từng tầng; tầng nào cũng có quyền từ chối."""

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
        self.contract = AddressContract(
            **{**self.contract.__dict__, "status": AddressStatus.REJECTED,
               "reason_code": reason, "confidence": 0.0}
        )
        return self
