"""Hồi quy: cấp từ parsed chỉ tính khi có bằng chứng trong raw_input.

Tên parser đoán ra nhưng khách không viết thì không được tính làm cấp.
"""
from __future__ import annotations

from types import SimpleNamespace

from src.address.domain.input_guard import InputGuard
from src.address.domain.schemas import ReasonCode


def _guard():
    return InputGuard(
        known_provinces={"dak lak", "ho chi minh", "thanh hoa", "binh duong"},
        known_districts={"cu mgar", "quan 1", "ngoc lac"},
        known_wards={"cuor dang", "ben nghe", "lam son", "phu cuong", "thanh my"},
    )


def test_parsed_level_without_raw_evidence_is_not_counted():
    # Parser đoán ward/district/province nhưng raw chỉ có "bi".
    parsed = SimpleNamespace(
        raw_input="bi", ward_name="Phú Cường",
        district_name="Thủ Dầu Một", province_name="Bình Dương",
    )
    result = _guard().check("bi", parsed)
    assert result.passed is False
    assert result.reason_code == ReasonCode.GENERIC_TOKEN_ONLY


def test_xom_rejected_even_if_parsed_guesses_a_ward():
    parsed = SimpleNamespace(
        raw_input="Xom", ward_name="Thanh Mỹ",
        district_name="Thanh Chương", province_name="Nghệ An",
    )
    result = _guard().check("Xom", parsed)
    assert result.passed is False


def test_soi_799_lam_son_rejected_despite_ward_name_in_street():
    # "Lam Sơn" chỉ xuất hiện sau "đường" — là tên đường, không phải xã.
    # Parser có thể điền thêm huyện/tỉnh suy từ xã Lam Sơn, nhưng các
    # tên đó không có trong raw nên không được tính -> chỉ còn 1 cấp.
    parsed = SimpleNamespace(
        raw_input="Sói 799, đường Lam Sơn", ward_name="Lam Sơn",
        district_name="Ngọc Lặc", province_name="Thanh Hóa",
    )
    result = _guard().check("Sói 799, đường Lam Sơn", parsed)
    assert result.passed is False
    assert result.reason_code == ReasonCode.MISSING_PROVINCE


def test_parsed_level_with_raw_evidence_counts():
    parsed = SimpleNamespace(
        raw_input="127 Nguyễn Trãi, xã Cuôr Đăng, huyện Cư M'gar, tỉnh Đắk Lắk",
        ward_name="Cuôr Đăng", district_name="Cư M'gar", province_name="Đắk Lắk",
    )
    result = _guard().check(
        "127 Nguyễn Trãi, xã Cuôr Đăng, huyện Cư M'gar, tỉnh Đắk Lắk", parsed)
    assert result.passed is True
    assert result.reason_code == ReasonCode.OK


def test_bi_accented_rejected():
    result = _guard().check("Bị", None)
    assert result.passed is False
