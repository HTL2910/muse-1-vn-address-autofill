"""Tests for fix_adapter_idempotency_audit_pii. Chua chay - CI bao ket qua.
Run: python -m pytest tests/test_adapter_idempotency_audit.py -q
"""
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List

import pytest

from src.integrations.pancake_goong_shipping_adapter import (
    AuditRecord,
    InMemoryAuditStore,
    OrderAddressStatus,
    PancakeDraft,
    PancakeGoongShippingAdapter,
    make_idempotency_key,
)
from src.security.address_pii_protection import decrypt_pii, scrub_pii, sha256_hex

RAW = "Nguyen Van A 0901234567 so 12 duong Lam Son phuong 2 Tan Binh Ho Chi Minh"


def _record(order="ORD-1", raw=RAW, status=OrderAddressStatus.BLOCKED_UNVERIFIED,
            label=None, reason="TEST"):
    return AuditRecord(
        make_idempotency_key(order), order, raw,
        {"phone": "0901234567", "suggestion": "x"},
        {"candidates": [{"detail": "so 12 duong Lam Son"}]},
        {}, {"province_code": "79", "district_code": "7901", "ward_code": "790102",
             "detail": "so 12 duong Lam Son", "phone": "0901234567"},
        None, status, carrier="SPX", carrier_label_id=label, reason_code=reason)


# --- idempotency anchored to the order only --------------------------------

def test_key_depends_only_on_order_id():
    k1 = make_idempotency_key("ORD-1", raw_input="dia chi cu", phone_ref="0901")
    k2 = make_idempotency_key("ORD-1", raw_input="dia chi da sua hoan toan khac", phone_ref="0999")
    k3 = make_idempotency_key("ORD-1")
    assert k1 == k2 == k3
    assert make_idempotency_key("ORD-2") != k1


# --- audit append-only + hash chain ------------------------------------------

def test_audit_is_append_only_and_keeps_blocked_history():
    store = InMemoryAuditStore()
    r1 = _record(status=OrderAddressStatus.BLOCKED_UNVERIFIED, reason="NO_CANDIDATES")
    r2 = _record(status=OrderAddressStatus.NEEDS_REVIEW, reason="VALIDATOR_NEEDS_REVIEW")
    r3 = _record(status=OrderAddressStatus.COMMITTED, label="LBL-1", reason="OK")
    store.save(r1); store.save(r2); store.save(r3)
    history = store.history(r1.idempotency_key)
    assert [h.status for h in history] == [
        OrderAddressStatus.BLOCKED_UNVERIFIED,
        OrderAddressStatus.NEEDS_REVIEW,
        OrderAddressStatus.COMMITTED,
    ]
    assert store.get_by_idempotency_key(r1.idempotency_key).status == OrderAddressStatus.COMMITTED
    # Earlier entries were not mutated by later saves.
    assert history[0].reason_code == "NO_CANDIDATES"
    assert history[0].entry_hash and history[1].prev_hash == history[0].entry_hash
    assert history[2].prev_hash == history[1].entry_hash
    assert store.verify_chain() is True


def test_audit_chain_detects_tampering():
    store = InMemoryAuditStore()
    store.save(_record())
    store.history()[0].reason_code = "TAMPERED"
    assert store.verify_chain() is False


def test_terminal_latest_view_is_not_displaced_but_attempt_is_audited():
    store = InMemoryAuditStore()
    committed = _record(status=OrderAddressStatus.COMMITTED, label="LBL-1", reason="OK")
    store.save(committed)
    store.save(_record(status=OrderAddressStatus.BLOCKED_UNVERIFIED, reason="LATE_BLOCK"))
    assert store.get_by_idempotency_key(committed.idempotency_key).status == OrderAddressStatus.COMMITTED
    assert len(store.history(committed.idempotency_key)) == 2
    assert store.verify_chain() is True


# --- PII at rest ----------------------------------------------------------------

def test_stored_audit_has_no_plaintext_pii_and_encrypted_raw_is_recoverable():
    store = InMemoryAuditStore()
    rec = _record()
    store.save(rec)
    stored = store.history()[0]
    assert stored.raw_input == ""
    assert "0901234567" not in json.dumps(stored.provider_responses, ensure_ascii=False)
    assert "0901234567" not in json.dumps(stored.final_address, ensure_ascii=False)
    assert "Lam Son" not in json.dumps(stored.final_address, ensure_ascii=False)
    assert stored.raw_input_sha256 == sha256_hex(RAW) == hashlib.sha256(RAW.encode()).hexdigest()
    assert stored.raw_input_encrypted and RAW not in stored.raw_input_encrypted
    assert decrypt_pii(stored.raw_input_encrypted) == RAW
    # Administrative codes survive scrubbing for dispute tracing.
    assert stored.final_address["province_code"] == "79"


def test_audit_to_json_never_contains_plaintext():
    rec = _record(status=OrderAddressStatus.COMMITTED, label="LBL-1", reason="OK")
    InMemoryAuditStore().save(rec)
    payload = PancakeGoongShippingAdapter.audit_to_json(rec)
    assert RAW not in payload
    assert "0901234567" not in payload
    assert "Lam Son" not in payload
    data = json.loads(payload)
    assert data["raw_input_sha256"] == sha256_hex(RAW)
    assert data["entry_hash"] == rec.entry_hash


def test_scrub_pii_keeps_codes():
    scrubbed = scrub_pii({"province_code": "66", "detail": "Cuor Dang",
                          "customer": {"phone": "0901234567", "ward_code": "660101"}})
    assert scrubbed["province_code"] == "66"
    assert scrubbed["detail"] == "***"
    assert scrubbed["customer"]["ward_code"] == "660101"
    assert "0901234567" not in json.dumps(scrubbed)


# --- end-to-end: corrected address cannot create a second label ------------------

@dataclass
class _Candidate:
    province_code: str = "79"
    district_code: str = "7901"
    ward_code: str = "790102"


@dataclass
class _RouterResult:
    status: str = "OK"
    candidates: List[Any] = field(default_factory=lambda: [_Candidate()])
    vetoed: List[Any] = field(default_factory=list)


class _Router:
    def geocode(self, a, d, anchored=None):
        return _RouterResult()
    def set_provider_enabled(self, name, enabled):
        pass


class _Validator:
    def validate(self, addr, preferred_carrier="SPX"):
        return type("V", (), {"decision": "OK", "errors": [], "warnings": [],
                              "confidence_penalty": 0.0})()


class _Labels:
    def __init__(self):
        self.calls = []
    def create_label(self, carrier, final_address, idempotency_key):
        self.calls.append(idempotency_key)
        return f"LBL-{len(self.calls)}"


@dataclass
class _Final:
    province_code: str = "79"
    district_code: str = "7901"
    ward_code: str = "790102"
    confidence: float = 0.95
    status: str = "VERIFIED"
    street: str = ""
    detail: str = ""


def test_corrected_address_replays_committed_label_once():
    labels = _Labels()
    adapter = PancakeGoongShippingAdapter(
        _Router(), _Validator(), labels, InMemoryAuditStore(),
        lambda router_result, draft: (_Final(), None))
    draft1 = PancakeDraft("ORD-9", RAW, {}, "0901234567")
    first = adapter.process_draft(draft1, "a", "a", anchored_province_code="79")
    assert first.status == OrderAddressStatus.COMMITTED
    # Customer "corrects" the raw address / phone - same order, same key.
    draft2 = PancakeDraft("ORD-9", "dia chi da sua khac han 0909999999", {}, "0909999999")
    second = adapter.process_draft(draft2, "b", "b", anchored_province_code="79")
    assert second.idempotency_key == first.idempotency_key
    assert second.status == OrderAddressStatus.COMMITTED
    assert labels.calls == [first.idempotency_key]  # exactly one label created
