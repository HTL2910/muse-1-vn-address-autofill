"""Tests cho fix_schemas_forbidden_default_crash:
schema cấm địa chỉ mặc định bằng status, không raise exception.
Chạy: python -m pytest tests/test_schemas_forbidden_default.py -v
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.address.domain.schemas import AddressContract, AddressStatus, PipelineResult, PipelineStage, ReasonCode
def test_bare_forbidden_default_is_rejected_not_raised():
    contract = AddressContract(raw_input="Xã Đăk Yă, Mang Yang, Gia Lai", province="Gia Lai", district="Mang Yang", ward="Đăk Yă", province_code="64", district_code="635", ward_code="63501")
    assert contract.status == AddressStatus.REJECTED
    assert contract.reason_code == ReasonCode.FORBIDDEN_DEFAULT
    assert contract.confidence == 0.0
    assert not contract.is_shippable
    assert contract.to_dict()["status"] == "REJECTED"
def test_forbidden_default_variants_are_rejected_not_raised():
    for raw in ("Xã Đăk Yă, Mang Yang, Gia Lai", "Đăk Yă, Mang Yang, Gia Lai", "Xa Dak Ya"):
        contract = AddressContract(raw_input=raw, province_code="64")
        assert contract.status == AddressStatus.REJECTED, raw
        assert contract.reason_code == ReasonCode.FORBIDDEN_DEFAULT, raw
def test_full_customer_address_with_dak_ya_is_not_rejected_by_schema():
    contract = AddressContract(raw_input="123 Đường Lê Lợi, Xã Đăk Yă, Mang Yang, Gia Lai", province="Gia Lai", district="Mang Yang", ward="Đăk Yă", house_number="123", street="Lê Lợi", province_code="64", district_code="635", ward_code="63501")
    assert contract.status == AddressStatus.PENDING
    assert contract.reason_code == ReasonCode.OK
    assert contract.province_code == "64"
def test_invalid_province_code_is_rejected_not_raised():
    contract = AddressContract(raw_input="x", province_code="6A")
    assert contract.status == AddressStatus.REJECTED
    assert contract.reason_code == ReasonCode.INVALID_CODE
    assert contract.province_code is None
    assert contract.confidence == 0.0
def test_invalid_district_and_ward_codes_are_rejected_not_raised():
    contract = AddressContract(raw_input="x", province_code="64", district_code="65X", ward_code="123")
    assert contract.status == AddressStatus.REJECTED
    assert contract.reason_code == ReasonCode.INVALID_CODE
    assert contract.district_code is None
    assert contract.ward_code is None
    assert contract.province_code == "64"
def test_confidence_out_of_range_is_clamped_not_raised():
    assert AddressContract(raw_input="x", confidence=1.5).confidence == 1.0
    assert AddressContract(raw_input="x", confidence=-0.2).confidence == 0.0
    assert AddressContract(raw_input="x", confidence=0.85).confidence == 0.85
def test_valid_contract_is_unchanged():
    contract = AddressContract(raw_input="123 Nguyễn Huệ, Bến Nghé, Quận 1, TP. Hồ Chí Minh", province="Hồ Chí Minh", district="Quận 1", ward="Bến Nghé", house_number="123", street="Nguyễn Huệ", province_code="79", district_code="760", ward_code="76001", confidence=0.9, status=AddressStatus.VERIFIED)
    assert contract.status == AddressStatus.VERIFIED
    assert contract.reason_code == ReasonCode.OK
    assert contract.is_shippable
def test_pipeline_result_reject_on_forbidden_contract_does_not_raise():
    contract = AddressContract(raw_input="Xã Đăk Yă, Mang Yang, Gia Lai", province_code="64")
    result = PipelineResult(contract=contract, stage_reached=PipelineStage.GATE)
    result.reject(PipelineStage.GATE, ReasonCode.FORBIDDEN_DEFAULT)
    assert result.rejected
    assert result.contract.status == AddressStatus.REJECTED
    assert result.contract.reason_code == ReasonCode.FORBIDDEN_DEFAULT
def test_rejected_factory_and_validate_do_not_raise():
    contract = AddressContract.rejected("Xã Đăk Yă, Mang Yang, Gia Lai")
    assert contract.status == AddressStatus.REJECTED
    assert ReasonCode.FORBIDDEN_DEFAULT.value in contract.validate()
    assert AddressContract(raw_input="x", province_code="64").validate() == []
