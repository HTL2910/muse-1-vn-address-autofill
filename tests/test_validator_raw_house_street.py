"""Hồi quy: Validator không trừ điểm nhà/đường khi bằng chứng nằm trong raw_input."""
from __future__ import annotations

from src.address.domain.schemas import AddressContract
from src.address.domain.validator import Decision, HierarchicalValidator


def _contract(raw, province_code="79", district_code="760", ward_code="76002"):
    return AddressContract(
        raw_input=raw, province_code=province_code,
        district_code=district_code, ward_code=ward_code,
    )


def test_full_address_with_street_only_in_raw_is_ok():
    result = HierarchicalValidator().validate(
        _contract("Số 12, đường Lê Lợi, Phường Bến Nghé, Quận 1, Thành phố Hồ Chí Minh"))
    assert result.decision == Decision.OK
    assert result.confidence_penalty == 0.0


def test_address_without_house_or_street_still_warns():
    result = HierarchicalValidator().validate(
        _contract("Phường Bến Nghé, Quận 1, Thành phố Hồ Chí Minh"))
    assert result.decision == Decision.NEEDS_REVIEW
    assert any(w.code == "MISSING_HOUSE_OR_STREET" for w in result.warnings)


def test_house_number_alone_does_not_clear_warning():
    result = HierarchicalValidator().validate(_contract("Số 12"))
    assert result.decision == Decision.NEEDS_REVIEW
