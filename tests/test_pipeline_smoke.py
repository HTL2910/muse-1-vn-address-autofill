"""Import smoke + golden hồi quy tối thiểu cho fix_missing_pipeline_core.

Chạy: python -m pytest tests/test_pipeline_smoke.py -v
Không gọi Goong/Pancake/SPX thật; router=None nên pipeline chỉ dùng
Canonical Admin DB nội bộ trong test.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.address.api.address_gateway import AddressGateway
from src.address.domain import confidence_gate, fuzzy_matcher, input_guard, province_anchor, ranker, schemas, structured_parser, validator
from src.address.domain.province_anchor import AdminIndex, AdminUnit
from src.address.services.address_service import AddressService


def _test_index() -> AdminIndex:
    units = [
        AdminUnit("66", "Đắk Lắk", "province", None, ("dak lak",), 12.7, 108.0),
        AdminUnit("660", "Cư M'gar", "district", "66", (), 12.8, 108.1),
        AdminUnit("66011", "Cuôr Đăng", "ward", "660", ("cuor dang",), 12.8, 108.1),
        AdminUnit("64", "Gia Lai", "province", None, ("gia lai",), 13.9, 108.0),
        AdminUnit("641", "Mang Yang", "district", "64", (), 13.9, 108.0),
        AdminUnit("64101", "Đăk Yă", "ward", "641", ("dak ya",), 13.9, 108.0),
        AdminUnit("22", "Hải Dương", "province", None, ("hai duong",), 20.9, 106.3),
        AdminUnit("220", "Chí Linh", "district", "22", (), 21.1, 106.3),
        AdminUnit("22001", "Phả Lại", "ward", "220", ("pha lai",), 21.1, 106.3),
        AdminUnit("40", "Nghệ An", "province", None, (), 19.0, 105.0),
        AdminUnit("400", "Thanh Chương", "district", "40", (), 19.0, 105.0),
        AdminUnit("40001", "Thanh Mỹ", "ward", "400", (), 19.0, 105.0),
        AdminUnit("04", "Cao Bằng", "province", None, (), 22.6, 106.2),
        AdminUnit("040", "Hòa An", "district", "04", (), 22.6, 106.2),
        AdminUnit("04001", "Hợp Giang", "ward", "040", (), 22.6, 106.2),
        AdminUnit("33", "Hưng Yên", "province", None, (), 20.8, 106.0),
        AdminUnit("330", "Kim Động", "district", "33", (), 20.8, 106.0),
        AdminUnit("33001", "Hiến Nam", "ward", "330", (), 20.8, 106.0),
        AdminUnit("74", "Bình Dương", "province", None, (), 11.0, 106.6),
        AdminUnit("740", "Thủ Dầu Một", "district", "74", (), 11.0, 106.6),
        AdminUnit("74001", "Phú Cường", "ward", "740", (), 11.0, 106.6),
    ]
    return AdminIndex(units)


def _service() -> AddressService:
    return AddressService(admin_index=_test_index(), router=None)


def test_imports_all_pipeline_modules():
    for mod in (schemas, structured_parser, province_anchor, input_guard,
                fuzzy_matcher, ranker, validator, confidence_gate):
        assert mod is not None


def test_cuor_dang_dak_lak_never_hai_duong():
    result = _service().resolve("xã Cuôr Đăng, Đắk Lắk")
    assert result.contract.province_code == "66"
    assert result.contract.province_code != "22"
    assert result.contract.ward_code == "66011"


def test_valid_full_address_verified():
    result = _service().resolve("127 Nguyễn Trãi, xã Cuôr Đăng, huyện Cư M'gar, tỉnh Đắk Lắk")
    assert result.contract.status.value == "VERIFIED"
    assert result.contract.is_shippable is True
    assert result.contract.house_number == "127"


def test_short_and_number_only_rejected():
    for raw in ["73", "127", "bi", "Bị", "Xom"]:
        result = _service().resolve(raw)
        assert result.contract.status.value == "REJECTED", raw


def test_soi_799_lam_son_rejected_for_missing_admin_levels():
    result = _service().resolve("Sói 799, đường Lam Sơn")
    assert result.contract.status.value in ("REJECTED", "NEEDS_REVIEW")
    assert result.contract.is_shippable is False


def test_forbidden_default_never_returned():
    result = _service().resolve("Xã Đăk Yă, Mang Yang, Gia Lai")
    assert result.contract.status.value == "REJECTED"
    assert "Đăk Yă" not in result.contract.to_display() or result.contract.status.value == "REJECTED"


def test_gateway_mapping():
    gateway = AddressGateway(_service())
    out = gateway.resolve_address("127 Nguyễn Trãi, xã Cuôr Đăng, huyện Cư M'gar, tỉnh Đắk Lắk")
    assert out["status"] == "VERIFIED"
    assert out["is_shippable"] is True
    out2 = gateway.resolve_address("73")
    assert out2["status"] == "REJECTED_INSUFFICIENT"
    assert out2["is_shippable"] is False
