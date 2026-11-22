"""
Multi-provider geocoding router with circuit breaker - approach_07
Branch: expert/backend-sec-m1, fix: fix_router_deadline_mismatch_leak

Fixes (giữ nguyên contract: RouterResult.status/candidates/
per_provider/vetoed, GeocodeCandidate, ProviderHealth API):
1. Deadline chung: trước đây `fut.result(timeout=4s)` chờ tuần tự
   từng future -> 3 provider x 4s = 12s. Nay deadline chung
   `global_timeout_seconds` (mặc định 4s) cho toàn bộ lần geocode,
   dùng `concurrent.futures.wait(..., timeout=remaining)`; future
   nào quá deadline bị `cancel()` + TIMEOUT ngay, không chặn các
   provider khác. Deadline đo bằng `time.monotonic()`, không dùng
   clock giả của health (clock giả sẽ làm wait không bao giờ hết).
2. Đếm mismatch/match theo REQUEST, không theo candidate:
   trước đây 1 response 10 ứng viên sai = 10 lần mismatch -> mở
   breaker oan sau 2 lần gọi. Nay mỗi provider call chỉ ghi 1 event:
   sống sót >=1 -> `record_success()` + `record_match()` 1 lần,
   per_provider=OK (kể cả khi có cả ứng viên vetoed); toàn bộ bị
   veto -> `record_mismatch()` 1 lần, per_provider=VETOED; rỗng ->
   không record_success/match/mismatch, không reset failure counter.
   `record_success` chỉ khi có ít nhất 1 ứng viên sống sót.
3. Half-open single trial + rò rỉ executor:
   - `is_open()` trước đây tự reset `_opened_at` khi hết cooldown,
     nhiều request đồng thời cùng thấy đóng (thundering herd).
     Nay `is_open()` không có side-effect; trial half-open được
     cấp phát nguyên tử qua `try_acquire_half_open_trial()` — chỉ
     1 caller được thử, các caller khác nhận CIRCUIT_OPEN.
     `record_success/record_failure/record_mismatch/record_match`
     giải phóng trial; trial thất bại mở lại breaker ngay.
   - Provider phải tôn trọng timeout: router truyền `timeout=`
     cho provider nếu signature của nó nhận timeout, ngược lại
     gọi như cũ và dựa vào deadline chung để không chặn.
     `close()` dùng `shutdown(wait=True, cancel_futures=True)`
     (graceful) thay cho `wait=False`; gọi 2 lần vẫn an toàn,
     geocode sau close trả ALL_PROVIDERS_DOWN.
4. Xóa code chết: `status = UNVERIFIED if not vetoed else UNVERIFIED`.
   Nay phân biệt: toàn bộ bị veto -> UNVERIFIED/vetoed;
   không ứng viên nào và mọi provider đều down/timeout/error ->
   ALL_PROVIDERS_DOWN; còn lại -> UNVERIFIED/no_candidate.

Status: chua chay - CI se bao ket qua.
"""
from __future__ import annotations

import inspect
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, wait
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Protocol


class ProviderStatus(str, Enum):
    OK = "OK"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"
    VETOED_PROVINCE_MISMATCH = "VETOED_PROVINCE_MISMATCH"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    RATE_LIMITED = "RATE_LIMITED"


class RouterStatus(str, Enum):
    VERIFIED_CANDIDATES = "VERIFIED_CANDIDATES"
    UNVERIFIED = "UNVERIFIED"          # fail-closed: go to review, do not guess
    ALL_PROVIDERS_DOWN = "ALL_PROVIDERS_DOWN"


@dataclass
class GeocodeCandidate:
    provider: str
    province_code: str
    district_code: str = ""
    ward_code: str = ""
    province_name: str = ""
    district_name: str = ""
    ward_name: str = ""
    detail: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    provider_score: float = 0.0  # provider's own score, never trusted alone
    raw_response: dict = field(default_factory=dict)


class GeocodingProvider(Protocol):
    name: str
    def geocode(self, query_ascii: str, query_diacritic: str) -> List[GeocodeCandidate]:
        ...


@dataclass
class ProviderHealth:
    name: str
    failure_threshold: int = 5
    mismatch_threshold: float = 0.20      # >20% province mismatch in window
    window_seconds: int = 900             # 15 min (spec allows 15m-1h)
    cooldown_seconds: int = 300
    timeout_seconds: float = 4.0
    quota_per_minute: int = 300
    _failures: int = 0
    _opened_at: Optional[float] = None
    _events: List[tuple[float, bool]] = field(default_factory=list)  # (ts, is_mismatch)
    _call_times: List[float] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # Half-open single trial: chỉ 1 caller được thử sau cooldown.
    _half_open_trial_in_use: bool = False

    # -- circuit breaker: is_open() KHÔNG có side-effect -----------------
    def is_open(self, now: float) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            return now - self._opened_at < self.cooldown_seconds

    def is_half_open(self, now: float) -> bool:
        with self._lock:
            return self._opened_at is not None and now - self._opened_at >= self.cooldown_seconds

    def try_acquire_half_open_trial(self, now: float) -> bool:
        """Trả True nếu được phép gọi (đóng, hoặc half-open và chưa ai thử).
        Trả False nếu breaker đang mở, hoặc trial half-open đã có người giữ."""
        with self._lock:
            if self._opened_at is None:
                return True
            if now - self._opened_at < self.cooldown_seconds:
                return False
            if self._half_open_trial_in_use:
                return False
            self._half_open_trial_in_use = True
            return True

    def release_half_open_trial(self) -> None:
        with self._lock:
            self._half_open_trial_in_use = False

    def set_enabled(self, enabled: bool, now: float) -> None:
        with self._lock:
            self._half_open_trial_in_use = False
            self._opened_at = None if enabled else now
            if enabled:
                self._failures = 0

    def allow_call(self, now: float) -> bool:
        with self._lock:
            self._call_times = [t for t in self._call_times if now - t < 60]
            if len(self._call_times) >= self.quota_per_minute:
                return False
            self._call_times.append(now)
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._half_open_trial_in_use = False

    def record_failure(self, now: float) -> None:
        with self._lock:
            was_trial = self._half_open_trial_in_use
            self._half_open_trial_in_use = False
            self._failures += 1
            # Trial half-open thất bại -> mở lại ngay, không đợi đủ threshold.
            if was_trial or self._failures >= self.failure_threshold:
                self._opened_at = now

    def record_mismatch(self, now: float) -> None:
        """1 event cho 1 request (không phải 1 event cho 1 candidate)."""
        with self._lock:
            was_trial = self._half_open_trial_in_use
            self._half_open_trial_in_use = False
            self._events = [(t, m) for t, m in self._events if now - t < self.window_seconds]
            self._events.append((now, True))
            total = len(self._events)
            mismatches = sum(1 for _, m in self._events if m)
            if was_trial or (total >= 20 and mismatches / total > self.mismatch_threshold):
                self._opened_at = now  # auto-disable bad provider / trial failed

    def record_match(self, now: float) -> None:
        """1 event cho 1 request có ít nhất 1 ứng viên sống sót."""
        with self._lock:
            was_trial = self._half_open_trial_in_use
            self._half_open_trial_in_use = False
            self._events = [(t, m) for t, m in self._events if now - t < self.window_seconds]
            self._events.append((now, False))
            if was_trial:
                # Trial half-open thành công -> đóng breaker.
                self._opened_at = None
                self._failures = 0


@dataclass
class RouterResult:
    status: RouterStatus
    candidates: List[GeocodeCandidate]
    per_provider: Dict[str, ProviderStatus]
    vetoed: List[GeocodeCandidate] = field(default_factory=list)
    reason: str = ""


class GeocodingRouter:
    def __init__(self, providers: List[GeocodingProvider],
                 max_workers: int = 8, clock: Callable[[], float] = time.time,
                 global_timeout_seconds: float = 4.0):
        self._providers = list(providers)
        self._health: Dict[str, ProviderHealth] = {
            p.name: ProviderHealth(name=p.name) for p in self._providers
        }
        self._executor = ThreadPoolExecutor(max_workers=max_workers,
                                            thread_name_prefix="geocode")
        self._clock = clock
        self._global_timeout_seconds = float(global_timeout_seconds)
        self._closed = False
        self._close_lock = threading.Lock()

    # -- configuration used by adapter (approach_19) -------------------------
    def set_provider_enabled(self, name: str, enabled: bool) -> None:
        health = self._health.get(name)
        if health is None:
            return
        health.set_enabled(enabled, self._clock())

    def health_snapshot(self) -> Dict[str, dict]:
        now = self._clock()
        # is_open() nay không có side-effect nên snapshot không ăn trial.
        return {name: {"circuit_open": h.is_open(now)} for name, h in self._health.items()}

    @staticmethod
    def _call_provider(provider: GeocodingProvider, query_ascii: str,
                       query_diacritic: str, timeout_seconds: float) -> List[GeocodeCandidate]:
        """Truyền timeout cho provider nếu nó nhận timeout; nếu không,
        gọi như cũ và deadline chung ở geocode() sẽ chặn việc chờ."""
        try:
            params = inspect.signature(provider.geocode).parameters
        except (TypeError, ValueError):
            params = {}
        if "timeout" in params:
            return provider.geocode(query_ascii, query_diacritic, timeout=timeout_seconds)  # type: ignore[call-arg]
        if "timeout_seconds" in params:
            return provider.geocode(query_ascii, query_diacritic, timeout_seconds=timeout_seconds)  # type: ignore[call-arg]
        return provider.geocode(query_ascii, query_diacritic)

    # -- main entry -----------------------------------------------------------
    def geocode(self, query_ascii: str, query_diacritic: str,
                anchored_province_code: Optional[str]) -> RouterResult:
        if self._closed:
            return RouterResult(RouterStatus.ALL_PROVIDERS_DOWN, [], {}, reason="router_closed")
        now = self._clock()
        per_provider: Dict[str, ProviderStatus] = {}
        candidates: List[GeocodeCandidate] = []
        vetoed: List[GeocodeCandidate] = []

        futures = {}
        for provider in self._providers:
            health = self._health[provider.name]
            if health.is_open(now):
                per_provider[provider.name] = ProviderStatus.CIRCUIT_OPEN
                continue
            if not health.try_acquire_half_open_trial(now):
                per_provider[provider.name] = ProviderStatus.CIRCUIT_OPEN
                continue
            if not health.allow_call(now):
                health.release_half_open_trial()
                per_provider[provider.name] = ProviderStatus.RATE_LIMITED
                continue
            fut = self._executor.submit(
                self._call_provider, provider, query_ascii, query_diacritic,
                health.timeout_seconds)
            futures[fut] = provider

        if not futures:
            return RouterResult(RouterStatus.ALL_PROVIDERS_DOWN, [], per_provider,
                                reason="all_providers_circuit_open_or_rate_limited")

        # Deadline chung cho toàn bộ lần geocode, đo bằng monotonic
        # (không dùng clock giả của health).
        deadline = time.monotonic() + self._global_timeout_seconds
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            done: set = set()
            not_done = set(futures)
        else:
            done, not_done = wait(set(futures), timeout=remaining)

        # Future quá deadline: cancel + TIMEOUT ngay, không chờ nó.
        for fut in not_done:
            provider = futures[fut]
            health = self._health[provider.name]
            try:
                fut.cancel()
            except Exception:
                pass
            health.record_failure(self._clock())
            per_provider[provider.name] = ProviderStatus.TIMEOUT

        for fut in done:
            provider = futures[fut]
            health = self._health[provider.name]
            try:
                results = fut.result(timeout=0)
            except FutureTimeout:
                health.record_failure(self._clock())
                per_provider[provider.name] = ProviderStatus.TIMEOUT
                continue
            except Exception:
                health.record_failure(self._clock())
                per_provider[provider.name] = ProviderStatus.ERROR
                continue

            results = results or []
            if not results:
                # Rỗng: không record_success, không reset failure counter,
                # không ghi match/mismatch. Provider đã trả lời, chỉ là
                # không có ứng viên.
                health.release_half_open_trial()
                per_provider[provider.name] = ProviderStatus.OK
                continue

            # Đếm theo REQUEST, không theo candidate.
            survived = [c for c in results
                        if not (anchored_province_code and c.province_code != anchored_province_code)]
            vetoed_list = [c for c in results
                           if anchored_province_code and c.province_code != anchored_province_code]
            vetoed.extend(vetoed_list)
            if survived:
                health.record_success()
                health.record_match(self._clock())
                # Mixed: có ứng viên đúng tỉnh -> OK, không bị ghi đè VETOED.
                per_provider[provider.name] = ProviderStatus.OK
                candidates.extend(survived)
            else:
                health.record_mismatch(self._clock())
                per_provider[provider.name] = ProviderStatus.VETOED_PROVINCE_MISMATCH

        if not candidates:
            if vetoed:
                return RouterResult(RouterStatus.UNVERIFIED, [], per_provider, vetoed=vetoed,
                                    reason="all_candidates_vetoed_province_mismatch")
            down_states = {ProviderStatus.CIRCUIT_OPEN, ProviderStatus.RATE_LIMITED,
                           ProviderStatus.TIMEOUT, ProviderStatus.ERROR}
            if per_provider and all(s in down_states for s in per_provider.values()):
                return RouterResult(RouterStatus.ALL_PROVIDERS_DOWN, [], per_provider,
                                    vetoed=vetoed, reason="all_providers_down_or_timed_out")
            return RouterResult(RouterStatus.UNVERIFIED, [], per_provider, vetoed=vetoed,
                                reason="no_candidate_returned")
        return RouterResult(RouterStatus.VERIFIED_CANDIDATES, candidates,
                            per_provider, vetoed=vetoed)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        # Graceful shutdown: chờ thread đang chạy xong (provider phải
        # tôn trọng timeout < router timeout, nếu không close có thể
        # chặn tới khi provider tự trả về — đó là cooperative
        # cancellation, không thể kill thread Python từ bên ngoài).
        try:
            self._executor.shutdown(wait=True, cancel_futures=True)
        except TypeError:  # Python < 3.9 không có cancel_futures
            self._executor.shutdown(wait=True)

    def __enter__(self) -> "GeocodingRouter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
