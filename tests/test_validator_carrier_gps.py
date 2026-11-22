"""Tests for fix_validator_carrier_gps. Chua chay - CI bao ket qua.
Run: python -m pytest tests/test_validator_carrier_gps.py -q

Codes follow schemas.py formats: province 2 digits, district 3, ward 5.
The fake index is a duck-typed stand-in for AdminIndex (.units only),
which is exactly what HierarchicalValidator consumes.
"""
from dataclasses import dataclass
from typing import Optional

import pytest

from src.address.domain.schemas import AddressContract
from src.address.domain.validator import Decision, HierarchicalValidator, blocked_reasons


@dataclass
class _Unit:
    parent_code: str = ""
    centroid_lat: Optional[float] = None
    centroid_lng: Optional[float] = None


class _Index:
    def __init__(self):
        self.units = {
            "66": _Unit("", 13.0, 108.0),        # Dak Lak + centroid
            "34": _Unit("", 20.9, 106.3),        # Hai Duong
            "652": _Unit("66"),                  # Krong Ana
            "340": _Unit("34"),                  # Chi Linh
            "65201": _Unit("652"),               # Cuor Dang
            "34001": _Unit("340"),               # Pha Lai
        }


def _addr(**over):
    base = dict(raw_input="so 12 Cuor Dang Krong Ana Dak Lak",
                province="Đắk Lắk", district="Krông Ana", ward="Cuôr Đăng",
                province_code="66", district_code="652", ward_code="65201",
                house_number="12", street="Cuôr Đăng", confidence=0.95)
    base.update(over)
    return AddressContract(**base)


def _codes(result):
    return {e.code for e in result.errors}


def test_valid_address_ok_for_its_own_carrier():
    v = HierarchicalValidator(_Index(), carrier_servable={"SPX": {"65201"}})
    result = v.validate(_addr(), preferred_carrier="SPX")
    assert result.decision == Decision.OK
    assert result.errors == []


def test_wrong_carrier_set_is_not_applied():
    # SPX serves the ward, the parcel ships with GHN and there is no GHN
    # data: must be NEEDS_REVIEW (check unavailable), never a silent OK
    # borrowed from SPX data and never blocked by SPX's set either.
    v = HierarchicalValidator(_Index(), carrier_servable={"SPX": {"65201"}})
    result = v.validate(_addr(), preferred_carrier="GHN")
    assert result.decision == Decision.NEEDS_REVIEW
    assert "CARRIER_CHECK_UNAVAILABLE" in {w.code for w in result.warnings}


def test_per_carrier_servable_blocked_only_for_that_carrier():
    v = HierarchicalValidator(_Index(), carrier_servable={"SPX": {"65201"}, "GHN": set()})
    assert v.validate(_addr(), preferred_carrier="SPX").decision == Decision.OK
    ghn = v.validate(_addr(), carrier="GHN")
    assert ghn.decision == Decision.BLOCKED
    assert "CARRIER_NOT_SERVABLE" in _codes(ghn)


def test_carrier_checker_province_and_ward_mismatch_are_split():
    def checker(carrier, p, d, w):
        return {"servable": True, "province_code": "34", "district_code": d, "ward_code": "34001"}
    v = HierarchicalValidator(_Index(), carrier_checker=checker)
    result = v.validate(_addr(), preferred_carrier="SPX")
    assert result.decision == Decision.BLOCKED
    assert "CARRIER_PROVINCE_MISMATCH" in _codes(result)
    assert "CARRIER_WARD_MISMATCH" in _codes(result)


def test_carrier_checker_unavailable_goes_to_review_not_pass():
    v = HierarchicalValidator(_Index(), carrier_checker=lambda c, p, d, w: None)
    result = v.validate(_addr(), preferred_carrier="SPX")
    assert result.decision == Decision.NEEDS_REVIEW
    assert "CARRIER_CHECK_UNAVAILABLE" in {w.code for w in result.warnings}


def test_reverse_geocode_province_mismatch_blocked():
    v = HierarchicalValidator(_Index(), reverse_geocoder=lambda lat, lng: "34")
    result = v.validate(_addr(lat=13.0, lng=108.0), preferred_carrier="SPX")
    assert result.decision == Decision.BLOCKED
    assert "GPS_PROVINCE_MISMATCH" in _codes(result)
    assert result.confidence_penalty >= 0.35


def test_reverse_geocode_match_is_ok():
    v = HierarchicalValidator(_Index(), reverse_geocoder=lambda lat, lng: {"province_code": "66", "district_code": "652"})
    result = v.validate(_addr(lat=12.7, lng=108.0), preferred_carrier="SPX")
    assert result.decision == Decision.OK


def test_centroid_fallback_still_blocks_far_gps():
    v = HierarchicalValidator(_Index())  # no reverse geocoder
    far = v.validate(_addr(lat=20.9, lng=106.3), preferred_carrier="SPX")  # Hai Duong point, Dak Lak address
    assert far.decision == Decision.BLOCKED
    assert "GPS_PROVINCE_MISMATCH" in _codes(far)


def test_invalid_coordinates_blocked():
    v = HierarchicalValidator(_Index())
    for lat, lng in ((0.0, 0.0), (91.0, 108.0), (13.0, 181.0)):
        result = v.validate(_addr(lat=lat, lng=lng), preferred_carrier="SPX")
        assert result.decision == Decision.BLOCKED
        assert "INVALID_COORDINATES" in _codes(result)


def test_all_field_reasons_collected_in_one_pass():
    v = HierarchicalValidator(
        _Index(),
        carrier_servable={"SPX": set()},
        reverse_geocoder=lambda lat, lng: "34")
    result = v.validate(
        _addr(district_code="999", lat=13.0, lng=108.0), preferred_carrier="SPX")
    codes = _codes(result)
    assert "DISTRICT_NOT_IN_ADMIN_DB" in codes       # district field
    assert "WARD_MISMATCH" in codes                  # ward field (parent is 652, not 999)
    assert "CARRIER_NOT_SERVABLE" in codes           # carrier check still ran
    assert "GPS_PROVINCE_MISMATCH" in codes          # GPS check still ran
    fields = {e.field for e in result.errors}
    assert {"district_code", "ward_code", "lat_lng"} <= fields
    assert blocked_reasons(result)["district_code"] == "DISTRICT_NOT_IN_ADMIN_DB"


def test_missing_and_unknown_codes_reported_per_field():
    v = HierarchicalValidator(_Index())
    missing = v.validate(_addr(province_code=None, district_code=None, ward_code=None))
    assert {"MISSING_PROVINCE", "MISSING_DISTRICT", "MISSING_WARD"} <= _codes(missing)
    unknown = v.validate(_addr(province_code="99", district_code="998", ward_code="99801"))
    assert "PROVINCE_NOT_IN_ADMIN_DB" in _codes(unknown)
    assert "DISTRICT_NOT_IN_ADMIN_DB" in _codes(unknown)
    assert "WARD_NOT_IN_ADMIN_DB" in _codes(unknown)


def test_cross_province_pairing_blocked_cuor_dang_case():
    # Cuor Dang ward code paired with Chi Linh district / Hai Duong province
    v = HierarchicalValidator(_Index(), carrier_servable={"SPX": {"65201", "34001"}})
    result = v.validate(_addr(province_code="34", district_code="340"), preferred_carrier="SPX")
    assert result.decision == Decision.BLOCKED
    assert "WARD_MISMATCH" in _codes(result)
