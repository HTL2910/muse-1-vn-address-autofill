"""Validator - fix_validator_carrier_gps.

Hierarchy is checked by parent_code in the Canonical Admin DB, never by
string prefix, and a customer-written address is never rejected for
matching the forbidden default by itself - that decision belongs to
the gate.

What this fix changes against the previous version:
1. Carrier checks are PER CARRIER. The carrier that will actually
   carry the parcel (preferred_carrier / carrier at label time) selects
   its own servable-ward set and its own admin-code cross-check.
   Checking SPX data for a GHN parcel is "check unavailable" ->
   NEEDS_REVIEW, never a silent pass and never with the wrong set.
   Carrier conflicts are split by cause: CARRIER_NOT_SERVABLE,
   CARRIER_PROVINCE_MISMATCH / CARRIER_DISTRICT_MISMATCH /
   CARRIER_WARD_MISMATCH (e.g. stale post-2025-merger codes).
2. GPS: an explicit reverse-geocoder (boundary lookup) is
   authoritative - a point resolving to another province is
   GPS_PROVINCE_MISMATCH (error, penalty 0.35); another district is a
   strong warning. Without a reverse-geocoder the legacy centroid rule
   is kept as fallback (>200 km from the province centroid -> error).
   Invalid coordinates (out of range or 0,0) are INVALID_COORDINATES.
3. Every check always runs and appends its own field-level reason:
   missing/not-in-DB/mismatch for province, district and ward, street,
   carrier and GPS are collected in one pass so CS can fix all fields
   at once.

Fail-closed: any error -> BLOCKED, any warning or penalty ->
NEEDS_REVIEW, only a fully clean address returns OK.
Status: chua chay - ket qua do CI bao.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

try:  # typing only - the validator works with any index exposing .units
    from .province_anchor import AdminIndex
except Exception:  # pragma: no cover
    AdminIndex = Any  # type: ignore
from .schemas import AddressContract


class Decision(str, Enum):
    OK = "OK"; NEEDS_REVIEW = "NEEDS_REVIEW"; BLOCKED = "BLOCKED"


@dataclass
class ValidationError:
    code: str; field: str; message: str


@dataclass
class ValidationResult:
    decision: Decision
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)
    confidence_penalty: float = 0.0


def _haversine_km(lat1, lng1, lat2, lng2):
    r = math.radians; dlat = r(lat2 - lat1); dlng = r(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(dlng / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _result_field(obj: Any, *names: str):
    if obj is None:
        return None
    if isinstance(obj, dict):
        for n in names:
            if obj.get(n) not in (None, ""):
                return obj.get(n)
        return None
    for n in names:
        value = getattr(obj, n, None)
        if value not in (None, ""):
            return value
    return None


class HierarchicalValidator:
    def __init__(self, index: Optional[AdminIndex] = None,
                 carrier_servable_wards: Optional[set] = None,
                 carrier_servable: Optional[Dict[str, set]] = None,
                 carrier_checker: Any = None,
                 reverse_geocoder: Any = None,
                 gps_province_error_km: float = 200.0) -> None:
        self.index = index
        self.carrier_servable_wards = carrier_servable_wards
        self.carrier_servable = carrier_servable
        self.carrier_checker = carrier_checker
        self.reverse_geocoder = reverse_geocoder
        self.gps_province_error_km = gps_province_error_km

    def _carrier_lookup(self, carrier: str, addr: AddressContract):
        checker = self.carrier_checker
        if checker is None:
            return None, False
        try:
            if callable(checker):
                return checker(carrier, addr.province_code, addr.district_code, addr.ward_code), True
            for method_name in ("lookup", "check", "cross_check"):
                method = getattr(checker, method_name, None)
                if callable(method):
                    return method(carrier, addr.province_code, addr.district_code, addr.ward_code), True
        except Exception:
            return None, True
        return None, True

    def _check_carrier(self, addr: AddressContract, carrier: str,
                       errors: list, warnings: list, penalty: float) -> float:
        servable_set = None
        if self.carrier_servable is not None:
            if carrier in self.carrier_servable:
                servable_set = self.carrier_servable[carrier]
            elif self.carrier_checker is None:
                warnings.append(ValidationError(
                    "CARRIER_CHECK_UNAVAILABLE", "carrier",
                    f"No servable data for carrier {carrier}; cannot verify with another carrier's data."))
                penalty += 0.20
        if servable_set is None and self.carrier_servable is None:
            servable_set = self.carrier_servable_wards
        if servable_set is not None and addr.ward_code and addr.ward_code not in servable_set:
            errors.append(ValidationError(
                "CARRIER_NOT_SERVABLE", "ward_code",
                f"Carrier {carrier} does not serve this ward."))

        if self.carrier_checker is not None and addr.ward_code:
            result, _ = self._carrier_lookup(carrier, addr)
            if result is None:
                warnings.append(ValidationError(
                    "CARRIER_CHECK_UNAVAILABLE", "carrier",
                    f"Carrier {carrier} cross-check unavailable, manual review required."))
                penalty += 0.20
            else:
                servable = _result_field(result, "servable", "is_servable", "can_deliver")
                if servable is False:
                    errors.append(ValidationError(
                        "CARRIER_NOT_SERVABLE", "ward_code",
                        f"Carrier {carrier} reports this ward is not on its delivery route."))
                c_province = _result_field(result, "province_code")
                c_district = _result_field(result, "district_code")
                c_ward = _result_field(result, "ward_code")
                if c_province and addr.province_code and str(c_province) != str(addr.province_code):
                    errors.append(ValidationError(
                        "CARRIER_PROVINCE_MISMATCH", "province_code",
                        f"Carrier {carrier} maps this address to province {c_province}, not {addr.province_code}."))
                if c_district and addr.district_code and str(c_district) != str(addr.district_code):
                    errors.append(ValidationError(
                        "CARRIER_DISTRICT_MISMATCH", "district_code",
                        f"Carrier {carrier} maps this address to district {c_district}, not {addr.district_code}."))
                if c_ward and addr.ward_code and str(c_ward) != str(addr.ward_code):
                    errors.append(ValidationError(
                        "CARRIER_WARD_MISMATCH", "ward_code",
                        f"Carrier {carrier} maps this address to ward {c_ward}, not {addr.ward_code}."))
        return penalty

    def _reverse_lookup(self, lat: float, lng: float):
        geo = self.reverse_geocoder
        if geo is None:
            return None
        try:
            if callable(geo):
                return geo(lat, lng)
            for method_name in ("province_code_for_point", "reverse_geocode", "reverse", "lookup"):
                method = getattr(geo, method_name, None)
                if callable(method):
                    return method(lat, lng)
        except Exception:
            return None
        return None

    def _check_gps(self, addr: AddressContract, errors: list, warnings: list,
                   penalty: float) -> float:
        if addr.lat is None or addr.lng is None:
            return penalty
        lat, lng = float(addr.lat), float(addr.lng)
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0) or (lat == 0.0 and lng == 0.0):
            errors.append(ValidationError(
                "INVALID_COORDINATES", "lat_lng",
                f"Coordinates out of range or empty: ({lat}, {lng})."))
            return penalty + 0.35

        reverse = self._reverse_lookup(lat, lng) if self.reverse_geocoder is not None else None
        if reverse is not None:
            r_province = reverse if isinstance(reverse, str) else _result_field(reverse, "province_code", "province")
            r_district = None if isinstance(reverse, str) else _result_field(reverse, "district_code", "district")
            if r_province and addr.province_code and str(r_province) != str(addr.province_code):
                errors.append(ValidationError(
                    "GPS_PROVINCE_MISMATCH", "lat_lng",
                    f"GPS point resolves to province {r_province}, not {addr.province_code}."))
                return penalty + 0.35
            if r_district and addr.district_code and str(r_district) != str(addr.district_code):
                warnings.append(ValidationError(
                    "GPS_DISTRICT_MISMATCH", "lat_lng",
                    f"GPS point resolves to district {r_district}, not {addr.district_code}."))
                penalty += 0.20
            return penalty

        if self.index is not None and addr.province_code:
            prov = self.index.units.get(addr.province_code)
            if prov is not None:
                c_lat = getattr(prov, "centroid_lat", None)
                c_lng = getattr(prov, "centroid_lng", None)
                if c_lat is not None and c_lng is not None:
                    dist = _haversine_km(lat, lng, c_lat, c_lng)
                    if dist > self.gps_province_error_km:
                        errors.append(ValidationError(
                            "GPS_PROVINCE_MISMATCH", "lat_lng",
                            f"GPS {dist:.0f}km from anchored province centroid."))
                        penalty += 0.35
        return penalty

    def validate(self, addr: AddressContract, preferred_carrier: str = "SPX",
                 carrier: Optional[str] = None,
                 anchored_province_code: Optional[str] = None) -> ValidationResult:
        carrier = (carrier or preferred_carrier or "SPX").upper()
        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []
        penalty = 0.0

        if not addr.province_code:
            errors.append(ValidationError("MISSING_PROVINCE", "province_code", "Missing province code."))
        if not addr.district_code:
            errors.append(ValidationError("MISSING_DISTRICT", "district_code", "Missing district code."))
        if not addr.ward_code:
            errors.append(ValidationError("MISSING_WARD", "ward_code", "Missing ward code."))

        if anchored_province_code and addr.province_code and str(anchored_province_code) != str(addr.province_code):
            errors.append(ValidationError(
                "PROVINCE_MISMATCH", "province_code",
                "Proposed province differs from the province the customer explicitly wrote."))

        if self.index is not None:
            units = self.index.units
            prov = units.get(addr.province_code or "")
            dist = units.get(addr.district_code or "")
            ward = units.get(addr.ward_code or "")
            if addr.province_code and prov is None:
                errors.append(ValidationError("PROVINCE_NOT_IN_ADMIN_DB", "province_code",
                                              "Province code not in Canonical Admin DB."))
            if addr.district_code and dist is None:
                errors.append(ValidationError("DISTRICT_NOT_IN_ADMIN_DB", "district_code",
                                              "District code not in Canonical Admin DB."))
            if addr.ward_code and ward is None:
                errors.append(ValidationError("WARD_NOT_IN_ADMIN_DB", "ward_code",
                                              "Ward code not in Canonical Admin DB."))
            if dist is not None and addr.province_code and dist.parent_code != addr.province_code:
                errors.append(ValidationError("DISTRICT_MISMATCH", "district_code", "District not in province."))
            if ward is not None and addr.district_code and ward.parent_code != addr.district_code:
                errors.append(ValidationError("WARD_MISMATCH", "ward_code", "Ward not in district."))

        if not addr.has_house_or_street:
            warnings.append(ValidationError("MISSING_HOUSE_OR_STREET", "street", "Missing house/street."))
            penalty += 0.15

        penalty = self._check_carrier(addr, carrier, errors, warnings, penalty)
        penalty = self._check_gps(addr, errors, warnings, penalty)

        decision = Decision.BLOCKED if errors else (Decision.NEEDS_REVIEW if warnings or penalty > 0 else Decision.OK)
        return ValidationResult(decision=decision, errors=errors, warnings=warnings,
                                confidence_penalty=penalty)


def blocked_reasons(result: ValidationResult) -> Dict[str, str]:
    """Machine-readable field -> reason-code map for API/UI."""
    return {e.field: e.code for e in result.errors}
