"""
Integration contract: Pancake / Goong / carriers - approach_19
Branch: expert/backend-sec-m1

Contract (enforced in code, not just in docs):
- Pancake = lead + DRAFT order source ONLY. Its autofilled address has no
  authority and is stored as `pancake_suggestion`, never as final address.
- Goong = ONE replaceable suggestion provider (alongside Vietmap, Google,
  Viettel Maps, OSM) routed through GeocodingRouter. No direct Goong call
  from order flow is allowed.
- Final gate before creating a shipping label: validate with the chosen
  carrier's admin codes. Province/ward mismatch or non-servable ward BLOCKS
  label creation and returns the reason to UI.
- Every decision is traceable: idempotency_key, raw_input, all provider
  responses, validator result, and the final customer-confirmed address are
  stored in an audit record for return disputes.
- Idempotency: same idempotency_key replays the stored result, never creates
  a second shipping label (safe under Pancake webhook retries / concurrency).
- Provider with province-mismatch rate over threshold is down-weighted /
  disabled via the router's circuit breaker.

Depends on domain/infrastructure modules in this repo by duck-typed ports to
avoid import cycles; wire concrete classes in services layer.
Status: chua chay - CI se bao ket qua.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Protocol


class OrderAddressStatus(str, Enum):
    DRAFT_FROM_PANCAKE = "DRAFT_FROM_PANCAKE"
    COMMITTED = "COMMITTED"                    # safe to create shipping label
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED_CARRIER_MISMATCH = "BLOCKED_CARRIER_MISMATCH"
    BLOCKED_UNVERIFIED = "BLOCKED_UNVERIFIED"


@dataclass
class PancakeDraft:
    pancake_order_id: str
    raw_input: str
    pancake_suggestion: Dict[str, Any] = field(default_factory=dict)  # untrusted
    customer_phone_ref: str = ""  # vault ref / masked, never log raw phone
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


class RouterPort(Protocol):
    def geocode(self, query_ascii: str, query_diacritic: str,
                anchored_province_code: Optional[str]): ...
    def set_provider_enabled(self, name: str, enabled: bool) -> None: ...


class ValidatorPort(Protocol):
    def validate(self, addr, preferred_carrier: str = "SPX"): ...


class CarrierLabelPort(Protocol):
    def create_label(self, carrier: str, final_address: Dict[str, Any],
                     idempotency_key: str) -> str:
        """Create shipping label, must itself honour idempotency_key."""
        ...


class AuditStorePort(Protocol):
    def get_by_idempotency_key(self, key: str) -> Optional[AuditRecord]: ...
    def save(self, record: AuditRecord) -> None: ...


def make_idempotency_key(pancake_order_id: str, raw_input: str, phone_ref: str) -> str:
    digest = hashlib.sha256(
        f"{pancake_order_id}|{raw_input.strip().lower()}|{phone_ref}".encode("utf-8")
    ).hexdigest()
    return f"addr:{digest[:40]}"


class InMemoryAuditStore:
    """Dev/test store. Production: PostgreSQL table with UNIQUE(idempotency_key)
    and encrypted PII columns (see src/security). Thread-safe for concurrency."""
    def __init__(self) -> None:
        self._data: Dict[str, AuditRecord] = {}
        self._lock = threading.Lock()

    def get_by_idempotency_key(self, key: str) -> Optional[AuditRecord]:
        with self._lock:
            return self._data.get(key)

    def save(self, record: AuditRecord) -> None:
        with self._lock:
            # First write wins for a key: concurrent retries must not overwrite
            # a COMMITTED record with a later draft.
            existing = self._data.get(record.idempotency_key)
            if existing is None or existing.status != OrderAddressStatus.COMMITTED:
                self._data[record.idempotency_key] = record


class PancakeGoongShippingAdapter:
    # Suggested final-address shape (canonical codes, not free text):
    # {"province_code","district_code","ward_code","street","detail",
    #  "anchored_province_code","lat","lng"}

    def __init__(self, router: RouterPort, validator: ValidatorPort,
                 carrier_labels: CarrierLabelPort, audit: AuditStorePort,
                 address_builder: Any):
        """
        address_builder: callable(router_result, draft) -> (final_address_obj,
        provider_responses_dict). Injected from services layer so ranking /
        confidence logic (algo-ml) stays out of the integration contract.
        """
        self._router = router
        self._validator = validator
        self._labels = carrier_labels
        self._audit = audit
        self._build = address_builder
        self._key_locks: Dict[str, threading.Lock] = {}
        self._key_locks_guard = threading.Lock()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._key_locks_guard:
            return self._key_locks.setdefault(key, threading.Lock())

    def process_draft(self, draft: PancakeDraft, query_ascii: str,
                      query_diacritic: str, anchored_province_code: Optional[str],
                      carrier: str = "SPX",
                      customer_confirmed_address: Optional[Dict[str, Any]] = None) -> AuditRecord:
        key = make_idempotency_key(draft.pancake_order_id, draft.raw_input,
                                   draft.customer_phone_ref)
        # Replay: webhook retry / double submit returns the original outcome.
        existing = self._audit.get_by_idempotency_key(key)
        if existing is not None and existing.status == OrderAddressStatus.COMMITTED:
            return existing

        # Per-key lock: concurrent retries for the same order serialize here,
        # different orders run in parallel. Lock is held only for this order.
        with self._lock_for(key):
            existing = self._audit.get_by_idempotency_key(key)
            if existing is not None and existing.status == OrderAddressStatus.COMMITTED:
                return existing

            # 1) Providers are suggestions only - Pancake suggestion is ignored
            #    as authority, Goong goes through the router like everyone else.
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
                record = AuditRecord(key, draft.pancake_order_id, draft.raw_input,
                                     draft.pancake_suggestion, provider_responses,
                                     {}, customer_confirmed_address,
                                     customer_confirmed_address,
                                     OrderAddressStatus.BLOCKED_UNVERIFIED,
                                     carrier=carrier)
                self._audit.save(record)
                return record

            final_obj, _ = self._build(router_result, draft)
            # Customer confirmation, when present, outranks provider ranking
            # for detail/street, but NEVER outranks hierarchy/carrier checks.
            if customer_confirmed_address:
                for field_name in ("street", "detail"):
                    if customer_confirmed_address.get(field_name):
                        setattr(final_obj, field_name, customer_confirmed_address[field_name])

            # 2) Final carrier-aligned validation gate
            validation = self._validator.validate(final_obj, preferred_carrier=carrier)
            validator_dict = {
                "decision": getattr(validation.decision, "value", str(validation.decision)),
                "errors": [e.__dict__ for e in getattr(validation, "errors", [])],
                "warnings": [e.__dict__ for e in getattr(validation, "warnings", [])],
                "confidence_penalty": getattr(validation, "confidence_penalty", 0.0),
            }
            final_dict = final_obj.__dict__ if hasattr(final_obj, "__dict__") else dict(final_obj)

            if validator_dict["decision"] == "BLOCKED":
                status = OrderAddressStatus.BLOCKED_CARRIER_MISMATCH
                record = AuditRecord(key, draft.pancake_order_id, draft.raw_input,
                                     draft.pancake_suggestion, provider_responses,
                                     validator_dict, final_dict,
                                     customer_confirmed_address, status, carrier=carrier)
                self._audit.save(record)
                return record
            if validator_dict["decision"] == "NEEDS_REVIEW":
                record = AuditRecord(key, draft.pancake_order_id, draft.raw_input,
                                     draft.pancake_suggestion, provider_responses,
                                     validator_dict, final_dict,
                                     customer_confirmed_address,
                                     OrderAddressStatus.NEEDS_REVIEW, carrier=carrier)
                self._audit.save(record)
                return record

            # 3) Only now may a shipping label be created, idempotently.
            label_id = self._labels.create_label(carrier, final_dict, key)
            record = AuditRecord(key, draft.pancake_order_id, draft.raw_input,
                                 draft.pancake_suggestion, provider_responses,
                                 validator_dict, final_dict,
                                 customer_confirmed_address,
                                 OrderAddressStatus.COMMITTED, carrier=carrier,
                                 carrier_label_id=label_id)
            self._audit.save(record)
            return record

    # -- provider governance ----------------------------------------------------
    def disable_provider(self, provider_name: str) -> None:
        """Used when mismatch/return rate breaches threshold (router also
        auto-opens its breaker; this is the explicit ops kill-switch)."""
        self._router.set_provider_enabled(provider_name, False)

    def enable_provider(self, provider_name: str) -> None:
        self._router.set_provider_enabled(provider_name, True)

    @staticmethod
    def audit_to_json(record: AuditRecord) -> str:
        """Safe serialization for dispute tracing. Phone is only a ref/masked
        value in this layer - raw PII must never be added to this payload."""
        return json.dumps({
            "idempotency_key": record.idempotency_key,
            "pancake_order_id": record.pancake_order_id,
            "raw_input": record.raw_input,
            "status": record.status.value,
            "carrier": record.carrier,
            "carrier_label_id": record.carrier_label_id,
            "final_address": record.final_address,
            "validator_result": record.validator_result,
        }, ensure_ascii=False)
