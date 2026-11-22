"""Regression tests for fix_admin_etl_merger_2025. Chua chay - CI bao ket qua.
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


def _store():
    store = AdminBoundaryStore()
    store.etl_load(_records(), version="2025.07", source="test-seed")
    return store


def test_no_hardcoded_wrong_merger_68_to_62():
    assert MERGER_2025_OLD_TO_NEW.get("68") != "62"
    assert "68" not in MERGER_2025_OLD_TO_NEW


def test_active_code_68_is_never_remapped():
    store = _store()
    assert store.resolve_old_code_2025("68") == "68"
    assert store.get_province("68").name == "Lâm Đồng"
    assert store.get_province("62").name != "Lâm Đồng"


def test_old_codes_resolve_to_active_target():
    store = _store()
    assert store.resolve_old_code_2025("67") == "68"
    assert store.resolve_old_code_2025("60") == "68"
    assert store.get_province("67").code == "68"


def test_merger_mapping_only_from_csv_and_only_for_inactive_codes():
    mapping = load_merger_csv("old_code,new_code\n67,68\n60,68\n")
    store = AdminBoundaryStore(merger_mapping=mapping)
    store.etl_load(_records(), version="2025.07", source="test-seed")
    assert store.resolve_old_code_2025("67") == "68"
    with pytest.raises(HierarchyError):
        load_merger_csv("old_code,new_code\n68,62\n62,51\n")  # chain rejected


def test_etl_load_rejects_merger_that_remaps_active_code():
    store = AdminBoundaryStore(merger_mapping={"68": "62"})
    with pytest.raises(HierarchyError):
        store.etl_load(_records(), version="2025.07", source="bad-seed")
    with pytest.raises(UnknownAdminCode):
        store.get_province("68")  # nothing was activated


def test_etl_transaction_rolls_back_on_hierarchy_error():
    store = _store()
    broken = _records() + [AdminRecord("999999", "Xã Lỗi", "ward", parent_code="0000")]
    with pytest.raises(HierarchyError):
        store.etl_load(broken, version="2025.08", source="broken")
    # Previous active version still serves, unchanged.
    assert store.active_version == "2025.07"
    assert store.get_province("68").name == "Lâm Đồng"


def test_etl_checksum_mismatch_rejected_and_match_accepted():
    store = AdminBoundaryStore()
    with pytest.raises(ChecksumMismatch):
        store.etl_load(_records(), version="v1", source="t", expected_checksum="0" * 64)
    result = store.etl_load(_records(), version="v1", source="t",
                            expected_checksum=checksum_for(_records()))
    assert result.checksum == checksum_for(_records())
    assert result.activated is True


def test_activate_and_rollback_version():
    store = _store()
    v2 = [AdminRecord("68", "Lâm Đồng", "province", old_codes=("60", "67"))]
    store.etl_load(v2, version="2025.08", source="t2")
    assert store.active_version == "2025.08"
    store.rollback_to_version("2025.07")
    assert store.active_version == "2025.07"
    assert store.get_districts_in_province("68")  # data of old version restored
    actions = [a["action"] for a in store.audit_log()]
    assert "etl_load" in actions and "rollback_activate" in actions


def test_hierarchy_lookups_use_resolved_province():
    store = _store()
    districts = store.get_districts_in_province("67")  # old code of Lam Dong
    assert [d.code for d in districts] == ["6801"]
    wards = store.get_wards_in_province("68")
    assert [w.code for w in wards] == ["680101"]


def test_load_admin_csv_parses_old_codes():
    records = load_admin_csv(
        "code,name,level,parent_code,old_codes\n"
        "68,Lâm Đồng,province,,60|67\n"
        "6801,Đà Lạt,district,68,\n"
    )
    assert records[0].old_codes == ("60", "67")
    assert records[1].parent_code == "68"


def test_unknown_code_raises_not_guesses():
    store = _store()
    with pytest.raises(UnknownAdminCode):
        store.get_province("999")
