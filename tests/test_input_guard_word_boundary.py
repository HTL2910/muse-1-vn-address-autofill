"""Hồi quy cho fix_input_guard_substring_false_level.

Tên hành chính chỉ được tính khi khớp theo ranh giới từ trong một đoạn,
sau khi loại cụm đường/số nhà — không được tính bằng substring.
Đồng thời check() phải nhận được ParsedAddress làm đối số thứ hai như
AddressService.resolve gọi: guard.check(raw_input, parsed), và trả về
GuardResult có .passed và .reason_code là ReasonCode (không phải str).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.address.domain.input_guard import GuardDecision, InputGuard
from src.address.domain.schemas import ReasonCode


def _guard():
    return InputGuard(
        known_provinces=["Đắk Lắk", "Hồ Chí Minh", "Thành phố Hồ Chí Minh"],
        known_districts=["Krông Búk", "Cư M'gar", "Quận 1"],
        known_wards=["Cuôr Đăng", "Bến Nghé"],
    )


def _parsed_cuor_dang():
    return SimpleNamespace(
        raw_input="127 Nguyễn Trãi, xã Cuôr Đăng, huyện Cư M'gar, tỉnh Đắk Lắk",
        house_number="127", street="Nguyễn Trãi",
        ward_name="Cuôr Đăng", district_name="Cư M'gar", province_name="Đắk Lắk",
        ward_code="66401", district_code="664", province_code="66",
    )


def test_check_accepts_parsed_as_second_positional_arg():
    """Hồi quy TypeError ở AddressService: check(raw, parsed) không được lỗi."""
    result = _guard().check(
        "127 Nguyễn Trãi, xã Cuôr Đăng, huyện Cư M'gar, tỉnh Đắk Lắk",
        _parsed_cuor_dang(),
    )
    assert result.decision == GuardDecision.PASS
    assert result.passed is True
    assert result.reason_code == ReasonCode.OK
    assert isinstance(result.reason_code, ReasonCode)
    assert result.allow_waybill is True
    assert result.call_geocoder is True


def test_check_accepts_parsed_as_keyword_arg():
    result = _guard().check(
        "127 Nguyễn Trãi, xã Cuôr Đăng, huyện Cư M'gar, tỉnh Đắk Lắk",
        parsed=_parsed_cuor_dang(),
    )
    assert result.decision == GuardDecision.PASS
    assert result.passed is True


def test_guard_result_passed_is_false_when_not_pass():
    result = _guard().check("73")
    assert result.passed is False
    assert result.decision == GuardDecision.REJECTED
    assert result.reason_code == ReasonCode.NUMBER_ONLY
    assert isinstance(result.reason_code, ReasonCode)


def test_parsed_street_name_misparsed_as_ward_does_not_count():
    """parsed.ward_name='Lam Sơn' không có trong known_wards thì không tính xã."""
    parsed = SimpleNamespace(
        ward_name="Lam Sơn", district_name=None, province_name=None,
        ward_code=None, district_code=None, province_code=None,
    )
    result = _guard().check("Sói 799, đường Lam Sơn", parsed)
    assert result.decision == GuardDecision.NEED_CLARIFICATION
    assert result.passed is False
    assert "ward" not in result.detected_levels
    assert result.allow_waybill is False


def test_street_name_containing_province_does_not_count():
    """'đường Đắk Lắk' là tên đường, không phải tỉnh Đắk Lắk."""
    result = _guard().check("Số 10, đường Đắk Lắk")
    assert result.decision != GuardDecision.PASS
    assert result.passed is False
    assert result.allow_waybill is False
    assert result.call_geocoder is False
    assert "province" not in result.detected_levels


def test_street_name_containing_province_plus_one_real_level_still_fails():
    """'đường Đắk Lắk, Quận 1' chỉ có 1 cấp thật (quận), chưa đủ 2 cấp."""
    result = _guard().check("Số 10, đường Đắk Lắk, Quận 1")
    assert result.decision == GuardDecision.NEED_CLARIFICATION
    assert "province" not in result.detected_levels
    assert "district" in result.detected_levels


def test_ward_name_inside_street_segment_does_not_count():
    """'đường Bến Nghé' là tên đường, không phải phường Bến Nghé."""
    result = _guard().check("Số 10, đường Bến Nghé, Quận 1")
    assert result.decision == GuardDecision.NEED_CLARIFICATION
    assert "ward" not in result.detected_levels
    assert "district" in result.detected_levels


def test_real_province_and_ward_pass():
    """'xã Cuôr Đăng, Đắk Lắk': xã và tỉnh đứng riêng từng đoạn -> PASS."""
    result = _guard().check("xã Cuôr Đăng, Đắk Lắk")
    assert result.decision == GuardDecision.PASS
    assert result.passed is True
    assert result.allow_waybill is True
    assert result.call_geocoder is True
    assert "province" in result.detected_levels
    assert "ward" in result.detected_levels


def test_full_hcm_address_passes():
    result = _guard().check(
        "Số 12, đường Lê Lợi, Phường Bến Nghé, Quận 1, Thành phố Hồ Chí Minh"
    )
    assert result.decision == GuardDecision.PASS
    assert set(result.detected_levels) == {"province", "district", "ward"}


def test_partial_word_does_not_match():
    """'Đắk Lắkx' là một token khác, không khớp tên chuẩn 'Đắk Lắk'."""
    result = _guard().check("Số 1, đường Đắk Lắkx")
    assert "province" not in result.detected_levels
    assert result.decision != GuardDecision.PASS


def test_short_inputs_still_rejected_or_clarified():
    for raw in ("73", "127", "bi", "Bị", "Xom", "Sói 799, đường Lam Sơn"):
        result = _guard().check(raw)
        assert result.decision in (GuardDecision.REJECTED, GuardDecision.NEED_CLARIFICATION), raw
        assert result.passed is False, raw
        assert result.allow_waybill is False, raw
        assert result.call_geocoder is False, raw


def test_no_known_names_fallback_still_works():
    """Không inject tên chuẩn thì fallback tiền tố có \\b vẫn tính cấp."""
    result = InputGuard().check("Phường Bến Nghé, Quận 1")
    assert result.decision == GuardDecision.PASS
    assert result.passed is True
    assert set(result.detected_levels) == {"district", "ward"}
