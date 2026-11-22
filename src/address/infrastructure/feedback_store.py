"""
approach_16 — Vòng lặp feedback từ SPX/GHN/GHTK, correction store, active learning.

Luồng:
1) Nhận webhook / file đối soát từ hãng vận chuyển: delivered, failed,
   wrong_address, returned. Gắn với raw_address, resolved_address, provider,
   confidence ban đầu tại thời điểm chốt đơn.
2) Địa chỉ từng bị hoàn vì sai địa chỉ -> hạ điểm tin cậy, vào blacklist tạm
   (có TTL, không cấm vĩnh viễn vì địa chỉ có thể đã được khách sửa đúng).
3) Địa chỉ khách sửa sau hoàn và sau đó giao thành công -> ground truth,
   lưu vào address_corrections: cặp raw_input -> địa chỉ đúng, ưu tiên tra cứu
   theo SĐT + khu vực trước cả provider bên ngoài.
4) Active learning: hàng đợi gán nhãn ưu tiên ca margin thấp (ranker phân vân)
   và ca hoàn mới. Hàng tuần xuất dataset để retrain alias map + trọng số scoring.

Bản in-memory dưới đây là adapter tham chiếu; production thay bằng Postgres
(các bảng được mô tả trong docstring từng hàm) — giữ nguyên giao diện.
PII: SĐT chỉ lưu dạng hash để tra cứu, không lưu thô trong correction store.
"""
from __future__ import annotations

import hashlib
import time
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

BLACKLIST_TTL_SECONDS = 30 * 24 * 3600  # 30 ngày
SUCCESS_BOOST = 0.05
WRONG_ADDRESS_PENALTY = 0.30


class DeliveryOutcome(str, Enum):
    DELIVERED = "delivered"
    FAILED = "failed"
    WRONG_ADDRESS = "wrong_address"
    RETURNED = "returned"


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", (text or "").strip().lower())


def hash_phone(phone: str) -> str:
    return hashlib.sha256((phone or "").strip().encode("utf-8")).hexdigest()


@dataclass
class FeedbackRecord:
    order_id: str
    carrier: str                     # spx | ghn | ghtk | ...
    outcome: DeliveryOutcome
    raw_address: str
    resolved_address: dict | None
    provider: str | None             # provider đã gợi ý địa chỉ bị dùng
    initial_confidence: float
    ward_code: str | None = None
    received_at: float = field(default_factory=time.time)
    note: str | None = None


@dataclass
class Correction:
    raw_input_norm: str
    phone_hash: str
    province_code: str
    district_code: str
    ward_code: str
    resolved_address: dict
    verified_deliveries: int = 1     # số lần giao thành công với bản sửa này
    updated_at: float = field(default_factory=time.time)


@dataclass
class LabelTask:
    order_id: str
    raw_address: str
    reason: str                      # low_margin | returned | wrong_address
    priority: float
    created_at: float = field(default_factory=time.time)
    labeled_address: dict | None = None


class FeedbackStore:
    """Tables (production): address_feedback, address_corrections,
    address_blacklist_temp, active_learning_queue, alias_candidates."""

    def __init__(self) -> None:
        self._feedback: list[FeedbackRecord] = []
        self._corrections: dict[tuple[str, str], Correction] = {}
        self._blacklist_until: dict[str, float] = {}   # ward_code|raw_norm -> expiry
        self._ward_trust_delta: dict[str, float] = {}  # ward_code -> cộng/trừ điểm
        self._label_queue: list[LabelTask] = []
        self._alias_candidates: dict[str, dict[str, int]] = {}

    # -- 1) Tiếp nhận feedback từ hãng vận chuyển --------------------------- #
    def ingest_carrier_feedback(self, record: FeedbackRecord,
                                ranker_margin: float | None = None) -> None:
        self._feedback.append(record)
        key_raw = _norm(record.raw_address)
        if record.outcome in (DeliveryOutcome.WRONG_ADDRESS, DeliveryOutcome.RETURNED):
            if record.ward_code:
                self._ward_trust_delta[record.ward_code] = (
                    self._ward_trust_delta.get(record.ward_code, 0.0) - WRONG_ADDRESS_PENALTY)
                self._blacklist_until[f"{record.ward_code}|{key_raw}"] = (
                    time.time() + BLACKLIST_TTL_SECONDS)
            self._label_queue.append(LabelTask(
                record.order_id, record.raw_address,
                reason=record.outcome.value, priority=1.0))
        elif record.outcome == DeliveryOutcome.DELIVERED:
            if record.ward_code:
                self._ward_trust_delta[record.ward_code] = min(
                    0.30, self._ward_trust_delta.get(record.ward_code, 0.0) + SUCCESS_BOOST)
            # Giao thành công sau khi từng bị blacklist -> gỡ blacklist sớm.
            self._blacklist_until.pop(f"{record.ward_code}|{key_raw}", None)
        if ranker_margin is not None and ranker_margin < 0.12:
            self._label_queue.append(LabelTask(
                record.order_id, record.raw_address,
                reason="low_margin", priority=0.8))

    # -- 2) Correction store: bộ nhớ ưu tiên theo SĐT + khu vực ------------- #
    def record_customer_correction(self, raw_input: str, phone: str,
                                   resolved_address: dict,
                                   province_code: str, district_code: str,
                                   ward_code: str) -> Correction:
        """Chỉ gọi khi địa chỉ sửa đã GIAO THÀNH CÔNG ít nhất 1 lần."""
        key = (hash_phone(phone), _norm(raw_input))
        existing = self._corrections.get(key)
        if existing:
            existing.verified_deliveries += 1
            existing.resolved_address = resolved_address
            existing.province_code, existing.district_code, existing.ward_code = (
                province_code, district_code, ward_code)
            existing.updated_at = time.time()
            return existing
        correction = Correction(_norm(raw_input), key[0], province_code,
                                district_code, ward_code, resolved_address)
        self._corrections[key] = correction
        return correction

    def lookup_correction(self, raw_input: str, phone: str) -> Correction | None:
        """Tra trước provider ngoài. Khớp SĐT + raw đã chuẩn hóa mới được dùng."""
        return self._corrections.get((hash_phone(phone), _norm(raw_input)))

    # -- 3) Tín hiệu cho ranker --------------------------------------------- #
    def trust_delta_for_ward(self, ward_code: str) -> float:
        return self._ward_trust_delta.get(ward_code, 0.0)

    def is_temporarily_blacklisted(self, ward_code: str, raw_input: str) -> bool:
        expiry = self._blacklist_until.get(f"{ward_code}|{_norm(raw_input)}")
        return bool(expiry and expiry > time.time())

    def returned_ward_codes(self) -> set[str]:
        return {wc for wc, delta in self._ward_trust_delta.items() if delta < 0}

    # -- 4) Active learning + retrain hàng tuần ------------------------------ #
    def next_label_tasks(self, limit: int = 100) -> list[LabelTask]:
        pending = [t for t in self._label_queue if t.labeled_address is None]
        return sorted(pending, key=lambda t: t.priority, reverse=True)[:limit]

    def submit_label(self, order_id: str, labeled_address: dict) -> bool:
        for task in self._label_queue:
            if task.order_id == order_id and task.labeled_address is None:
                task.labeled_address = labeled_address
                return True
        return False

    def learn_alias_candidate(self, raw_token: str, canonical_name: str) -> None:
        bucket = self._alias_candidates.setdefault(_norm(raw_token), {})
        key = _norm(canonical_name)
        bucket[key] = bucket.get(key, 0) + 1

    def export_weekly_retrain_dataset(self, min_alias_support: int = 5) -> dict:
        """
        Xuất dữ liệu cho job retrain hàng tuần (ngoài module này):
        - corrections đã kiểm chứng để bổ sung bộ nhớ ưu tiên,
        - alias có support >= ngưỡng để xét duyệt đưa vào alias map,
        - feedback có nhãn outcome để hiệu chỉnh trọng số scoring.
        Không tự động áp alias chưa qua duyệt của vận hành.
        """
        return {
            "corrections": [
                {"raw_input_norm": c.raw_input_norm, "ward_code": c.ward_code,
                 "district_code": c.district_code, "province_code": c.province_code,
                 "verified_deliveries": c.verified_deliveries}
                for c in self._corrections.values()],
            "alias_candidates_reviewed": {
                raw: max(names, key=names.get)
                for raw, names in self._alias_candidates.items()
                if max(names.values()) >= min_alias_support},
            "feedback_count": len(self._feedback),
            "labeled_tasks": [
                {"raw_address": t.raw_address, "labeled_address": t.labeled_address}
                for t in self._label_queue if t.labeled_address is not None],
        }
