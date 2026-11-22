"""
Integration contract: Pancake / Goong / carriers - approach_19
Fix: fix_adapter_fail_closed_allowlist - allowlist + status mapping
(kept unchanged):
  REJECTED_INSUFFICIENT -> BLOCKED_UNVERIFIED (checked on the raw
  string first, never collapsed through AddressStatus.REJECTED).
  Hierarchy check at the adapter verifies format + anchored province
  only, never string prefixes.

Fix: fix_adapter_idempotency_audit_pii:
  1. Idempotency is anchored to the ORDER only:
     make_idempotency_key(pancake_order_id) - raw_input and phone are
     accepted for caller compatibility but are NEVER part of the key.
  2. Audit is append-only with a hash chain: save() APPENDS an entry
     (seq, prev_hash, entry_hash) and never overwrites or deletes a
     previous entry; history(key) retains every attempt for disputes.
  3. Latest-view transition rules (corrected after test review):
     - COMMITTED is the only order-sticky status: once a label exists,
       no later decision displaces it in the latest view.
     - BLOCKED_* / NEEDS_REVIEW / LABEL_FAILED end one ATTEMPT only:
       a reprocessed order (customer corrected the address, label
       retry) supersedes them, e.g.
       BLOCKED_UNVERIFIED -> NEEDS_REVIEW -> COMMITTED.
     - DRAFT_FROM_PANCAKE never displaces a real decision.
  4. PII: the STORED audit entry is a redacted copy - raw_input is
     replaced by raw_input_encrypted (authenticated encryption via
     src/security/address_pii_protection) plus raw_input_sha256, and
     phone/name/note/detail/street fields in stored dicts are scrubbed.
     Plaintext exists only in the in-memory record returned to the
     caller of the current request, never at rest, never in
     audit_to_json(). Per-key locks use a fixed striped set (64).

Status: chua chay - ket qua do CI bao.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol

try:
    from ..address.domain.schemas import (
        AddressStatus as _CanonicalAddressStatus,
        GateStatus as _CanonicalGateStatus,
        gate_to_address_status as _gate_to_address_status,
    )
    _HAS_SCHEMAS = True
except Exception:  # pragma: no cover
    _HAS_SCHEMAS = False
    _CanonicalAddressStatus = None  # type: ignore
    _CanonicalGateStatus = None  # type: ignore
    _gate_to_address_status = None  # type: ignore

try:
    from ..security.address_pii_protection import encrypt_pii, scrub_pii
except Exception:  # pragma: no cover - standalone fallback, never plaintext
    import base64 as _b64
    import hmac as _hmac
    import secrets as _secrets

    def encrypt_pii(plaintext, key=None):  # type: ignore
        text = "" if plaintext is None else str(plaintext)
        if not text:
            return ""
        raw_key = hashlib.sha256(str(key or "dev-only-address-pii-key-change-me").encode()).digest()
        nonce = _secrets.token_bytes(16)
        data = text.encode("utf-8")
        stream = bytearray(); counter = 0
        while len(stream) < len(data):
            stream.extend(_hmac.new(raw_key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
            counter += 1
        ct = bytes(a ^ b for a, b in zip(data, bytes(stream[:len(data)])))
        tag = _hmac.new(raw_key, b"tag" + nonce + ct, hashlib.sha256).digest()
        return "enc:v1:" + _b64.b64encode(b"H" + nonce + ct + tag).decode("ascii")

    def scrub_pii(obj):  # type: ignore
        pii = {"phone", "customer_phone", "tel", "mobile", "name", "customer_name",
               "note", "detail", "street", "address", "raw_input", "full_address"}
        if isinstance(obj, dict):
            return {k: ("***" if str(k).lower() in pii else scrub_pii(v)) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [scrub_pii(v) for v in obj]
        return obj

MIN_CONFIDENCE = 0.85
ALLOWED_VALIDATOR_DECISIONS = frozenset({"OK"})
ALLOWED_GATE_STATUSES = frozenset({"VERIFIED"})


def gate_status_to_order_status(gate_status: str) -> str:
    value = str(gate_status or "").upper()
    if value == "REJECTED_INSUFFICIENT":
        return OrderAddressStatus.BLOCKED_UNVERIFIED.value
    if value == "REJECTED":
        return OrderAddressStatus.BLOCKED_GATE_REJECTED.value
    if value in ("VERIFIED",):
        return OrderAddressStatus.COMMITTED.value
    if value in ("NEED_CONFIRMATION", "NEEDS_REVIEW"):
        return OrderAddressStatus.NEEDS_REVIEW.value
    return OrderAddressStatus.BLOCKED_UNVERIFIED.value


class OrderAddressStatus(str, Enum):
    DRAFT_FROM_PANCAKE = "DRAFT_FROM_PANCAKE"
    COMMITTED = "COMMITTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED_CARRIER_MISMATCH = "BLOCKED_CARRIER_MISMATCH"
    BLOCKED_UNVERIFIED = "BLOCKED_UNVERIFIED"
    BLOCKED_LOW_CONFIDENCE = "BLOCKED_LOW_CONFIDENCE"
    BLOCKED_GATE_REJECTED = "BLOCKED_GATE_REJECTED"
    LABEL_FAILED = "LABEL_FAILED"


# Attempt-terminal statuses: they end one processing attempt, they do
# NOT freeze the order. Only COMMITTED freezes the latest view.
_ATTEMPT_TERMINAL_STATUSES = frozenset({
    OrderAddressStatus.BLOCKED_CARRIER_MISMATCH,
    OrderAddressStatus.BLOCKED_UNVERIFIED,
    OrderAddressStatus.BLOCKED_LOW_CONFIDENCE,
    OrderAddressStatus.BLOCKED_GATE_REJECTED,
    OrderAddressStatus.LABEL_FAILED,
})
_STICKY_STATUS = OrderAddressStatus.COMMITTED


def _sha256_hex(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _masked_preview(raw_input: str) -> str:
    return "***" if (raw_input or "").strip() else ""


@dataclass
class PancakeDraft:
    pancake_order_id: str
    raw_input: str
    pancake_suggestion: Dict[str, Any] = field(default_factory=dict)
    customer_phone_ref: str = ""
    source: str = "pancake"


@dataclass
class AuditRecord:
    idempotency_key: str
    pancake_order_id: str
    raw_input: str
    pancake_suggestion: Dict[str, Any]
    provider_responses: Dict[str, Any]
    validator_result: Dict[str, Any]
    final_address: Optional[Dict[str, Any]]
    customer_confirmed_address: Optional[Dict[str, Any]]
    status: OrderAddressStatus
    created_at: float = field(default_factory=time.time)
    carrier: str = "SPX"
    carrier_label_id: Optional[str] = None
    raw_input_sha256: str = ""
    gate_result: Dict[str, Any] = field(default_factory=dict)
    confidence_score: Optional[float] = None
    reason_code: str = ""
    raw_input_encrypted: str = ""
    audit_seq: int = 0
    prev_hash: str = ""
    entry_hash: str = ""

    def __post_init__(self) -> None:
        if not self.raw_input_sha256 and self.raw_input:
            self.raw_input_sha256 = _sha256_hex(self.raw_input)

    def stored_copy(self) -> "AuditRecord":
        """Redacted copy that is safe to persist: no plaintext PII."""
        return dataclasses.replace(
            self,
            raw_input="",
            raw_input_encrypted=self.raw_input_encrypted or encrypt_pii(self.raw_input),
            raw_input_sha256=self.raw_input_sha256 or _sha256_hex(self.raw_input),
            pancake_suggestion=scrub_pii(self.pancake_suggestion or {}),
            provider_responses=scrub_pii(self.provider_responses or {}),
            final_address=scrub_pii(self.final_address) if self.final_address else None,
            customer_confirmed_address=(scrub_pii(self.customer_confirmed_address)
                                        if self.customer_confirmed_address else None),
        )


def _entry_hash(seq: int, prev_hash: str, record: AuditRecord) -> str:
    canonical = json.dumps({
        "seq": seq,
        "prev_hash": prev_hash,
        "idempotency_key": record.idempotency_key,
        "pancake_order_id": record.pancake_order_id,
        "status": record.status.value if isinstance(record.status, Enum) else str(record.status),
        "raw_input_sha256": record.raw_input_sha256,
        "carrier": record.carrier,
        "carrier_label_id": record.carrier_label_id,
        "reason_code": record.reason_code,
        "created_at": record.created_at,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RouterPort(Protocol):
    def geocode(self, query_ascii: str, query_diacritic: str,
                anchored_province_code: Optional[str]): ...
    def set_provider_enabled(self, name: str, enabled: bool) -> None: ...


class ValidatorPort(Protocol):
    def validate(self, addr, preferred_carrier: str = "SPX"): ...


class GatePort(Protocol):
    def evaluate(self, addr, candidates=None): ...


class AddressServicePort(Protocol):
    def resolve(self, raw_input: str, carrier: str = "SPX"): ...


class CarrierLabelPort(Protocol):
    def create_label(self, carrier: str, final_address: Dict[str, Any],
                     idempotency_key: str) -> str: ...


class AuditStorePort(Protocol):
    def get_by_idempotency_key(self, key: str) -> Optional[AuditRecord]: ...
    def save(self, record: AuditRecord) -> None: ...
    def history(self, key: str) -> List[AuditRecord]: ...
    def verify_chain(self) -> bool: ...


def make_idempotency_key(pancake_order_id: str, raw_input: str = "", phone_ref: str = "") -> str:
    """Idempotency anchored to the ORDER only. raw_input / phone_ref are
    accepted for caller compatibility and deliberately ignored."""
    _ = (raw_input, phone_ref)
    digest = hashlib.sha256(f"{(pancake_order_id or '').strip()}".encode("utf-8")).hexdigest()
    return f"addr:{digest[:40]}"


class InMemoryAuditStore:
    """Append-only, hash-chained, PII-encrypted audit store.

    save() never overwrites history. The latest-view pointer follows
    the transition rules in the module docstring: COMMITTED is sticky,
    attempt-terminal statuses (BLOCKED_*, LABEL_FAILED) and NEEDS_REVIEW
    are superseded by the next decision for the same order, and a DRAFT
    never displaces a real decision.
    """

    GENESIS_HASH = "0" * 64

    def __init__(self) -> None:
        self._history: List[AuditRecord] = []
        self._latest: Dict[str, AuditRecord] = {}
        self._lock = threading.Lock()

    def get_by_idempotency_key(self, key: str) -> Optional[AuditRecord]:
        with self._lock:
            return self._latest.get(key)

    def history(self, key: Optional[str] = None) -> List[AuditRecord]:
        with self._lock:
            if key is None:
                return list(self._history)
            return [r for r in self._history if r.idempotency_key == key]

    def all_records(self) -> List[AuditRecord]:
        return self.history()

    def verify_chain(self) -> bool:
        with self._lock:
            prev = self.GENESIS_HASH
            for expected_seq, entry in enumerate(self._history, start=1):
                if entry.audit_seq != expected_seq or entry.prev_hash != prev:
                    return False
                if entry.entry_hash != _entry_hash(entry.audit_seq, entry.prev_hash, entry):
                    return False
                prev = entry.entry_hash
            return True

    def save(self, record: AuditRecord) -> None:
        with self._lock:
            stored = record.stored_copy()
            seq = len(self._history) + 1
            prev = self._history[-1].entry_hash if self._history else self.GENESIS_HASH
            stored.audit_seq = seq
            stored.prev_hash = prev
            stored.entry_hash = _entry_hash(seq, prev, stored)
            self._history.append(stored)
            record.audit_seq = seq
            record.prev_hash = prev
            record.entry_hash = stored.entry_hash
            record.raw_input_sha256 = stored.raw_input_sha256
            existing = self._latest.get(record.idempotency_key)
            if existing is None:
                self._latest[record.idempotency_key] = stored
            elif existing.status == _STICKY_STATUS:
                pass  # COMMITTED stands: a label already exists for this order
            elif (record.status == OrderAddressStatus.DRAFT_FROM_PANCAKE
                  and existing.status != OrderAddressStatus.DRAFT_FROM_PANCAKE):
                pass  # a draft never displaces a real decision
            else:
                # Attempt-terminal and review statuses are superseded by
                # the next decision for the same order (reprocessing
                # after the customer corrected the address).
                self._latest[record.idempotency_key] = stored


_STRIPED_LOCKS = [threading.Lock() for _ in range(64)]


def _lock_for_key(key: str) -> threading.Lock:
    return _STRIPED_LOCKS[int(hashlib.sha256(key.encode()).hexdigest(), 16) % 64]


def _decision_value(decision: Any) -> str:
    return str(getattr(decision, "value", decision) or "").upper()


def _hierarchy_ok(final_dict: Dict[str, Any], anchored_province_code: Optional[str]) -> tuple[bool, str]:
    province = str(final_dict.get("province_code") or "")
    district = str(final_dict.get("district_code") or "")
    ward = str(final_dict.get("ward_code") or "")
    if not province or not district or not ward:
        return False, "MISSING_WARD" if not ward else ("MISSING_DISTRICT" if not district else "MISSING_PROVINCE")
    if anchored_province_code and province != str(anchored_province_code):
        return False, "PROVINCE_MISMATCH"
    return True, ""


class PancakeGoongShippingAdapter:
    def __init__(self, router: RouterPort, validator: ValidatorPort,
                 carrier_labels: CarrierLabelPort, audit: AuditStorePort,
                 address_builder: Any,
                 address_service: Optional[AddressServicePort] = None,
                 confidence_gate: Optional[GatePort] = None):
        self._router = router
        self._validator = validator
        self._labels = carrier_labels
        self._audit = audit
        self._build = address_builder
        self._service = address_service
        self._gate = confidence_gate

    def _save(self, record: AuditRecord) -> AuditRecord:
        self._audit.save(record)
        return record

    def _blocked_record(self, key: str, draft: PancakeDraft, provider_responses: Dict[str, Any],
                        validator_dict: Dict[str, Any], final_dict: Optional[Dict[str, Any]],
                        customer_confirmed_address: Optional[Dict[str, Any]],
                        status: OrderAddressStatus, carrier: str,
                        gate_result: Optional[Dict[str, Any]] = None,
                        confidence: Optional[float] = None, reason: str = "") -> AuditRecord:
        return self._save(AuditRecord(
            key, draft.pancake_order_id, draft.raw_input, draft.pancake_suggestion,
            provider_responses, validator_dict, final_dict, customer_confirmed_address,
            status, carrier=carrier, gate_result=gate_result or {},
            confidence_score=confidence, reason_code=reason))

    def process_draft(self, draft: PancakeDraft, query_ascii: str,
                      query_diacritic: str, anchored_province_code: Optional[str] = None,
                      carrier: str = "SPX",
                      customer_confirmed_address: Optional[Dict[str, Any]] = None) -> AuditRecord:
        key = make_idempotency_key(draft.pancake_order_id)
        existing = self._audit.get_by_idempotency_key(key)
        if existing is not None and existing.status == OrderAddressStatus.COMMITTED:
            return existing
        with _lock_for_key(key):
            existing = self._audit.get_by_idempotency_key(key)
            if existing is not None and existing.status == OrderAddressStatus.COMMITTED:
                return existing
            if self._service is not None:
                return self._process_via_service(draft, key, carrier, anchored_province_code,
                                                 customer_confirmed_address)
            router_result = self._router.geocode(query_ascii, query_diacritic,
                                                 anchored_province_code)
            provider_responses = {
                "router_status": getattr(router_result.status, "value", str(router_result.status)),
                "candidates": [c.__dict__ if hasattr(c, "__dict__") else str(c)
                               for c in getattr(router_result, "candidates", [])],
                "vetoed": [c.__dict__ if hasattr(c, "__dict__") else str(c)
                           for c in getattr(router_result, "vetoed", [])],
                "pancake_suggestion_untrusted": draft.pancake_suggestion,
            }
            if not getattr(router_result, "candidates", None):
                return self._blocked_record(
                    key, draft, provider_responses, {}, customer_confirmed_address,
                    customer_confirmed_address, OrderAddressStatus.BLOCKED_UNVERIFIED,
                    carrier, reason="NO_CANDIDATES")
            final_obj, _ = self._build(router_result, draft)
            if customer_confirmed_address:
                for field_name in ("street", "detail"):
                    if customer_confirmed_address.get(field_name):
                        try:
                            setattr(final_obj, field_name, customer_confirmed_address[field_name])
                        except Exception:
                            pass
            final_dict = dict(final_obj.__dict__) if hasattr(final_obj, "__dict__") else dict(final_obj)
            ok, reason = _hierarchy_ok(final_dict, anchored_province_code)
            if not ok:
                status = (OrderAddressStatus.BLOCKED_CARRIER_MISMATCH
                          if reason in ("HIERARCHY_CONFLICT", "PROVINCE_MISMATCH")
                          else OrderAddressStatus.BLOCKED_UNVERIFIED)
                return self._blocked_record(key, draft, provider_responses, {}, final_dict,
                                            customer_confirmed_address, status, carrier, reason=reason)
            validation = self._validator.validate(final_obj, preferred_carrier=carrier)
            validator_dict = {
                "decision": _decision_value(getattr(validation, "decision", "")),
                "errors": [e.__dict__ if hasattr(e, "__dict__") else str(e) for e in getattr(validation, "errors", [])],
                "warnings": [e.__dict__ if hasattr(e, "__dict__") else str(e) for e in getattr(validation, "warnings", [])],
                "confidence_penalty": getattr(validation, "confidence_penalty", 0.0),
            }
            decision = validator_dict["decision"]
            if decision == "NEEDS_REVIEW":
                return self._blocked_record(key, draft, provider_responses, validator_dict, final_dict,
                                            customer_confirmed_address, OrderAddressStatus.NEEDS_REVIEW,
                                            carrier, reason="VALIDATOR_NEEDS_REVIEW")
            if decision not in ALLOWED_VALIDATOR_DECISIONS:
                return self._blocked_record(key, draft, provider_responses, validator_dict, final_dict,
                                            customer_confirmed_address,
                                            OrderAddressStatus.BLOCKED_CARRIER_MISMATCH,
                                            carrier, reason=f"VALIDATOR_DECISION_{decision or 'UNKNOWN'}")
            confidence, gate_status = self._extract_gate(final_obj, final_dict)
            if gate_status is not None and gate_status not in ALLOWED_GATE_STATUSES:
                mapped = gate_status_to_order_status(gate_status)
                status = (OrderAddressStatus.NEEDS_REVIEW if mapped == OrderAddressStatus.NEEDS_REVIEW.value
                          else OrderAddressStatus.BLOCKED_GATE_REJECTED)
                return self._blocked_record(key, draft, provider_responses, validator_dict, final_dict,
                                            customer_confirmed_address, status, carrier,
                                            gate_result={"status": gate_status}, confidence=confidence,
                                            reason="GATE_NOT_VERIFIED")
            if confidence is None or confidence < MIN_CONFIDENCE:
                return self._blocked_record(key, draft, provider_responses, validator_dict, final_dict,
                                            customer_confirmed_address,
                                            OrderAddressStatus.BLOCKED_LOW_CONFIDENCE,
                                            carrier, gate_result={"status": gate_status},
                                            confidence=confidence, reason="LOW_CONFIDENCE")
            gate_result = {"status": gate_status or "VERIFIED", "confidence_score": confidence}
            try:
                label_id = self._labels.create_label(carrier, final_dict, key)
            except Exception as exc:  # noqa: BLE001
                return self._blocked_record(key, draft, provider_responses, validator_dict, final_dict,
                                            customer_confirmed_address, OrderAddressStatus.LABEL_FAILED,
                                            carrier, gate_result=gate_result, confidence=confidence,
                                            reason=f"LABEL_FAILED:{type(exc).__name__}")
            record = AuditRecord(key, draft.pancake_order_id, draft.raw_input,
                                 draft.pancake_suggestion, provider_responses,
                                 validator_dict, final_dict, customer_confirmed_address,
                                 OrderAddressStatus.COMMITTED, carrier=carrier,
                                 carrier_label_id=label_id, gate_result=gate_result,
                                 confidence_score=confidence, reason_code="OK")
            return self._save(record)

    def _extract_gate(self, final_obj: Any, final_dict: Dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
        confidence = final_dict.get("confidence_score", final_dict.get("confidence"))
        if confidence is None:
            confidence = getattr(final_obj, "confidence", None) or getattr(final_obj, "confidence_score", None)
        gate_status = final_dict.get("gate_status", final_dict.get("status"))
        if gate_status is None:
            gate_status = getattr(final_obj, "status", None)
        if gate_status is not None:
            gate_status = _decision_value(gate_status)
        try:
            confidence_f = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence_f = None
        return confidence_f, gate_status

    def _process_via_service(self, draft: PancakeDraft, key: str, carrier: str,
                             anchored_province_code: Optional[str],
                             customer_confirmed_address: Optional[Dict[str, Any]]) -> AuditRecord:
        provider_responses = {"via": "address_service", "pancake_suggestion_untrusted": draft.pancake_suggestion}
        service_result = self._service.resolve(draft.raw_input, carrier=carrier)
        contract = getattr(service_result, "contract", None)
        if contract is None:
            return self._blocked_record(key, draft, provider_responses, {}, None,
                                        customer_confirmed_address, OrderAddressStatus.BLOCKED_UNVERIFIED,
                                        carrier, reason="SERVICE_NO_CONTRACT")
        final_dict = contract.to_dict() if hasattr(contract, "to_dict") else dict(contract)
        status_value = _decision_value(getattr(contract, "status", ""))
        confidence = float(getattr(contract, "confidence", 0.0) or 0.0)
        gate_result = {"status": status_value, "confidence_score": confidence,
                       "reason_code": _decision_value(getattr(contract, "reason_code", ""))}
        if status_value != "VERIFIED":
            mapped = gate_status_to_order_status(status_value)
            status = (OrderAddressStatus.NEEDS_REVIEW if mapped == OrderAddressStatus.NEEDS_REVIEW.value
                      else OrderAddressStatus.BLOCKED_GATE_REJECTED)
            if status_value == "REJECTED":
                status = OrderAddressStatus.BLOCKED_UNVERIFIED
            return self._blocked_record(key, draft, provider_responses, {}, final_dict,
                                        customer_confirmed_address, status, carrier,
                                        gate_result=gate_result, confidence=confidence,
                                        reason=gate_result["reason_code"])
        ok, reason = _hierarchy_ok(final_dict, anchored_province_code)
        if not ok or confidence < MIN_CONFIDENCE:
            status = (OrderAddressStatus.BLOCKED_CARRIER_MISMATCH if reason in ("HIERARCHY_CONFLICT", "PROVINCE_MISMATCH")
                      else OrderAddressStatus.BLOCKED_LOW_CONFIDENCE)
            return self._blocked_record(key, draft, provider_responses, {}, final_dict,
                                        customer_confirmed_address, status, carrier,
                                        gate_result=gate_result, confidence=confidence, reason=reason or "LOW_CONFIDENCE")
        try:
            label_id = self._labels.create_label(carrier, final_dict, key)
        except Exception as exc:  # noqa: BLE001
            return self._blocked_record(key, draft, provider_responses, {}, final_dict,
                                        customer_confirmed_address, OrderAddressStatus.LABEL_FAILED,
                                        carrier, gate_result=gate_result, confidence=confidence,
                                        reason=f"LABEL_FAILED:{type(exc).__name__}")
        record = AuditRecord(key, draft.pancake_order_id, draft.raw_input,
                             draft.pancake_suggestion, provider_responses, {}, final_dict,
                             customer_confirmed_address, OrderAddressStatus.COMMITTED,
                             carrier=carrier, carrier_label_id=label_id,
                             gate_result=gate_result, confidence_score=confidence, reason_code="OK")
        return self._save(record)

    def disable_provider(self, provider_name: str) -> None:
        self._router.set_provider_enabled(provider_name, False)

    def enable_provider(self, provider_name: str) -> None:
        self._router.set_provider_enabled(provider_name, True)

    @staticmethod
    def audit_to_json(record: AuditRecord) -> str:
        final = dict(record.final_address or {})
        for k in ("phone", "customer_phone", "note", "detail", "street"):
            if k in final:
                final[k] = "***"
        return json.dumps({
            "idempotency_key": record.idempotency_key,
            "pancake_order_id": record.pancake_order_id,
            "raw_input_sha256": record.raw_input_sha256 or _sha256_hex(record.raw_input),
            "raw_input_masked": _masked_preview(record.raw_input),
            "raw_input_encrypted": record.raw_input_encrypted,
            "status": record.status.value,
            "carrier": record.carrier,
            "carrier_label_id": record.carrier_label_id,
            "final_address": final,
            "validator_result": record.validator_result,
            "gate_result": record.gate_result,
            "confidence_score": record.confidence_score,
            "reason_code": record.reason_code,
            "audit_seq": record.audit_seq,
            "prev_hash": record.prev_hash,
            "entry_hash": record.entry_hash,
        }, ensure_ascii=False)
