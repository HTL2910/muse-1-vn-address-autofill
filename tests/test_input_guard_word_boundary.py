"""Hồi quy cho fix_input_guard_substring_false_level.

Tên hành chính chỉ được tính khi khớp theo ranh giới từ trong một đoạn,
sau khi loại cụm đường/số nhà — không được tính bằng substring.
"""
from __future__ import annotations

from src.address.domain.input_guard import GuardDecision, InputGuard


def _guard():
    return InputGuard(
        known_provinces=["Đắk Lắk", "Hồ Chí Minh", "Thành phố Hồ Chí Minh"],
        known_districts=["Krông Búk", "Quận 1"],
        known_wards=["Cuôr Đăng", "Bến Nghé"],
    )


def test_street_name_containing_province_does_not_count():
    """'đường Đắk Lắk' là tên đường, không phải tỉnh Đắk Lắk."""
    result = _guard().check("Số 10, đường Đắk Lắk")
    assert result.decision != GuardDecision.PASS
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
        assert result.allow_waybill is False, raw
        assert result.call_geocoder is False, raw


def test_no_known_names_fallback_still_works():
    """Không inject tên chuẩn thì fallback tiền tố có \\b vẫn tính cấp."""
    result = InputGuard().check("Phường Bến Nghé, Quận 1")
    assert result.decision == GuardDecision.PASS
    assert set(result.detected_levels) == {"district", "ward"}
