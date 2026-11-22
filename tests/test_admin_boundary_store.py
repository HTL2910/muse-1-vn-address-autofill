"""Regression tests for fix_admin_etl_merger_2025 (v3). Chua chay - CI bao.
Run: python -m pytest tests/test_admin_boundary_store.py -q
"""
import pytest

from src.address.infrastructure.admin_boundary_store import (
    MERGER_2025_OLD_TO_NEW,
    AdminBoundaryStore,
    AdminRecord,
    ChecksumMismatch,
    HierarchyError,
    UnknownAdminCode,
    checksum_for,
    get_default_store,
    load_admin_csv,
    load_merger_csv,
)


def _records():
    return [
        AdminRecord("68", "Lâm Đồng", "province", old_codes=("60", "67")),
        AdminRecord("62", "Kon Tum (old seed)", "province"),
        AdminRecord("66", "Đắk Lắk", "province"),
        AdminRecord("6601", "Buôn Ma Thuột", "district", parent_code="66"),
        AdminRecord("660101", "Tân Lập", "ward", parent_code="6601"),
        AdminRecord("6801", "Đà Lạt", "district", parent_code="68"),
        AdminRecord("680101", "Phường 1", "ward", parent_code="6801"),
    ]


def _bare_store():
    store = AdminBoundaryStore(seed=False)
    store.etl_load(_records(), version="2025.07", source="test-seed")
    return store


def test_no_hardcoded_wrong_merger_68_to_62():
    assert MERGER_2025_OLD_TO_NEW.get("68") != "62"
    assert "68" not in MERGER_2025_OLD_TO_NEW


def test_active_code_68_is_never_remapped():
    store = _bare_store()
    assert store.resolve_old_code_2025("68") == "68"
    assert store.get_province("68").name == "Lâm Đồng"


def test_old_codes_resolve_to_active_target():
    store = _bare_store()
    assert store.resolve_old_code_2025("67") == "68"
    assert store.get_province("67").code == "68"


def test_merger_mapping_only_from_csv():
    mapping = load_merger_csv("old_code,new_code\n67,68\n60,68\n")
    store = AdminBoundaryStore(merger_mapping=mapping, seed=False)
    store.etl_load(_records(), version="2025.07", source="test-seed")
    assert store.resolve_old_code_2025("67") == "68"
    with pytest.raises(HierarchyError):
        load_merger_csv("old_code,new_code\n68,62\n62,51\n")


def test_etl_merger_active_overlap_is_warning_not_load_failure():
    store = AdminBoundaryStore(merger_mapping={"68": "62"}, seed=False)
    result = store.etl_load(_records(), version="2025.07", source="seed")
    assert result.activated is True
    assert store.resolve_old_code_2025("68") == "68"


def test_etl_transaction_rolls_back_on_hierarchy_error():
    store = _bare_store()
    broken = _records() + [AdminRecord("999999", "Xã Lỗi", "ward", parent_code="0000")]
    with pytest.raises(HierarchyError):
        store.etl_load(broken, version="2025.08", source="broken")
    assert store.active_version == "2025.07"
    assert store.get_province("68").name == "Lâm Đồng"


def test_etl_checksum_and_idempotent_reload():
    store = AdminBoundaryStore(seed=False)
    with pytest.raises(ChecksumMismatch):
        store.etl_load(_records(), version="v1", source="t", expected_checksum="0" * 64)
    result = store.etl_load(_records(), version="v1", source="t",
                            expected_checksum=checksum_for(_records()))
    assert result.activated is True
    assert store.etl_load(_records(), version="v1", source="rerun").activated is True


def test_activate_and_rollback_version():
    store = _bare_store()
    store.etl_load([AdminRecord("68", "Lâm Đồng", "province", old_codes=("60", "67"))],
                   version="2025.08", source="t2")
    assert store.active_version == "2025.08"
    store.rollback_to_version("2025.07")
    assert store.get_districts_in_province("68")


def test_seed_covers_golden_cases():
    store = AdminBoundaryStore()
    assert store.get_province("64").name == "Gia Lai"
    assert store.find_by_name("Đăk Yă", level="ward", parent_code="6401").code == "640101"
    assert store.find_by_name("Cuôr Đăng", level="ward", parent_code="6601").code == "660101"
    assert store.find_by_name("Cuôr Đăng", level="ward", parent_code="3401") is None
    assert store.get_province("79").name == "Hồ Chí Minh"
    assert store.get_province("dak lak").code == "66"
    assert get_default_store().get_province("64").code == "64"


def test_hierarchy_check_blocks_cross_province_pairing():
    store = AdminBoundaryStore()
    assert store.is_valid_hierarchy("66", "6601", "660101") is True
    # Cuor Dang (Dak Lak) paired with Pha Lai / Chi Linh / Hai Duong codes
    assert store.is_valid_hierarchy("66", "3401", "340101") is False
    assert store.is_valid_hierarchy("34", "3401", "660101") is False


def test_compatibility_constructors_and_alias_fields():
    # Other experts' wiring styles must not crash the store.
    assert AdminBoundaryStore({"67": "68"}).resolve_old_code_2025("67") == "68"
    assert AdminBoundaryStore(_records(), seed=False).get_province("66").name == "Đắk Lắk"
    rec = AdminRecord(code="66", name="Đắk Lắk", type="province", slug="dak-lak")
    assert rec.level == "province" and rec.extra["slug"] == "dak-lak"
    rec2 = AdminRecord.from_row({"code": "6601", "name": "Krông Ana",
                                 "level": "district", "province_code": "66"})
    assert rec2.parent_code == "66"
    store = AdminBoundaryStore(seed=False)
    loaded = store.load_admin_csv(
        "code,name,level,parent_code\n66,Đắk Lắk,province,\n6601,Krông Ana,district,66\n")
    assert len(loaded) == 2 and store.get_province("66").name == "Đắk Lắk"


def test_load_admin_csv_module_parse_only():
    records = load_admin_csv(
        "code,name,level,parent_code,old_codes\n68,Lâm Đồng,province,,60|67\n")
    assert records[0].old_codes == ("60", "67")


def test_unknown_code_raises_not_guesses():
    with pytest.raises(UnknownAdminCode):
        _bare_store().get_province("999")
