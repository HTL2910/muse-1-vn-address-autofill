"""Allowlist tests cho fix_adapter_fail_closed_allowlist.

Chạy: python -m pytest tests/test_adapter_allowlist.py -v
Không gọi Goong/Pancake/SPX thật; router/validator/labels đều là fake.
Thêm ca mã thật không có tính prefix: 66/652/24211 vẫn phải qua
hierarchy (parent_code thuộc validator, adapter chỉ kiểm tra định dạng).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.integrations.pancake_goong_shipping_adapter import (
    InMemoryAuditStore,
    OrderAddressStatus,
    PancakeDraft,
    PancakeGoongShippingAdapter,
    gate_status_to_order_status,
    make_idempotency_key,
)


def _final(confidence=0.95, status="VERIFIED", province="66", district="660", ward="66011"):
    return SimpleNamespace(
        province="Đắk Lắk", district="Cư M'gar", ward="Cuôr Đăng",
        street="Nguyễn Trãi", house_number="127",
        province_code=province, district_code=district, ward_code=ward,
        confidence=confidence, status=status,
    )


def _adapter(validator_decision="OK", confidence=0.95, final=None, label_raises=False):
    router = SimpleNamespace(
        status=SimpleNamespace(value="OK"),
        candidates=[SimpleNamespace(province_code="66")],
        vetoed=[],
    )
    router.geocode = lambda a, b, c: router
    router.set_provider_enabled = lambda name, enabled: None
    validator = SimpleNamespace(
        validate=lambda addr, preferred_carrier="SPX": SimpleNamespace(
            decision=SimpleNamespace(value=validator_decision), errors=[], warnings=[],
            confidence_penalty=0.0)
    )
    calls = {"n": 0, "labels": []}

    class Labels:
        def create_label(self, carrier, final_address, key):
            calls["n"] += 1
            calls["labels"].append(key)
            if label_raises:
                raise RuntimeError("carrier down")
            return "LBL-1"

    def builder(router_result, draft):
        return (final if final is not None else _final(confidence=confidence), {})

    adapter = PancakeGoongShippingAdapter(router, validator, Labels(),
                                          InMemoryAuditStore(), builder)
    return adapter, calls


def _draft(order="ORD-1", raw="127 Nguyễn Trãi, xã Cuôr Đăng, Đắk Lắk"):
    return PancakeDraft(pancake_order_id=order, raw_input=raw)


def test_unknown_validator_decision_does_not_create_label():
    for decision in ["REJECTED", "INVALID", "ERROR", "SOMETHING_NEW", ""]:
        adapter, calls = _adapter(validator_decision=decision)
        record = adapter.process_draft(_draft(), "127", "127", "66")
        assert calls["n"] == 0, decision
        assert record.status in (OrderAddressStatus.BLOCKED_CARRIER_MISMATCH,
                                 OrderAddressStatus.BLOCKED_UNVERIFIED)
        assert record.carrier_label_id is None


def test_needs_review_validator_maps_to_needs_review():
    adapter, calls = _adapter(validator_decision="NEEDS_REVIEW")
    record = adapter.process_draft(_draft(), "127", "127", "66")
    assert calls["n"] == 0
    assert record.status == OrderAddressStatus.NEEDS_REVIEW


def test_ok_but_low_confidence_no_label():
    adapter, calls = _adapter(validator_decision="OK", confidence=0.60)
    record = adapter.process_draft(_draft(), "127", "127", "66")
    assert calls["n"] == 0
    assert record.status == OrderAddressStatus.BLOCKED_LOW_CONFIDENCE


def test_missing_confidence_is_blocked_not_assumed():
    final = _final(confidence=None, status="VERIFIED")
    final.confidence = None
    adapter, calls = _adapter(validator_decision="OK", final=final)
    record = adapter.process_draft(_draft(), "127", "127", "66")
    assert calls["n"] == 0
    assert record.status == OrderAddressStatus.BLOCKED_LOW_CONFIDENCE


def test_missing_ward_and_province_mismatch_no_label():
    adapter, calls = _adapter(final=_final(ward=""))
    record = adapter.process_draft(_draft(), "127", "127", "66")
    assert calls["n"] == 0
    adapter2, calls2 = _adapter(final=_final(province="22", district="220", ward="22001"))
    record2 = adapter2.process_draft(_draft(), "127", "127", "66")
    assert calls2["n"] == 0
    assert record2.status == OrderAddressStatus.BLOCKED_CARRIER_MISMATCH


def test_real_codes_without_prefix_property_still_pass():
    # Mã thật không có tính prefix: 66 / 652 / 24211 vẫn hợp lệ ở adapter.
    adapter, calls = _adapter(final=_final(province="66", district="652", ward="24211"))
    record = adapter.process_draft(_draft(), "127", "127", "66")
    assert calls["n"] == 1
    assert record.status == OrderAddressStatus.COMMITTED


def test_ok_verified_complete_creates_label_once_and_replays():
    adapter, calls = _adapter()
    record = adapter.process_draft(_draft(), "127", "127", "66")
    assert record.status == OrderAddressStatus.COMMITTED
    assert record.carrier_label_id == "LBL-1"
    record2 = adapter.process_draft(_draft(raw="số khác, xã Cuôr Đăng, Đắk Lắk"), "x", "x", "66")
    assert record2.status == OrderAddressStatus.COMMITTED
    assert record2.carrier_label_id == "LBL-1"
    assert calls["n"] == 1
    assert make_idempotency_key("ORD-1", "a") == make_idempotency_key("ORD-1", "b")


def test_customer_confirmed_cannot_change_province_code():
    adapter, calls = _adapter()
    record = adapter.process_draft(
        _draft(), "127", "127", "66",
        customer_confirmed_address={"province_code": "22", "district_code": "220",
                                    "ward_code": "22001", "street": "Đường mới"})
    assert calls["n"] == 1
    assert record.final_address["province_code"] == "66"
    assert record.final_address["street"] == "Đường mới"


def test_create_label_exception_saves_label_failed_audit():
    adapter, calls = _adapter(label_raises=True)
    record = adapter.process_draft(_draft(), "127", "127", "66")
    assert record.status == OrderAddressStatus.LABEL_FAILED
    assert record.carrier_label_id is None
    assert adapter._audit.get_by_idempotency_key(record.idempotency_key) is not None


def test_audit_to_json_does_not_leak_raw_input():
    adapter, _ = _adapter()
    record = adapter.process_draft(_draft(), "127", "127", "66")
    payload = json.loads(PancakeGoongShippingAdapter.audit_to_json(record))
    assert "127 Nguyễn Trãi" not in json.dumps(payload, ensure_ascii=False)
    assert payload["raw_input_sha256"]
    assert "raw_input" not in payload


def test_gate_status_mapping_is_unified():
    assert gate_status_to_order_status("VERIFIED") == "COMMITTED"
    assert gate_status_to_order_status("NEED_CONFIRMATION") == "NEEDS_REVIEW"
    assert gate_status_to_order_status("REJECTED_INSUFFICIENT") == "BLOCKED_UNVERIFIED"
    assert gate_status_to_order_status("UNKNOWN_NEW") == "BLOCKED_UNVERIFIED"
