"""
Integration contract: Pancake / Goong / carriers - approach_19
Branch: expert/backend-sec-m1, fix: fix_adapter_fail_closed_allowlist

Allowlist fail-closed (thay cho fail-open cũ):
  Nhãn vận chuyển CHỈ được tạo khi TẤT CẢ điều kiện sau cùng đúng:
    1. Validator decision thuộc allowlist {"OK"} — mọi giá trị khác,
       kể cả REJECTED / INVALID / ERROR / enum mới chưa biết, đều chặn.
    2. Gate status == VERIFIED và confidence >= 0.85 (thiếu confidence
       thì coi là chưa xác minh, không được suy đoán là đủ).
    3. Đủ 3 mã province_code / district_code / ward_code và prefix
       phân cấp đúng: district.startswith(province), ward.startswith(district).
    4. Nếu có anchored_province_code thì final province_code phải bằng nó.
  Không có đường rơi-through nào tới create_label.

Thống nhất trạng thái (một bảng mapping duy nhất):
  schemas.AddressStatus / GateStatus  ->  OrderAddressStatus
    VERIFIED            -> COMMITTED (chỉ sau khi qua allowlist trên)
    NEEDS_REVIEW        -> NEEDS_REVIEW
    NEED_CONFIRMATION   -> NEEDS_REVIEW
    REJECTED            -> BLOCKED_UNVERIFIED / BLOCKED_GATE_REJECTED
    REJECTED_INSUFFICIENT -> BLOCKED_UNVERIFIED
  Validator Decision    ->  OrderAddressStatus
    OK                  -> (tiếp tục kiểm tra allowlist, chưa phải COMMITTED)
    NEEDS_REVIEW        -> NEEDS_REVIEW
    BLOCKED / khác      -> BLOCKED_CARRIER_MISMATCH hoặc BLOCKED_UNVERIFIED

Contract giữ nguyên từ bản gốc:
- Pancake = lead + DRAFT order source ONLY, suggestion không có thẩm quyền.
- Goong = ONE replaceable suggestion provider, qua GeocodingRouter.
- Idempotency neo theo pancake_order_id (không neo theo raw_input):
  sửa địa chỉ sau review không tạo key mới / nhãn thứ hai cho cùng đơn.
- Audit: không xuất raw_input plain-text, không ghi đè COMMITTED/BLOCKED,
  create_label lỗi vẫn lưu LABEL_FAILED.
- AddressService (nếu được inject) là entry point duy nhất; adapter
  không gọi thẳng provider khi đã có service.
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

# --- Thống nhất trạng thái: dùng schemas nếu có, fallback nội bộ nếu chưa merge ---
try:  # khi schemas.py đã merge cùng nhánh này
    from ..address.domain.schemas import (  # type: ignore
        AddressStatus as _CanonicalAddressStatus,
        GateStatus as _CanonicalGateStatus,
        gate_to_address_status as _gate_to_address_status,
    )

    _HAS_SCHEMAS = True
except Exception:  # pragma: no cover - adapter vẫn chạy standalone trong test
    _HAS_SCHEMAS = False
    _CanonicalAddressStatus = None  # type: ignore
    _CanonicalGateStatus = None  # type: ignore
    _gate_to_address_status = None  # type: ignore

MIN_CONFIDENCE = 0.85
ALLOWED_VALIDATOR_DECISIONS = frozenset({"OK"})
ALLOWED_GATE_STATUSES = frozenset({"VERIFIED"})


def gate_status_to_order_status(gate_status: str) -> str:
    """Mapping chuẩn GateStatus -> OrderAddressStatus, dùng chung mọi tầng."""
    value = str(gate_status or "").upper()
    if _HAS_SCHEMAS and _CanonicalGateStatus is not None:
        try:
            canonical = _CanonicalGateStatus(value)
            mapped = _gate_to_address_status(canonical)
            return {
                "VERIFIED": OrderAddressStatus.COMMITTED.value,
                "NEEDS_REVIEW": OrderAddressStatus.NEEDS_REVIEW.value,
                "REJECTED": OrderAddressStatus.BLOCKED_GATE_REJECTED.value,
            }[mapped.value]
        except Exception:
            pass
    return {
        "VERIFIED": OrderAddressStatus.COMMITTED.value,
        "NEED_CONFIRMATION": OrderAddressStatus.NEEDS_REVIEW.value,
        "NEEDS_REVIEW": OrderAddressStatus.NEEDS_REVIEW.value,
        "REJECTED_INSUFFICIENT": OrderAddressStatus.BLOCKED_UNVERIFIED.value,
        "REJECTED": OrderAddressStatus.BLOCKED_GATE_REJECTED.value,
    }.get(value, OrderAddressStatus.BLOCKED_UNVERIFIED.value)


class OrderAddressStatus(str, Enum):
    DRAFT_FROM_PANCAKE = "DRAFT_FROM_PANCAKE"
    COMMITTED = "COMMITTED"  # safe to create shipping label, chỉ sau allowlist
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED_CARRIER_MISMATCH = "BLOCKED_CARRIER_MISMATCH"
    BLOCKED_UNVERIFIED = "BLOCKED_UNVERIFIED"
    BLOCKED_LOW_CONFIDENCE = "BLOCKED_LOW_CONFIDENCE"
    BLOCKED_GATE_REJECTED = "BLOCKED_GATE_REJECTED"
    LABEL_FAILED = "LABEL_FAILED"


# Trạng thái cuối (terminal): không được ghi đè bởi trạng thái sau.
_TERMINAL_STATUSES = frozenset({
    OrderAddressStatus.COMMITTED,
    OrderAddressStatus.BLOCKED_CARRIER_MISMATCH,
    OrderAddressStatus.BLOCKED_UNVERIFIED,
    OrderAddressStatus.BLOCKED_LOW_CONFIDENCE,
    OrderAddressStatus.BLOCKED_GATE_REJECTED,
})


def _sha256_hex(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _masked_preview(raw_input: str) -> str:
    # Audit/UI chỉ cần biết có input hay không, không cần nội dung.
    return "***" if (raw_input or "").strip() else ""


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
    raw_input: str  # lưu trong store nội bộ dev/test; KHÔNG xuất ra JSON/log
    pancake_suggestion: Dict[str, Any]
    provider_responses: Dict[str, Any]
    validator_result: Dict[str, Any]
    final_address: Optional[Dict[str, Any]]
    customer_confirmed_address: Optional[Dict[str, Any]]
    status: OrderAddressStatus
    created_at: float = field(default_factory=time.time)
    carrier: str = "SPX"
    carrier_label_id: Optional[str] = None
    # Thêm cho allowlist / audit, có default để code cũ vẫn khởi tạo được.
    raw_input_sha256: str = ""
    gate_result: Dict[str, Any] = field(default_factory=dict)
    confidence_score: Optional[float] = None
    reason_code: str = ""

    def __post_init__(self) -> None:
        if not self.raw_input_sha256 and self.raw_input:
            self.raw_input_sha256 = _sha256_hex(self.raw_input)


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
                     idempotency_key: str) -> str:
        """Create shipping label, must itself honour idempotency_key."""
        ...


class AuditStorePort(Protocol):
    def get_by_idempotency_key(self, key: str) -> Optional[AuditRecord]: ...
    def save(self, record: AuditRecord) -> None: ...


def make_idempotency_key(pancake_order_id: str, raw_input: str = "", phone_ref: str = "") -> str:
    """Neo theo đơn, KHÔNG neo theo raw_input/phone_ref.

    Bản cũ băm order_id|raw_input|phone_ref nên sửa địa chỉ tạo key mới
    và bypass replay COMMITTED -> nhãn thứ hai cho cùng đơn. Bản mới
    chỉ băm pancake_order_id; sửa địa chỉ sau commit phải đi qua flow
    hủy/thay nhãn, không được tạo key mới âm thầm. Giữ tham số cũ để
    code gọi cũ không vỡ, nhưng cố tình bỏ qua chúng khi tính key.
    """
    digest = hashlib.sha256(f"{(pancake_order_id or '').strip()}".encode("utf-8")).hexdigest()
    return f"addr:{digest[:40]}"


class InMemoryAuditStore:
    """Dev/test store. Production: PostgreSQL table với UNIQUE(idempotency_key)
    và encrypted PII columns (see src/security). Thread-safe.

    save() là state machine tiến lên, không ghi đè trạng thái terminal:
    COMMITTED / BLOCKED_* giữ nguyên, NEEDS_REVIEW được phép tiến lên
    COMMITTED hoặc BLOCKED, DRAFT được phép tiến lên mọi trạng thái.
    """
    def __init__(self) -> None:
        self._data: Dict[str, AuditRecord] = {}
        self._lock = threading.Lock()

    def get_by_idempotency_key(self, key: str) -> Optional[AuditRecord]:
        with self._lock:
            return self._data.get(key)

    def save(self, record: AuditRecord) -> None:
        with self._lock:
            existing = self._data.get(record.idempotency_key)
            if existing is None:
                self._data[record.idempotency_key] = record
                return
            if existing.status in _TERMINAL_STATUSES:
                return
            # NEEDS_REVIEW / DRAFT: cho phép tiến lên, không cho lùi về DRAFT.
            if record.status == OrderAddressStatus.DRAFT_FROM_PANCAKE:
                return
            self._data[record.idempotency_key] = record


# Striped locks cố định (64) thay cho dict lock theo key tăng vô hạn.
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
    if not district.startswith(province) or not ward.startswith(district):
        return False, "HIERARCHY_CONFLICT"
    if anchored_province_code and province != str(anchored_province_code):
        return False, "PROVINCE_MISMATCH"
    return True, ""


class PancakeGoongShippingAdapter:
    # Suggested final-address shape (canonical codes, not free text):
    # {"province_code","district_code","ward_code","street","detail",
    #  "anchored_province_code","lat","lng","confidence","status"}

    def __init__(self, router: RouterPort, validator: ValidatorPort,
                 carrier_labels: CarrierLabelPort, audit: AuditStorePort,
                 address_builder: Any,
                 address_service: Optional[AddressServicePort] = None,
                 confidence_gate: Optional[GatePort] = None):
        """
        address_builder: callable(router_result, draft) -> (final_address_obj,
        provider_responses_dict). Chỉ dùng khi KHÔNG có address_service.
        address_service: AddressService.resolve() là entry duy nhất khi có;
        khi đó adapter không gọi router/builder trực tiếp.
        """
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

            # --- Đường ưu tiên: AddressService là entry duy nhất ---
            if self._service is not None:
                return self._process_via_service(draft, key, carrier, anchored_province_code,
                                                 customer_confirmed_address)

            # --- Đường legacy (router + builder + validator), vẫn allowlist ---
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
            # Customer confirmation chỉ được sửa street/detail, KHÔNG được
            # đổi province/district/ward code — mọi code khác đều bị bỏ qua.
            if customer_confirmed_address:
                for field_name in ("street", "detail"):
                    if customer_confirmed_address.get(field_name):
                        try:
                            setattr(final_obj, field_name, customer_confirmed_address[field_name])
                        except Exception:
                            pass
            final_dict = dict(final_obj.__dict__) if hasattr(final_obj, "__dict__") else dict(final_obj)

            # Allowlist 1: hierarchy + anchored province.
            ok, reason = _hierarchy_ok(final_dict, anchored_province_code)
            if not ok:
                status = (OrderAddressStatus.BLOCKED_CARRIER_MISMATCH
                          if reason in ("HIERARCHY_CONFLICT", "PROVINCE_MISMATCH")
                          else OrderAddressStatus.BLOCKED_UNVERIFIED)
                return self._blocked_record(key, draft, provider_responses, {}, final_dict,
                                            customer_confirmed_address, status, carrier, reason=reason)

            # Allowlist 2: validator — chỉ {"OK"} được đi tiếp.
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
                # BLOCKED / REJECTED / INVALID / ERROR / enum mới: chặn, không label.
                return self._blocked_record(key, draft, provider_responses, validator_dict, final_dict,
                                            customer_confirmed_address,
                                            OrderAddressStatus.BLOCKED_CARRIER_MISMATCH,
                                            carrier, reason=f"VALIDATOR_DECISION_{decision or 'UNKNOWN'}")

            # Allowlist 3: gate + confidence — thiếu thì chặn, không suy đoán.
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

            # Allowlist đã đủ: chỉ lúc này mới được tạo nhãn, và luôn audit cả khi lỗi.
            gate_result = {"status": gate_status or "VERIFIED", "confidence_score": confidence}
            try:
                label_id = self._labels.create_label(carrier, final_dict, key)
            except Exception as exc:  # noqa: BLE001 - phải lưu audit cho ca lỗi nhất
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
        # Service đã chạy guard/anchor/validate/gate; adapter chỉ kiểm tra
        # lại allowlist cuối, không tin mù vào service.
        status_value = _decision_value(getattr(contract, "status", ""))
        confidence = float(getattr(contract, "confidence", 0.0) or 0.0)
        gate_result = {"status": status_value, "confidence_score": confidence,
                       "reason_code": _decision_value(getattr(contract, "reason_code", ""))}
        if status_value != "VERIFIED":
            mapped = gate_status_to_order_status(status_value)
            status = (OrderAddressStatus.NEEDS_REVIEW if mapped == OrderAddressStatus.NEEDS_REVIEW.value
                      else OrderAddressStatus.BLOCKED_GATE_REJECTED)
            # REJECTED từ service mà không rõ lý do thì về BLOCKED_UNVERIFIED.
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

    # -- provider governance ----------------------------------------------------
    def disable_provider(self, provider_name: str) -> None:
        self._router.set_provider_enabled(provider_name, False)

    def enable_provider(self, provider_name: str) -> None:
        self._router.set_provider_enabled(provider_name, True)

    @staticmethod
    def audit_to_json(record: AuditRecord) -> str:
        """Safe serialization: KHÔNG xuất raw_input plain-text, chỉ hash + masked."""
        final = dict(record.final_address or {})
        # Không để SĐT / ghi chú riêng lọt ra ngoài qua final_address.
        for k in ("phone", "customer_phone", "note"):
            if k in final:
                final[k] = "***"
        return json.dumps({
            "idempotency_key": record.idempotency_key,
            "pancake_order_id": record.pancake_order_id,
            "raw_input_sha256": record.raw_input_sha256 or _sha256_hex(record.raw_input),
            "raw_input_masked": _masked_preview(record.raw_input),
            "status": record.status.value,
            "carrier": record.carrier,
            "carrier_label_id": record.carrier_label_id,
            "final_address": final,
            "validator_result": record.validator_result,
            "gate_result": record.gate_result,
            "confidence_score": record.confidence_score,
            "reason_code": record.reason_code,
        }, ensure_ascii=False)
