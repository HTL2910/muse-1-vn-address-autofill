"""
Risk-tiered manual review queue - approach_15
Fix: fix_review_queue_state_machine (round 2)

Kept from the original: risk tiers (GREEN auto, YELLOW 1-click CS,
RED hold + ask customer), thresholds 0.90 / 0.65, SLA (YELLOW 15 min,
RED customer wait 4h), metrics shape, public method names.

What this fix changes:
1. Store is a DATABASE, not a process-local dict. ReviewStore port with
   a SQLite implementation (stdlib sqlite3; :memory: by default, a file
   path in production, PostgreSQL can implement the same port).
   UNIQUE(order_id): enqueueing the same order twice returns the
   existing item instead of creating a duplicate queue entry.
   Optimistic concurrency: every mutation is a compare-and-swap on
   `version`; a stale writer gets ConcurrencyError instead of silently
   overwriting another CS agent's decision. Items survive a queue /
   process restart whenever the store is file-backed.
2. Explicit state machine, per actor. CS approve/reject is only legal
   from PENDING_CS or ESCALATED; customer confirmation is only legal
   from WAITING_CUSTOMER / ESCALATED / PENDING_CS; APPROVED and
   AUTO_APPROVED are terminal. Anything else raises
   InvalidTransitionError - approving twice is an error, not a second
   metrics sample.
3. customer_confirmed() RE-VALIDATES before it can approve:
   - the one-tap token: in production mode (token_secret configured)
     a token is mandatory, only its HMAC hash is stored, it is bound
     to this order and expires; whenever a token IS supplied it is
     always verified. Without a secret (internal/CS-driven flows and
     tests) the token is optional, because no link was ever issued.
   - the confirmed province must equal the anchored province;
   - the injected validator (HierarchicalValidator or any duck-typed
     validate_dict / callable) must not return BLOCKED, and the gate
     (if injected) must return VERIFIED; NEEDS_REVIEW routes the item
     to PENDING_CS instead of approving;
   - with no validator injected, a structural fallback still requires
     all three admin codes and the anchored province.
   A failed re-validation leaves the item WAITING_CUSTOMER with
   last_validation_errors recorded so the customer can retry - it is
   never approved and never silently dropped.
4. Intake stamps created_at with the queue clock, so SLA math compares
   one consistent time source.

Status: chua chay - CI se bao ket qua.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Protocol


class Tier(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class ReviewStatus(str, Enum):
    AUTO_APPROVED = "AUTO_APPROVED"
    PENDING_CS = "PENDING_CS"
    WAITING_CUSTOMER = "WAITING_CUSTOMER"
    APPROVED = "APPROVED"
    REJECTED_NEEDS_INFO = "REJECTED_NEEDS_INFO"
    ESCALATED = "ESCALATED"


class InvalidTransitionError(ValueError):
    """Raised when a status transition is not allowed for that actor."""


class ConcurrencyError(RuntimeError):
    """Raised when a compare-and-swap update loses to another writer."""


class InvalidTokenError(ValueError):
    """Raised when a one-tap fix token is wrong or expired, or is
    missing while the queue runs in token-enforced (secret) mode."""


# State machine: current status -> statuses it may move to (any actor).
TRANSITIONS: Dict[ReviewStatus, frozenset] = {
    ReviewStatus.PENDING_CS: frozenset({
        ReviewStatus.APPROVED, ReviewStatus.WAITING_CUSTOMER,
        ReviewStatus.REJECTED_NEEDS_INFO, ReviewStatus.ESCALATED}),
    ReviewStatus.WAITING_CUSTOMER: frozenset({
        ReviewStatus.APPROVED, ReviewStatus.PENDING_CS,
        ReviewStatus.WAITING_CUSTOMER, ReviewStatus.ESCALATED}),
    ReviewStatus.ESCALATED: frozenset({
        ReviewStatus.APPROVED, ReviewStatus.WAITING_CUSTOMER,
        ReviewStatus.PENDING_CS}),
    ReviewStatus.REJECTED_NEEDS_INFO: frozenset({ReviewStatus.WAITING_CUSTOMER}),
    ReviewStatus.APPROVED: frozenset(),
    ReviewStatus.AUTO_APPROVED: frozenset(),
}
TERMINAL_STATUSES = frozenset({ReviewStatus.APPROVED, ReviewStatus.AUTO_APPROVED})
# Per-actor source states - this is what stops a CS approve from
# short-circuiting an item that is waiting on the customer.
CS_ACTION_SOURCES = frozenset({ReviewStatus.PENDING_CS, ReviewStatus.ESCALATED})
CUSTOMER_CONFIRM_SOURCES = frozenset({
    ReviewStatus.WAITING_CUSTOMER, ReviewStatus.ESCALATED, ReviewStatus.PENDING_CS})


@dataclass
class ReviewItem:
    order_id: str
    confidence: float
    raw_input: str
    proposed_address: dict
    anchored_province_code: Optional[str] = None
    risk_flags: List[str] = field(default_factory=list)
    prior_wrong_address_return: bool = False
    customer_phone_ref: str = ""  # vault reference / masked value only, never raw in logs
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    tier: Tier = Tier.YELLOW
    status: ReviewStatus = ReviewStatus.PENDING_CS
    created_at: float = field(default_factory=time.time)
    decided_at: Optional[float] = None
    decided_by: str = ""
    customer_message_sent_at: Optional[float] = None
    # fix_review_queue_state_machine fields
    version: int = 0
    fix_token_hash: str = ""
    fix_token_expires_at: Optional[float] = None
    last_validation_errors: List[str] = field(default_factory=list)
    last_action: str = ""


class CustomerMessenger(Protocol):
    def send_address_fix_link(self, phone_ref: str, order_id: str,
                              anchored_province_code: Optional[str],
                              channels: List[str]) -> bool:
        """Channels tried in order, e.g. ['zalo', 'app', 'sms']. One-tap
        link must only offer wards inside the anchored province. Newer
        implementations may accept fix_token=<token> as a keyword."""
        ...


class ReviewStore(Protocol):
    def save_new(self, item: ReviewItem) -> bool: ...
    def get(self, item_id: str) -> Optional[ReviewItem]: ...
    def get_by_order(self, order_id: str) -> Optional[ReviewItem]: ...
    def update_if_version(self, item: ReviewItem, expected_version: int) -> bool: ...
    def list_all(self) -> List[ReviewItem]: ...


# ---------------------------------------------------------------------------
# SQLite store (production-shaped; PostgreSQL can implement the same port)
# ---------------------------------------------------------------------------
_COLUMNS = ("id", "order_id", "confidence", "raw_input", "proposed_address",
            "anchored_province_code", "risk_flags", "prior_wrong_address_return",
            "customer_phone_ref", "tier", "status", "created_at", "decided_at",
            "decided_by", "customer_message_sent_at", "version",
            "fix_token_hash", "fix_token_expires_at", "last_validation_errors",
            "last_action")


def _item_to_row(item: ReviewItem) -> tuple:
    return (item.id, item.order_id, item.confidence, item.raw_input,
            json.dumps(item.proposed_address or {}, ensure_ascii=False),
            item.anchored_province_code,
            json.dumps(item.risk_flags or [], ensure_ascii=False),
            1 if item.prior_wrong_address_return else 0,
            item.customer_phone_ref, item.tier.value, item.status.value,
            item.created_at, item.decided_at, item.decided_by,
            item.customer_message_sent_at, item.version,
            item.fix_token_hash, item.fix_token_expires_at,
            json.dumps(item.last_validation_errors or [], ensure_ascii=False),
            item.last_action)


def _row_to_item(row: sqlite3.Row) -> ReviewItem:
    return ReviewItem(
        order_id=row["order_id"], confidence=row["confidence"],
        raw_input=row["raw_input"],
        proposed_address=json.loads(row["proposed_address"] or "{}"),
        anchored_province_code=row["anchored_province_code"],
        risk_flags=json.loads(row["risk_flags"] or "[]"),
        prior_wrong_address_return=bool(row["prior_wrong_address_return"]),
        customer_phone_ref=row["customer_phone_ref"],
        id=row["id"], tier=Tier(row["tier"]), status=ReviewStatus(row["status"]),
        created_at=row["created_at"], decided_at=row["decided_at"],
        decided_by=row["decided_by"] or "",
        customer_message_sent_at=row["customer_message_sent_at"],
        version=row["version"], fix_token_hash=row["fix_token_hash"] or "",
        fix_token_expires_at=row["fix_token_expires_at"],
        last_validation_errors=json.loads(row["last_validation_errors"] or "[]"),
        last_action=row["last_action"] or "")


class SQLiteReviewStore:
    """DB-backed store. One connection + lock (safe for the shop's
    worker count); file-backed instances survive process restarts."""

    DDL = """
    CREATE TABLE IF NOT EXISTS review_items (
        id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL UNIQUE,
        confidence REAL NOT NULL,
        raw_input TEXT NOT NULL,
        proposed_address TEXT NOT NULL,
        anchored_province_code TEXT,
        risk_flags TEXT NOT NULL,
        prior_wrong_address_return INTEGER NOT NULL DEFAULT 0,
        customer_phone_ref TEXT NOT NULL DEFAULT '',
        tier TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at REAL NOT NULL,
        decided_at REAL,
        decided_by TEXT NOT NULL DEFAULT '',
        customer_message_sent_at REAL,
        version INTEGER NOT NULL DEFAULT 0,
        fix_token_hash TEXT NOT NULL DEFAULT '',
        fix_token_expires_at REAL,
        last_validation_errors TEXT NOT NULL DEFAULT '[]',
        last_action TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_review_status ON review_items(status, created_at);
    """

    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock, self._conn:
            self._conn.executescript(self.DDL)

    def save_new(self, item: ReviewItem) -> bool:
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute(
                        f"INSERT INTO review_items ({', '.join(_COLUMNS)}) "
                        f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                        _item_to_row(item))
                return True
            except sqlite3.IntegrityError:
                return False  # duplicate id or duplicate order_id

    def get(self, item_id: str) -> Optional[ReviewItem]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM review_items WHERE id = ?", (item_id,)).fetchone()
            return _row_to_item(row) if row else None

    def get_by_order(self, order_id: str) -> Optional[ReviewItem]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM review_items WHERE order_id = ?", (order_id,)).fetchone()
            return _row_to_item(row) if row else None

    def update_if_version(self, item: ReviewItem, expected_version: int) -> bool:
        """Compare-and-swap: writes only if the stored version is still
        expected_version, then bumps the version on both sides."""
        with self._lock:
            row_values = _item_to_row(item)
            assignments = ", ".join(f"{c} = ?" for c in _COLUMNS if c not in ("id", "version"))
            values = [v for c, v in zip(_COLUMNS, row_values) if c not in ("id", "version")]
            with self._conn:
                cur = self._conn.execute(
                    f"UPDATE review_items SET {assignments}, version = version + 1 "
                    f"WHERE id = ? AND version = ?",
                    (*values, item.id, expected_version))
            if cur.rowcount != 1:
                return False
            item.version = expected_version + 1
            return True

    def list_all(self) -> List[ReviewItem]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM review_items ORDER BY created_at ASC").fetchall()
            return [_row_to_item(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class InMemoryReviewStore:
    """Same port, dict-backed. For unit tests of other modules only -
    it has the same CAS/unique semantics but does not survive restarts."""

    def __init__(self):
        self._items: Dict[str, ReviewItem] = {}
        self._by_order: Dict[str, str] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _copy(item: ReviewItem) -> ReviewItem:
        return ReviewItem(**{**item.__dict__,
                             "risk_flags": list(item.risk_flags),
                             "proposed_address": dict(item.proposed_address or {}),
                             "last_validation_errors": list(item.last_validation_errors)})

    def save_new(self, item: ReviewItem) -> bool:
        with self._lock:
            if item.id in self._items or item.order_id in self._by_order:
                return False
            self._items[item.id] = self._copy(item)
            self._by_order[item.order_id] = item.id
            return True

    def get(self, item_id: str) -> Optional[ReviewItem]:
        with self._lock:
            item = self._items.get(item_id)
            return self._copy(item) if item else None

    def get_by_order(self, order_id: str) -> Optional[ReviewItem]:
        with self._lock:
            item_id = self._by_order.get(order_id)
            return self.get(item_id) if item_id else None

    def update_if_version(self, item: ReviewItem, expected_version: int) -> bool:
        with self._lock:
            stored = self._items.get(item.id)
            if stored is None or stored.version != expected_version:
                return False
            item.version = expected_version + 1
            self._items[item.id] = self._copy(item)
            return True

    def list_all(self) -> List[ReviewItem]:
        with self._lock:
            return [self._copy(i) for i in sorted(self._items.values(), key=lambda x: x.created_at)]

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------
class ReviewQueue:
    GREEN_THRESHOLD = 0.90
    YELLOW_THRESHOLD = 0.65
    YELLOW_SLA_SECONDS = 15 * 60
    RED_CUSTOMER_WAIT_SECONDS = 4 * 3600
    FIX_TOKEN_TTL_SECONDS = 48 * 3600

    RED_FLAGS = {"too_short_input", "numeric_only", "province_anchor_mismatch",
                 "province_conflict", "carrier_province_mismatch", "no_candidate"}

    def __init__(self, messenger: Optional[CustomerMessenger] = None,
                 clock: Callable[[], float] = time.time,
                 store: Optional[ReviewStore] = None,
                 validator=None, gate=None,
                 token_secret: str = ""):
        self._store: ReviewStore = store if store is not None else SQLiteReviewStore(":memory:")
        self._messenger = messenger
        self._clock = clock
        self._validator = validator
        self._gate = gate
        self._token_secret = token_secret or ""

    @classmethod
    def with_sqlite(cls, path: str, **kwargs) -> "ReviewQueue":
        return cls(store=SQLiteReviewStore(path), **kwargs)

    def close(self) -> None:
        close = getattr(self._store, "close", None)
        if callable(close):
            close()

    # -- classification -------------------------------------------------------
    def classify(self, item: ReviewItem) -> Tier:
        flags = set(item.risk_flags)
        if item.prior_wrong_address_return or flags & self.RED_FLAGS:
            return Tier.RED
        if item.confidence < self.YELLOW_THRESHOLD:
            return Tier.RED
        if item.confidence >= self.GREEN_THRESHOLD and not flags:
            return Tier.GREEN
        return Tier.YELLOW

    # -- token handling ---------------------------------------------------------
    def _hash_token(self, token: str) -> str:
        if self._token_secret:
            return hmac.new(self._token_secret.encode(), token.encode(), hashlib.sha256).hexdigest()
        return hashlib.sha256(token.encode()).hexdigest()

    def _make_fix_token(self, item: ReviewItem) -> str:
        token = secrets.token_urlsafe(24)
        item.fix_token_hash = self._hash_token(token)
        item.fix_token_expires_at = self._clock() + self.FIX_TOKEN_TTL_SECONDS
        return token

    def issue_fix_token(self, item_id: str) -> str:
        item = self._get(item_id)
        token = self._make_fix_token(item)
        self._persist(item)
        return token

    def _verify_fix_token(self, item: ReviewItem, token: Optional[str]) -> None:
        """A supplied token is ALWAYS verified (hash + expiry). A token
        is only *required* in token-enforced mode (token_secret set):
        without a secret no customer link was ever issued, so internal
        and CS-driven confirmations are not blocked by a token nobody
        could have received."""
        if not token:
            if self._token_secret and item.fix_token_hash:
                raise InvalidTokenError("one-tap fix token is required for this item")
            return
        if not item.fix_token_hash:
            raise InvalidTokenError("no fix token was issued for this item")
        if item.fix_token_expires_at is not None and self._clock() > item.fix_token_expires_at:
            raise InvalidTokenError("one-tap fix token has expired")
        if not hmac.compare_digest(self._hash_token(token), item.fix_token_hash):
            raise InvalidTokenError("one-tap fix token does not match this order")

    # -- persistence helpers ------------------------------------------------------
    def _persist(self, item: ReviewItem) -> ReviewItem:
        expected = item.version
        if not self._store.update_if_version(item, expected):
            raise ConcurrencyError(
                f"review item {item.id} was modified concurrently (expected version {expected})")
        return item

    def _transition(self, item: ReviewItem, new_status: ReviewStatus, action: str) -> ReviewItem:
        if new_status == item.status:
            item.last_action = action
            return self._persist(item)
        allowed = TRANSITIONS.get(item.status, frozenset())
        if new_status not in allowed:
            raise InvalidTransitionError(
                f"cannot move review item {item.id} from {item.status.value} to {new_status.value}")
        item.status = new_status
        item.last_action = action
        return self._persist(item)

    def _require_cs_source(self, item: ReviewItem, action: str) -> None:
        if item.status not in CS_ACTION_SOURCES:
            raise InvalidTransitionError(
                f"CS action {action} is not allowed from {item.status.value} "
                f"(item {item.id}); CS may only act on PENDING_CS or ESCALATED items")

    def _get(self, item_id: str) -> ReviewItem:
        item = self._store.get(item_id)
        if item is None:
            raise KeyError(f"review item not found: {item_id}")
        return item

    def get(self, item_id: str) -> ReviewItem:
        return self._get(item_id)

    def get_by_order(self, order_id: str) -> Optional[ReviewItem]:
        return self._store.get_by_order(order_id)

    # -- intake ---------------------------------------------------------------
    def enqueue(self, item: ReviewItem) -> ReviewItem:
        existing = self._store.get_by_order(item.order_id)
        if existing is not None:
            return existing  # UNIQUE(order_id): idempotent intake
        item.created_at = self._clock()  # SLA is measured from intake, one clock
        item.tier = self.classify(item)
        fix_token: Optional[str] = None
        if item.tier == Tier.GREEN:
            item.status = ReviewStatus.AUTO_APPROVED
            item.decided_at = self._clock()
            item.decided_by = "auto:green"
            item.last_action = "enqueue_green"
        elif item.tier == Tier.YELLOW:
            item.status = ReviewStatus.PENDING_CS
            item.last_action = "enqueue_yellow"
        else:
            item.status = ReviewStatus.WAITING_CUSTOMER
            item.last_action = "enqueue_red"
            fix_token = self._make_fix_token(item)
        if not self._store.save_new(item):
            existing = self._store.get_by_order(item.order_id)
            if existing is not None:
                return existing
            raise ConcurrencyError(f"could not enqueue review item for order {item.order_id}")
        if fix_token is not None:
            self._ask_customer(item, fix_token=fix_token)
            self._persist(item)
        return item

    def _ask_customer(self, item: ReviewItem, fix_token: Optional[str] = None) -> None:
        if self._messenger is None:
            return
        if fix_token is None:
            fix_token = self._make_fix_token(item)
        try:
            sent = self._messenger.send_address_fix_link(
                item.customer_phone_ref, item.order_id,
                item.anchored_province_code, channels=["zalo", "app", "sms"],
                fix_token=fix_token)
        except TypeError:
            sent = self._messenger.send_address_fix_link(
                item.customer_phone_ref, item.order_id,
                item.anchored_province_code, channels=["zalo", "app", "sms"])
        if sent:
            item.customer_message_sent_at = self._clock()

    # -- CS actions ----------------------------------------------------------------
    def approve(self, item_id: str, cs_user: str,
                expected_version: Optional[int] = None) -> ReviewItem:
        item = self._get(item_id)
        self._require_cs_source(item, "approve")
        if expected_version is not None and item.version != expected_version:
            raise ConcurrencyError(
                f"review item {item_id} version {item.version} != expected {expected_version}")
        item.decided_at = self._clock()
        item.decided_by = cs_user
        return self._transition(item, ReviewStatus.APPROVED, "cs_approve")

    def reject_to_customer(self, item_id: str, cs_user: str,
                           expected_version: Optional[int] = None) -> ReviewItem:
        item = self._get(item_id)
        self._require_cs_source(item, "reject_to_customer")
        if expected_version is not None and item.version != expected_version:
            raise ConcurrencyError(
                f"review item {item_id} version {item.version} != expected {expected_version}")
        item.decided_at = self._clock()
        item.decided_by = cs_user
        item = self._transition(item, ReviewStatus.WAITING_CUSTOMER, "cs_reject_to_customer")
        fix_token = self._make_fix_token(item)
        self._ask_customer(item, fix_token=fix_token)
        return self._persist(item)

    # -- customer confirmation with mandatory re-validation -------------------------
    def _run_validator(self, item: ReviewItem, confirmed: dict):
        """Returns (outcome, errors): outcome in pass|review|fail|none."""
        validator = self._validator
        if validator is None:
            return "none", []
        try:
            if hasattr(validator, "validate_dict"):
                result = validator.validate_dict(confirmed, item)
            elif callable(validator):
                result = validator(confirmed, item)
            elif hasattr(validator, "validate"):
                from ..address.domain.schemas import AddressContract
                contract = AddressContract(
                    raw_input=item.raw_input,
                    province_code=confirmed.get("province_code"),
                    district_code=confirmed.get("district_code"),
                    ward_code=confirmed.get("ward_code"),
                    street=confirmed.get("street"),
                    house_number=confirmed.get("house_number"),
                    confidence=item.confidence)
                result = validator.validate(
                    contract, anchored_province_code=item.anchored_province_code)
            else:
                return "none", []
        except Exception as exc:  # validator itself failed: fail closed to CS
            return "review", [f"validator_error:{type(exc).__name__}"]
        if result is None:
            return "review", ["validator_unavailable"]
        decision = result.get("decision") if isinstance(result, dict) else getattr(result, "decision", None)
        decision = str(getattr(decision, "value", decision) or "").upper()
        raw_errors = result.get("errors") if isinstance(result, dict) else getattr(result, "errors", [])
        errors = [str(getattr(e, "code", e)) for e in (raw_errors or [])]
        if decision in ("OK", "VERIFIED", "PASS", "APPROVED"):
            return "pass", errors
        if decision in ("NEEDS_REVIEW", "NEED_CONFIRMATION", "REVIEW"):
            return "review", errors
        return "fail", errors or [f"validator_decision:{decision or 'UNKNOWN'}"]

    def _run_gate(self, item: ReviewItem, confirmed: dict) -> str:
        gate = self._gate
        if gate is None:
            return "none"
        try:
            result = gate(confirmed, item) if callable(gate) else gate.evaluate(confirmed, item)
        except Exception:
            return "review"
        status = result.get("status") if isinstance(result, dict) else getattr(result, "status", result)
        status = str(getattr(status, "value", status) or "").upper()
        if status in ("VERIFIED", "OK", "PASS"):
            return "pass"
        if status in ("NEED_CONFIRMATION", "NEEDS_REVIEW"):
            return "review"
        return "fail"

    def customer_confirmed(self, item_id: str, confirmed_address: dict,
                           token: Optional[str] = None) -> ReviewItem:
        item = self._get(item_id)
        if item.status not in CUSTOMER_CONFIRM_SOURCES:
            raise InvalidTransitionError(
                f"customer confirmation is not allowed from {item.status.value} (item {item_id})")
        self._verify_fix_token(item, token)
        confirmed = dict(confirmed_address or {})

        # 1) Anchored province is a hard bound - a forwarded link or a
        #    careless tap can never move the order to another province.
        errors: List[str] = []
        if item.anchored_province_code and confirmed.get("province_code") \
                and str(confirmed["province_code"]) != str(item.anchored_province_code):
            errors.append("PROVINCE_ANCHOR_MISMATCH")
        # 2) Structural completeness (fallback when no validator injected).
        if not confirmed.get("province_code") or not confirmed.get("district_code") \
                or not confirmed.get("ward_code"):
            errors.append("INCOMPLETE_ADMIN_CODES")
        # 3) Full validator + gate re-validation when injected.
        v_outcome, v_errors = self._run_validator(item, confirmed)
        if v_outcome == "fail":
            errors.extend(v_errors)
        g_outcome = self._run_gate(item, confirmed) if not errors else "none"

        if errors or v_outcome == "fail" or g_outcome == "fail":
            item.last_validation_errors = errors or v_errors or ["gate_rejected"]
            item.last_action = "customer_confirmed_revalidation_failed"
            if item.status == ReviewStatus.ESCALATED:
                return self._transition(item, ReviewStatus.WAITING_CUSTOMER,
                                        "customer_confirmed_revalidation_failed")
            return self._persist(item)

        item.proposed_address = confirmed
        item.last_validation_errors = []
        if v_outcome == "review" or g_outcome == "review":
            item.last_action = "customer_confirmed_needs_cs_review"
            if item.status == ReviewStatus.PENDING_CS:
                return self._persist(item)
            return self._transition(item, ReviewStatus.PENDING_CS,
                                    "customer_confirmed_needs_cs_review")

        item.decided_at = self._clock()
        item.decided_by = "customer:one_tap_link"
        return self._transition(item, ReviewStatus.APPROVED, "customer_confirmed_approved")

    # -- SLA / ops ---------------------------------------------------------------------
    def escalate_breaches(self) -> List[ReviewItem]:
        now = self._clock()
        breached: List[ReviewItem] = []
        for item in self._store.list_all():
            if item.status == ReviewStatus.PENDING_CS and now - item.created_at > self.YELLOW_SLA_SECONDS:
                breached.append(self._transition(item, ReviewStatus.ESCALATED, "sla_escalate_yellow"))
            elif item.status == ReviewStatus.WAITING_CUSTOMER and now - item.created_at > self.RED_CUSTOMER_WAIT_SECONDS:
                breached.append(self._transition(item, ReviewStatus.ESCALATED, "sla_escalate_red"))
        return breached

    def next_for_cs(self, limit: int = 20) -> List[ReviewItem]:
        pending = [i for i in self._store.list_all() if i.status in
                   (ReviewStatus.PENDING_CS, ReviewStatus.ESCALATED)]
        pending.sort(key=lambda i: (i.status != ReviewStatus.ESCALATED, i.created_at))
        return pending[:limit]

    def metrics(self) -> dict:
        items = self._store.list_all()
        by_tier: Dict[str, int] = {t.value: 0 for t in Tier}
        by_status: Dict[str, int] = {}
        review_seconds: List[float] = []
        for item in items:
            by_tier[item.tier.value] += 1
            by_status[item.status.value] = by_status.get(item.status.value, 0) + 1
            if item.decided_at is not None:
                review_seconds.append(item.decided_at - item.created_at)
        avg = (sum(review_seconds) / len(review_seconds)) if review_seconds else 0.0
        return {"by_tier": by_tier, "by_status": by_status,
                "avg_review_seconds": round(avg, 2),
                "queue_depth_cs": len(self.next_for_cs(limit=10000))}
