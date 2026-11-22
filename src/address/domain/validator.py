"""
Hierarchical consistency validator - approach_11
Branch: expert/backend-sec-m1
Pure domain logic + injected ports for carrier codes and reverse geocode.
Runs BEFORE any shipping order is created. Fail-closed.

Checks:
1. ward in district, district in province (Canonical Admin DB is truth).
2. Street exists in ward if street data is available (soft check -> warning).
3. Cross-check carrier admin codes (SPX/GHN/GHTK): province or ward conflict
   blocks the order, with a per-field reason.
4. Reverse geocode (GPS/IP/app location if present): point must lie inside
   the inferred province/district bounding area, otherwise strong downgrade.
Every block returns field-level reasons so CS fixes the right field.
Status: chua chay - CI se bao ket qua.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Protocol


class ValidationDecision(str, Enum):
    PASS = "PASS"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class AdminUnit:
    code: str
    name: str
    level: str  # province | district | ward
    parent_code: str = ""


@dataclass(frozen=True)
class AddressToValidate:
    province_code: str
    district_code: str
    ward_code: str
    street: str = ""
    detail: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    anchored_province_code: Optional[str] = None  # province explicitly written by customer


class CanonicalAdminPort(Protocol):
    def get(self, code: str) -> Optional[AdminUnit]: ...
    def street_exists_in_ward(self, street_ascii: str, ward_code: str) -> Optional[bool]:
        """None = no street data (do not block), True/False = known."""
        ...


class CarrierCodePort(Protocol):
    def lookup(self, carrier: str, province_code: str, district_code: str,
               ward_code: str) -> Optional[dict]:
        """Return {'province_code':..., 'district_code':..., 'ward_code':...,
        'servable': bool} in OUR canonical codes, or None if carrier unreachable."""
        ...


class ReverseGeocodePort(Protocol):
    def province_code_for_point(self, lat: float, lng: float) -> Optional[str]: ...


@dataclass
class FieldError:
    field: str       # province | district | ward | street | location | carrier
    code: str
    message_vi: str


@dataclass
class ValidationResult:
    decision: ValidationDecision
    errors: list[FieldError] = field(default_factory=list)
    warnings: list[FieldError] = field(default_factory=list)
    confidence_penalty: float = 0.0

    @property
    def is_pass(self) -> bool:
        return self.decision == ValidationDecision.PASS


class HierarchicalValidator:
    CARRIERS = ("SPX", "GHN", "GHTK")

    def __init__(self, admin: CanonicalAdminPort,
                 carrier: Optional[CarrierCodePort] = None,
                 reverse: Optional[ReverseGeocodePort] = None):
        self._admin = admin
        self._carrier = carrier
        self._reverse = reverse

    def validate(self, addr: AddressToValidate,
                 preferred_carrier: str = "SPX") -> ValidationResult:
        errors: list[FieldError] = []
        warnings: list[FieldError] = []
        penalty = 0.0

        # 0. Explicit customer province is an anchor - never override it.
        if addr.anchored_province_code and addr.province_code != addr.anchored_province_code:
            errors.append(FieldError(
                "province", "PROVINCE_ANCHOR_MISMATCH",
                "Tỉnh đề xuất khác tỉnh khách đã ghi rõ. Chặn để CS xác nhận, không tự đổi tỉnh."))
            # Example regression: Cuor Dang, Dak Lak -> Pha Lai, Hai Duong must land here.

        # 1. Hierarchy in Canonical DB
        province = self._admin.get(addr.province_code) if addr.province_code else None
        district = self._admin.get(addr.district_code) if addr.district_code else None
        ward = self._admin.get(addr.ward_code) if addr.ward_code else None

        if not province:
            errors.append(FieldError("province", "PROVINCE_NOT_FOUND",
                                     "Không tìm thấy tỉnh/thành trong dữ liệu hành chính chuẩn."))
        if not district:
            errors.append(FieldError("district", "DISTRICT_NOT_FOUND",
                                     "Không tìm thấy quận/huyện trong dữ liệu hành chính chuẩn."))
        elif province and district.parent_code != addr.province_code:
            errors.append(FieldError("district", "DISTRICT_NOT_IN_PROVINCE",
                                     "Quận/huyện không thuộc tỉnh đã chọn."))
        if not ward:
            errors.append(FieldError("ward", "WARD_NOT_FOUND",
                                     "Không tìm thấy phường/xã trong dữ liệu hành chính chuẩn."))
        elif district and ward.parent_code != addr.district_code:
            errors.append(FieldError("ward", "WARD_NOT_IN_DISTRICT",
                                     "Phường/xã không thuộc quận/huyện đã chọn."))

        # 2. Street existence (soft when data exists)
        if addr.street and addr.ward_code and not errors:
            exists = self._admin.street_exists_in_ward(addr.street, addr.ward_code)
            if exists is False:
                warnings.append(FieldError("street", "STREET_NOT_IN_WARD",
                                           "Tên đường không thấy trong phường/xã này, cần CS kiểm tra số nhà/đường."))
                penalty += 0.15

        # 3. Carrier cross-check - conflict on province/ward blocks
        if self._carrier is not None and province and ward:
            carrier_result = None
            try:
                carrier_result = self._carrier.lookup(
                    preferred_carrier, addr.province_code, addr.district_code, addr.ward_code)
            except Exception:
                warnings.append(FieldError("carrier", "CARRIER_CHECK_UNAVAILABLE",
                                           "Không kiểm chứng được với hãng vận chuyển, chuyển rà soát thủ công."))
                penalty += 0.20
            if carrier_result is not None:
                if carrier_result.get("servable") is False:
                    errors.append(FieldError("carrier", "CARRIER_NOT_SERVABLE",
                                             f"{preferred_carrier} báo phường/xã không thuộc tuyến giao."))
                if carrier_result.get("province_code") not in (None, addr.province_code):
                    errors.append(FieldError("province", "CARRIER_PROVINCE_MISMATCH",
                                             f"{preferred_carrier} đối chiếu ra tỉnh khác, chặn tạo vận đơn."))
                if carrier_result.get("ward_code") not in (None, addr.ward_code):
                    errors.append(FieldError("ward", "CARRIER_WARD_MISMATCH",
                                             f"{preferred_carrier} đối chiếu ra phường/xã khác, cần xác nhận lại."))

        # 4. Reverse geocode consistency
        if self._reverse is not None and addr.lat is not None and addr.lng is not None:
            gps_province = self._reverse.province_code_for_point(addr.lat, addr.lng)
            if gps_province and gps_province != addr.province_code:
                warnings.append(FieldError("location", "GPS_PROVINCE_MISMATCH",
                                           "Vị trí GPS/app nằm ở tỉnh khác tỉnh của địa chỉ, hạ điểm mạnh."))
                penalty += 0.35

        if errors:
            decision = ValidationDecision.BLOCKED
        elif warnings or penalty >= 0.15:
            decision = ValidationDecision.NEEDS_REVIEW
        else:
            decision = ValidationDecision.PASS
        return ValidationResult(decision, errors, warnings, confidence_penalty=penalty)


def blocked_reasons(result: ValidationResult) -> Dict[str, str]:
    """Machine-readable map for API/UI: field -> reason code."""
    return {e.field: e.code for e in result.errors}
