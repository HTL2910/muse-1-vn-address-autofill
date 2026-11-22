"""
approach_12 — Address Intelligence Service bất đồng bộ, idempotency và outbox.

Kiến trúc:
- Service độc lập, tách khỏi app và Pancake. Đơn hàng chỉ lưu raw_address.
- Trạng thái duy nhất: PENDING -> VERIFIED | NEEDS_REVIEW | REJECTED.
  Không có trạng thái "điền mặc định rồi cho đi".
- Idempotency-Key = sha256(order_id | phone | normalized_raw_address),
  unique constraint ở store -> webhook retry không tạo trùng vận đơn.
- Outbox Pattern: ghi kết quả + sự kiện outbox trong cùng một "giao dịch"
  (ở bản triển khai in-memory là cùng một lần khóa; khi nối DB thật, thay
  InMemoryStore bằng transaction SQL gồm bảng address_jobs + outbox_events).
- Queue có giới hạn concurrency, retry exponential backoff + jitter tối đa
  3 lần rồi vào Dead Letter Queue (DLQ). Worker auto-scale theo độ dài queue
  được mô hình bằng `desired_workers()` để hạ tầng (K8s HPA / consumer group)
  đọc làm tín hiệu; module này không tự hứa đã triển khai autoscaler.

Chưa chạy: cần nối store/queue thật (Postgres + Kafka/RabbitMQ) ở infrastructure
trước khi dùng production. Giao diện dưới đây cố ý giữ transport-agnostic để
REST/gRPC handler ở src/address/api/ bọc lên trên.
"""
from __future__ import annotations

import asyncio
import hashlib
import random
import time
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Protocol


class AddressStatus(str, Enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"


def make_idempotency_key(order_id: str, phone: str, raw_address: str) -> str:
    normalized = unicodedata.normalize("NFC", (raw_address or "").strip().lower())
    payload = f"{order_id.strip()}|{phone.strip()}|{normalized}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class AddressJob:
    idempotency_key: str
    order_id: str
    shop_id: str
    phone: str
    raw_address: str
    status: AddressStatus = AddressStatus.PENDING
    resolved_address: dict | None = None
    confidence: float = 0.0
    reason: str | None = None
    attempts: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class OutboxEvent:
    event_id: str
    idempotency_key: str
    event_type: str  # address.verified | address.needs_review | address.rejected
    payload: dict
    published: bool = False


class JobStore(Protocol):
    def get_or_create(self, job: AddressJob) -> tuple[AddressJob, bool]: ...
    def update(self, job: AddressJob) -> None: ...
    def append_outbox(self, event: OutboxEvent) -> None: ...
    def unpublished_outbox(self) -> list[OutboxEvent]: ...
    def mark_published(self, event_id: str) -> None: ...


class InMemoryStore:
    """Store tham chiếu cho test/dev. Production thay bằng Postgres transaction."""
    def __init__(self) -> None:
        self._jobs: dict[str, AddressJob] = {}
        self._outbox: list[OutboxEvent] = {}
        self._outbox_list: list[OutboxEvent] = []
        self._lock = asyncio.Lock()

    def get_or_create(self, job: AddressJob) -> tuple[AddressJob, bool]:
        if job.idempotency_key in self._jobs:
            return self._jobs[job.idempotency_key], False
        self._jobs[job.idempotency_key] = job
        return job, True

    def update(self, job: AddressJob) -> None:
        job.updated_at = time.time()
        self._jobs[job.idempotency_key] = job

    def append_outbox(self, event: OutboxEvent) -> None:
        self._outbox_list.append(event)

    def unpublished_outbox(self) -> list[OutboxEvent]:
        return [e for e in self._outbox_list if not e.published]

    def mark_published(self, event_id: str) -> None:
        for e in self._outbox_list:
            if e.event_id == event_id:
                e.published = True


# Pipeline xử lý 1 địa chỉ, do api/gateway cung cấp:
# (raw_address) -> (status, resolved_address, confidence, reason)
PipelineFn = Callable[[str], Awaitable[tuple[AddressStatus, dict | None, float, str | None]]]

MAX_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 2.0


class AddressService:
    def __init__(self, store: JobStore, pipeline: PipelineFn,
                 max_concurrency: int = 20) -> None:
        self._store = store
        self._pipeline = pipeline
        self._queue: asyncio.Queue[AddressJob] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self.dead_letter_queue: list[AddressJob] = []
        self._workers: list[asyncio.Task] = []

    # -- API đồng bộ phía đặt hàng: trả ngay PENDING, không chặn checkout --- #
    async def submit(self, order_id: str, shop_id: str, phone: str,
                     raw_address: str) -> AddressJob:
        key = make_idempotency_key(order_id, phone, raw_address)
        job, created = self._store.get_or_create(AddressJob(
            idempotency_key=key, order_id=order_id, shop_id=shop_id,
            phone=phone, raw_address=raw_address))
        if created:
            await self._queue.put(job)
        # created=False nghĩa là webhook retry / khách bấm lại: trả job cũ,
        # tuyệt đối không xử lý trùng, không tạo vận đơn thứ hai.
        return job

    def desired_workers(self, min_workers: int = 2, max_workers: int = 32) -> int:
        """Tín hiệu auto-scale: ~100 job chờ thêm 1 worker, kẹp trong [min, max]."""
        depth = self._queue.qsize()
        return max(min_workers, min(max_workers, min_workers + depth // 100))

    async def start(self, num_workers: int = 4) -> None:
        self._workers = [asyncio.create_task(self._worker())
                         for _ in range(num_workers)]

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers.clear()

    async def _worker(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                async with self._semaphore:
                    await self._process_with_retry(job)
            finally:
                self._queue.task_done()

    async def _process_with_retry(self, job: AddressJob) -> None:
        while True:
            job.attempts += 1
            try:
                status, resolved, confidence, reason = await self._pipeline(job.raw_address)
                # Pipeline không bao giờ được trả VERIFIED rỗng.
                if status == AddressStatus.VERIFIED and not resolved:
                    status, reason = AddressStatus.NEEDS_REVIEW, "verified_without_payload"
                job.status, job.resolved_address = status, resolved
                job.confidence, job.reason = confidence, reason
                self._store.update(job)
                self._store.append_outbox(OutboxEvent(
                    event_id=f"{job.idempotency_key}:{status.value}",
                    idempotency_key=job.idempotency_key,
                    event_type=f"address.{status.value.lower()}",
                    payload={"order_id": job.order_id, "shop_id": job.shop_id,
                             "status": status.value, "resolved_address": resolved,
                             "confidence": confidence, "reason": reason}))
                return
            except Exception as exc:  # provider timeout, DB chập chờn...
                if job.attempts >= MAX_ATTEMPTS:
                    job.status = AddressStatus.NEEDS_REVIEW
                    job.reason = f"pipeline_failed_after_retries: {type(exc).__name__}"
                    self._store.update(job)
                    self.dead_letter_queue.append(job)
                    return
                backoff = BASE_BACKOFF_SECONDS * (2 ** (job.attempts - 1))
                await asyncio.sleep(backoff + random.uniform(0, backoff * 0.3))

    async def relay_outbox(self, publish: Callable[[OutboxEvent], Awaitable[None]]) -> int:
        """Relay chạy định kỳ: publish sự kiện chưa gửi, đánh dấu sau khi thành công."""
        sent = 0
        for event in self._store.unpublished_outbox():
            await publish(event)
            self._store.mark_published(event.event_id)
            sent += 1
        return sent
