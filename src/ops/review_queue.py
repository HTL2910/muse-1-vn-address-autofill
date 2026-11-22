"""
Risk-tiered manual review queue - approach_15
Branch: expert/backend-sec-m1
Ops service: in-memory core + ports for persistence / messaging.
Not all orders are reviewed - only the risky middle. Protects revenue:
GREEN goes straight, YELLOW is 1-click fast, RED waits for the customer.

Tiers:
- GREEN  confidence >= 0.90 and hierarchy consistent -> auto-approve, no queue.
- YELLOW 0.65 <= confidence < 0.90, missing house number, duplicate ward name,
         or provider proposes a different province -> CS 1-click review.
- RED    confidence < 0.65, input too short, province conflict, prior return
         for wrong address -> hold + auto-message customer (Zalo/SMS/app)
         with a one-tap fix link to re-pick ward in the ANCHORED province.

SLA: YELLOW target 15 min in business hours, RED customer wait 4h then escalate.
Metrics: avg review seconds, queue depth by tier, SLA breach count.
Status: chua chay - CI se bao ket qua. Persistence/messaging via ports in prod.
"""
from __future__ import annotations

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


class CustomerMessenger(Protocol):
    def send_address_fix_link(self, phone_ref: str, order_id: str,
                              anchored_province_code: Optional[str],
                              channels: List[str]) -> bool:
        """Channels tried in order, e.g. ['zalo', 'app', 'sms']. One-tap link
        must only offer wards inside the anchored province."""
        ...


class ReviewQueue:
    GREEN_THRESHOLD = 0.90
    YELLOW_THRESHOLD = 0.65
    YELLOW_SLA_SECONDS = 15 * 60
    RED_CUSTOMER_WAIT_SECONDS = 4 * 3600

    RED_FLAGS = {"too_short_input", "numeric_only", "province_anchor_mismatch",
                 "province_conflict", "carrier_province_mismatch", "no_candidate"}

    def __init__(self, messenger: Optional[CustomerMessenger] = None,
                 clock: Callable[[], float] = time.time):
        self._items: Dict[str, ReviewItem] = {}
        self._messenger = messenger
        self._clock = clock
        self._review_seconds: List[float] = []

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

    # -- intake ---------------------------------------------------------------
    def enqueue(self, item: ReviewItem) -> ReviewItem:
        item.tier = self.classify(item)
        if item.tier == Tier.GREEN:
            item.status = ReviewStatus.AUTO_APPROVED
            item.decided_at = self._clock()
            item.decided_by = "auto:green"
        elif item.tier == Tier.YELLOW:
            item.status = ReviewStatus.PENDING_CS
        else:  # RED - hold and ask the customer, do not waste CS time guessing
            item.status = ReviewStatus.WAITING_CUSTOMER
            self._ask_customer(item)
        self._items[item.id] = item
        return item

    def _ask_customer(self, item: ReviewItem) -> None:
        if self._messenger is None:
            return
        sent = self._messenger.send_address_fix_link(
            item.customer_phone_ref, item.order_id,
            item.anchored_province_code, channels=["zalo", "app", "sms"])
        if sent:
            item.customer_message_sent_at = self._clock()

    # -- CS actions (designed for one-screen compare + keyboard shortcuts) ----
    # UI contract: A = approve proposed, E = edit ward in anchored province,
    # R = send back to customer, Esc = skip. Backend only records the decision.
    def approve(self, item_id: str, cs_user: str) -> ReviewItem:
        return self._decide(item_id, cs_user, ReviewStatus.APPROVED)

    def reject_to_customer(self, item_id: str, cs_user: str) -> ReviewItem:
        item = self._decide(item_id, cs_user, ReviewStatus.REJECTED_NEEDS_INFO)
        item.status = ReviewStatus.WAITING_CUSTOMER
        self._ask_customer(item)
        return item

    def customer_confirmed(self, item_id: str, confirmed_address: dict) -> ReviewItem:
        item = self._get(item_id)
        item.proposed_address = confirmed_address
        item.status = ReviewStatus.APPROVED
        item.decided_at = self._clock()
        item.decided_by = "customer:one_tap_link"
        return item

    def _decide(self, item_id: str, cs_user: str, status: ReviewStatus) -> ReviewItem:
        item = self._get(item_id)
        now = self._clock()
        self._review_seconds.append(now - item.created_at)
        item.status = status
        item.decided_at = now
        item.decided_by = cs_user
        return item

    def _get(self, item_id: str) -> ReviewItem:
        if item_id not in self._items:
            raise KeyError(f"review item not found: {item_id}")
        return self._items[item_id]

    # -- SLA / ops --------------------------------------------------------------
    def escalate_breaches(self) -> List[ReviewItem]:
        now = self._clock()
        breached: List[ReviewItem] = []
        for item in self._items.values():
            if item.status == ReviewStatus.PENDING_CS and now - item.created_at > self.YELLOW_SLA_SECONDS:
                item.status = ReviewStatus.ESCALATED; breached.append(item)
            elif item.status == ReviewStatus.WAITING_CUSTOMER and now - item.created_at > self.RED_CUSTOMER_WAIT_SECONDS:
                item.status = ReviewStatus.ESCALATED; breached.append(item)
        return breached

    def next_for_cs(self, limit: int = 20) -> List[ReviewItem]:
        pending = [i for i in self._items.values() if i.status in
                   (ReviewStatus.PENDING_CS, ReviewStatus.ESCALATED)]
        # Oldest first + RED escalations first: prevents queue becoming a bottleneck.
        pending.sort(key=lambda i: (i.status != ReviewStatus.ESCALATED, i.created_at))
        return pending[:limit]

    def metrics(self) -> dict:
        by_tier: Dict[str, int] = {t.value: 0 for t in Tier}
        by_status: Dict[str, int] = {}
        for item in self._items.values():
            by_tier[item.tier.value] += 1
            by_status[item.status.value] = by_status.get(item.status.value, 0) + 1
        avg = (sum(self._review_seconds) / len(self._review_seconds)) if self._review_seconds else 0.0
        return {"by_tier": by_tier, "by_status": by_status,
                "avg_review_seconds": round(avg, 2),
                "queue_depth_cs": len(self.next_for_cs(limit=10000))}
