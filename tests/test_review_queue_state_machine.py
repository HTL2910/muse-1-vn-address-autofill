"""Tests for fix_review_queue_state_machine. Chua chay - CI bao ket qua.
Run: python -m pytest tests/test_review_queue_state_machine.py -q
"""
import pytest

from src.ops.review_queue import (
    ConcurrencyError,
    InMemoryReviewStore,
    InvalidTokenError,
    InvalidTransitionError,
    ReviewItem,
    ReviewQueue,
    ReviewStatus,
    SQLiteReviewStore,
    Tier,
)


def _item(order="ORD-1", confidence=0.75, anchored="66", flags=None, **kw):
    return ReviewItem(order_id=order, confidence=confidence,
                      raw_input="xom 3 Cuor Dang Krong Ana Dak Lak",
                      proposed_address={"province_code": "66", "district_code": "652",
                                        "ward_code": "65201"},
                      anchored_province_code=anchored,
                      risk_flags=flags or [], **kw)


class _Messenger:
    def __init__(self):
        self.calls = []
    def send_address_fix_link(self, phone_ref, order_id, anchored, channels, fix_token=None):
        self.calls.append({"order_id": order_id, "fix_token": fix_token})
        return True


def test_enqueue_tiers_and_unique_order():
    q = ReviewQueue()
    green = q.enqueue(_item("ORD-G", confidence=0.95))
    assert green.tier == Tier.GREEN and green.status == ReviewStatus.AUTO_APPROVED
    yellow = q.enqueue(_item("ORD-Y", confidence=0.75))
    assert yellow.tier == Tier.YELLOW and yellow.status == ReviewStatus.PENDING_CS
    red = q.enqueue(_item("ORD-R", confidence=0.40))
    assert red.tier == Tier.RED and red.status == ReviewStatus.WAITING_CUSTOMER
    again = q.enqueue(_item("ORD-Y", confidence=0.75))
    assert again.id == yellow.id  # UNIQUE(order_id): no duplicate entry
    assert q.metrics()["queue_depth_cs"] == 1


def test_state_machine_blocks_double_approve_and_wrong_state():
    q = ReviewQueue()
    yellow = q.enqueue(_item("ORD-Y"))
    approved = q.approve(yellow.id, "cs-a")
    assert approved.status == ReviewStatus.APPROVED
    with pytest.raises(InvalidTransitionError):
        q.approve(yellow.id, "cs-b")  # terminal: second approve is an error
    red = q.enqueue(_item("ORD-R", confidence=0.40))
    with pytest.raises(InvalidTransitionError):
        q.approve(red.id, "cs-a")  # WAITING_CUSTOMER cannot be CS-approved directly


def test_reject_to_customer_and_sla_escalation():
    now = [1000.0]
    q = ReviewQueue(clock=lambda: now[0])
    yellow = q.enqueue(_item("ORD-Y"))
    rejected = q.reject_to_customer(yellow.id, "cs-a")
    assert rejected.status == ReviewStatus.WAITING_CUSTOMER
    yellow2 = q.enqueue(_item("ORD-Y2"))
    now[0] += ReviewQueue.YELLOW_SLA_SECONDS + 1
    breached = q.escalate_breaches()
    assert [b.id for b in breached] == [yellow2.id]
    assert breached[0].status == ReviewStatus.ESCALATED


def test_optimistic_concurrency_and_persistence(tmp_path):
    db = str(tmp_path / "review.db")
    q1 = ReviewQueue.with_sqlite(db)
    yellow = q1.enqueue(_item("ORD-Y"))
    with pytest.raises(ConcurrencyError):
        q1.approve(yellow.id, "cs-a", expected_version=yellow.version + 99)
    approved = q1.approve(yellow.id, "cs-a", expected_version=yellow.version)
    assert approved.version == yellow.version + 1
    # A brand-new queue over the same DB file still sees the decision:
    # the store is the source of truth, not process memory.
    q2 = ReviewQueue.with_sqlite(db)
    loaded = q2.get_by_order("ORD-Y")
    assert loaded is not None and loaded.status == ReviewStatus.APPROVED


def test_customer_confirmed_cross_province_is_not_approved():
    q = ReviewQueue()
    red = q.enqueue(_item("ORD-R", confidence=0.40, anchored="66"))
    result = q.customer_confirmed(red.id, {"province_code": "34", "district_code": "340",
                                           "ward_code": "34001"})
    assert result.status == ReviewStatus.WAITING_CUSTOMER
    assert "PROVINCE_ANCHOR_MISMATCH" in result.last_validation_errors
    ok = q.customer_confirmed(red.id, {"province_code": "66", "district_code": "652",
                                       "ward_code": "65201"})
    assert ok.status == ReviewStatus.APPROVED
    assert ok.decided_by == "customer:one_tap_link"


def test_customer_confirmed_validator_block_and_review_routing():
    def blocking(confirmed, item):
        return {"decision": "BLOCKED", "errors": [{"code": "WARD_MISMATCH"}]}
    q = ReviewQueue(validator=blocking)
    red = q.enqueue(_item("ORD-R", confidence=0.40))
    result = q.customer_confirmed(red.id, {"province_code": "66", "district_code": "652",
                                           "ward_code": "65201"})
    assert result.status == ReviewStatus.WAITING_CUSTOMER
    assert "WARD_MISMATCH" in result.last_validation_errors

    def reviewing(confirmed, item):
        return {"decision": "NEEDS_REVIEW", "errors": []}
    q2 = ReviewQueue(validator=reviewing)
    red2 = q2.enqueue(_item("ORD-R2", confidence=0.40))
    routed = q2.customer_confirmed(red2.id, {"province_code": "66", "district_code": "652",
                                             "ward_code": "65201"})
    assert routed.status == ReviewStatus.PENDING_CS  # not auto-approved


def test_fix_token_required_wrong_and_expired():
    messenger = _Messenger()
    now = [1000.0]
    q = ReviewQueue(messenger=messenger, clock=lambda: now[0], token_secret="s3cret")
    red = q.enqueue(_item("ORD-R", confidence=0.40))
    token = messenger.calls[0]["fix_token"]
    assert token  # the link sent to the customer carries a token
    valid = {"province_code": "66", "district_code": "652", "ward_code": "65201"}
    with pytest.raises(InvalidTokenError):
        q.customer_confirmed(red.id, valid, token="wrong-token")
    with pytest.raises(InvalidTokenError):
        q.customer_confirmed(red.id, valid)  # missing token
    now[0] += ReviewQueue.FIX_TOKEN_TTL_SECONDS + 1
    with pytest.raises(InvalidTokenError):
        q.customer_confirmed(red.id, valid, token=token)  # expired
    fresh = q.issue_fix_token(red.id)
    approved = q.customer_confirmed(red.id, valid, token=fresh)
    assert approved.status == ReviewStatus.APPROVED


def test_inmemory_store_has_same_cas_semantics():
    store = InMemoryReviewStore()
    q = ReviewQueue(store=store)
    yellow = q.enqueue(_item("ORD-Y"))
    stale = store.get(yellow.id)
    q.approve(yellow.id, "cs-a")
    assert store.update_if_version(stale, stale.version) is False
