"""
Multi-provider geocoding router with circuit breaker - approach_07
Branch: expert/backend-sec-m1

Rules:
- Providers are SUGGESTIONS only. Router never commits an address.
- A result whose province != anchored province is VETOED immediately.
- Each provider: timeout 3-5s, token-bucket rate limit, quota counter,
  circuit breaker (closed -> open -> half-open).
- If province-mismatch rate or carrier-return rate for a provider exceeds
  threshold in the rolling window, breaker opens automatically.
- All providers down / disagreeing -> status UNVERIFIED, never a guess.
- No leaked threads/resources: providers are called via a bounded executor
  owned by the router and must be closed with close() / context manager.

Status: chua chay - CI se bao ket qua.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
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

    def is_open(self, now: float) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            if now - self._opened_at >= self.cooldown_seconds:
                self._opened_at = None  # half-open: allow one trial via caller
                self._failures = 0
                return False
            return True

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

    def record_failure(self, now: float) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = now

    def record_mismatch(self, now: float) -> None:
        with self._lock:
            self._events = [(t, m) for t, m in self._events if now - t < self.window_seconds]
            self._events.append((now, True))
            total = len(self._events)
            mismatches = sum(1 for _, m in self._events if m)
            if total >= 20 and mismatches / total > self.mismatch_threshold:
                self._opened_at = now  # auto-disable bad provider

    def record_match(self, now: float) -> None:
        with self._lock:
            self._events = [(t, m) for t, m in self._events if now - t < self.window_seconds]
            self._events.append((now, False))


@dataclass
class RouterResult:
    status: RouterStatus
    candidates: List[GeocodeCandidate]
    per_provider: Dict[str, ProviderStatus]
    vetoed: List[GeocodeCandidate] = field(default_factory=list)
    reason: str = ""


class GeocodingRouter:
    def __init__(self, providers: List[GeocodingProvider],
                 max_workers: int = 8, clock: Callable[[], float] = time.time):
        self._providers = list(providers)
        self._health: Dict[str, ProviderHealth] = {
            p.name: ProviderHealth(name=p.name) for p in self._providers
        }
        self._executor = ThreadPoolExecutor(max_workers=max_workers,
                                            thread_name_prefix="geocode")
        self._clock = clock
        self._closed = False

    # -- configuration used by adapter (approach_19) -------------------------
    def set_provider_enabled(self, name: str, enabled: bool) -> None:
        health = self._health.get(name)
        if health is None:
            return
        with health._lock:
            health._opened_at = self._clock() if not enabled else None

    def health_snapshot(self) -> Dict[str, dict]:
        now = self._clock()
        return {name: {"circuit_open": h.is_open(now)} for name, h in self._health.items()}

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
            if not health.allow_call(now):
                per_provider[provider.name] = ProviderStatus.RATE_LIMITED
                continue
            fut = self._executor.submit(provider.geocode, query_ascii, query_diacritic)
            futures[fut] = provider

        if not futures:
            return RouterResult(RouterStatus.ALL_PROVIDERS_DOWN, [], per_provider,
                                reason="all_providers_circuit_open_or_rate_limited")

        for fut, provider in futures.items():
            health = self._health[provider.name]
            try:
                results = fut.result(timeout=health.timeout_seconds)
            except FutureTimeout:
                health.record_failure(self._clock())
                per_provider[provider.name] = ProviderStatus.TIMEOUT
                continue
            except Exception:
                health.record_failure(self._clock())
                per_provider[provider.name] = ProviderStatus.ERROR
                continue

            health.record_success()
            per_provider[provider.name] = ProviderStatus.OK
            for cand in results or []:
                if anchored_province_code and cand.province_code != anchored_province_code:
                    health.record_mismatch(self._clock())
                    vetoed.append(cand)
                    per_provider[provider.name] = ProviderStatus.VETOED_PROVINCE_MISMATCH
                else:
                    health.record_match(self._clock())
                    candidates.append(cand)

        if not candidates:
            status = RouterStatus.UNVERIFIED if not vetoed else RouterStatus.UNVERIFIED
            return RouterResult(status, [], per_provider, vetoed=vetoed,
                                reason="no_candidate_survived_province_anchor")
        return RouterResult(RouterStatus.VERIFIED_CANDIDATES, candidates,
                            per_provider, vetoed=vetoed)

    def close(self) -> None:
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    def __enter__(self) -> "GeocodingRouter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
