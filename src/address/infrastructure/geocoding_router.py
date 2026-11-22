"""Router — deadline chung, đếm mismatch theo request, half-open
single trial, close(wait=True). Giữ nguyên contract cũ."""
from __future__ import annotations
import inspect, threading, time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, wait
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Protocol
class ProviderStatus(str, Enum):
    OK="OK"; TIMEOUT="TIMEOUT"; ERROR="ERROR"; VETOED_PROVINCE_MISMATCH="VETOED_PROVINCE_MISMATCH"; CIRCUIT_OPEN="CIRCUIT_OPEN"; RATE_LIMITED="RATE_LIMITED"
class RouterStatus(str, Enum):
    VERIFIED_CANDIDATES="VERIFIED_CANDIDATES"; UNVERIFIED="UNVERIFIED"; ALL_PROVIDERS_DOWN="ALL_PROVIDERS_DOWN"
@dataclass
class GeocodeCandidate:
    provider: str; province_code: str; district_code: str=""; ward_code: str=""; province_name: str=""; district_name: str=""; ward_name: str=""; detail: str=""; lat: Optional[float]=None; lng: Optional[float]=None; provider_score: float=0.0; raw_response: dict=field(default_factory=dict)
class GeocodingProvider(Protocol):
    name: str
    def geocode(self, query_ascii: str, query_diacritic: str) -> List[GeocodeCandidate]: ...
@dataclass
class ProviderHealth:
    name: str; failure_threshold: int=5; mismatch_threshold: float=0.20; window_seconds: int=900; cooldown_seconds: int=300; timeout_seconds: float=4.0; quota_per_minute: int=300
    _failures: int=0; _opened_at: Optional[float]=None; _events: List[tuple[float,bool]]=field(default_factory=list); _call_times: List[float]=field(default_factory=list); _lock: threading.Lock=field(default_factory=threading.Lock,repr=False); _half_open_trial_in_use: bool=False
    def is_open(self, now: float) -> bool:
        with self._lock: return self._opened_at is not None and now-self._opened_at<self.cooldown_seconds
    def is_half_open(self, now: float) -> bool:
        with self._lock: return self._opened_at is not None and now-self._opened_at>=self.cooldown_seconds
    def try_acquire_half_open_trial(self, now: float) -> bool:
        with self._lock:
            if self._opened_at is None: return True
            if now-self._opened_at<self.cooldown_seconds: return False
            if self._half_open_trial_in_use: return False
            self._half_open_trial_in_use=True; return True
    def release_half_open_trial(self) -> None:
        with self._lock: self._half_open_trial_in_use=False
    def set_enabled(self, enabled: bool, now: float) -> None:
        with self._lock: self._half_open_trial_in_use=False; self._opened_at=None if enabled else now
        if enabled: self._failures=0
    def allow_call(self, now: float) -> bool:
        with self._lock:
            self._call_times=[t for t in self._call_times if now-t<60]
            if len(self._call_times)>=self.quota_per_minute: return False
            self._call_times.append(now); return True
    def record_success(self) -> None:
        with self._lock: self._failures=0; self._opened_at=None; self._half_open_trial_in_use=False
    def record_failure(self, now: float) -> None:
        with self._lock:
            was=self._half_open_trial_in_use; self._half_open_trial_in_use=False; self._failures+=1
            if was or self._failures>=self.failure_threshold: self._opened_at=now
    def record_mismatch(self, now: float) -> None:
        with self._lock:
            was=self._half_open_trial_in_use; self._half_open_trial_in_use=False
            self._events=[(t,m) for t,m in self._events if now-t<self.window_seconds]; self._events.append((now,True))
            total=len(self._events); mism=sum(1 for _,m in self._events if m)
            if was or (total>=20 and mism/total>self.mismatch_threshold): self._opened_at=now
    def record_match(self, now: float) -> None:
        with self._lock:
            was=self._half_open_trial_in_use; self._half_open_trial_in_use=False
            self._events=[(t,m) for t,m in self._events if now-t<self.window_seconds]; self._events.append((now,False))
            if was: self._opened_at=None; self._failures=0
@dataclass
class RouterResult:
    status: RouterStatus; candidates: List[GeocodeCandidate]; per_provider: Dict[str,ProviderStatus]; vetoed: List[GeocodeCandidate]=field(default_factory=list); reason: str=""
class GeocodingRouter:
    def __init__(self, providers: List[GeocodingProvider], max_workers: int=8, clock: Callable[[],float]=time.time, global_timeout_seconds: float=4.0):
        self._providers=list(providers); self._health={p.name:ProviderHealth(name=p.name) for p in self._providers}; self._executor=ThreadPoolExecutor(max_workers=max_workers,thread_name_prefix="geocode"); self._clock=clock; self._global_timeout_seconds=float(global_timeout_seconds); self._closed=False; self._close_lock=threading.Lock()
    def set_provider_enabled(self, name: str, enabled: bool) -> None:
        h=self._health.get(name)
        if h is not None: h.set_enabled(enabled,self._clock())
    def health_snapshot(self) -> Dict[str,dict]:
        now=self._clock(); return {n:{"circuit_open":h.is_open(now)} for n,h in self._health.items()}
    @staticmethod
    def _call_provider(provider, query_ascii, query_diacritic, timeout_seconds):
        try: params=inspect.signature(provider.geocode).parameters
        except (TypeError,ValueError): params={}
        if "timeout" in params: return provider.geocode(query_ascii,query_diacritic,timeout=timeout_seconds)
        if "timeout_seconds" in params: return provider.geocode(query_ascii,query_diacritic,timeout_seconds=timeout_seconds)
        return provider.geocode(query_ascii,query_diacritic)
    def geocode(self, query_ascii: str, query_diacritic: str, anchored_province_code: Optional[str]) -> RouterResult:
        if self._closed: return RouterResult(RouterStatus.ALL_PROVIDERS_DOWN,[],{},reason="router_closed")
        now=self._clock(); per_provider: Dict[str,ProviderStatus]={}; candidates: List[GeocodeCandidate]=[]; vetoed: List[GeocodeCandidate]=[]
        futures={}
        for provider in self._providers:
            health=self._health[provider.name]
            if health.is_open(now): per_provider[provider.name]=ProviderStatus.CIRCUIT_OPEN; continue
            if not health.try_acquire_half_open_trial(now): per_provider[provider.name]=ProviderStatus.CIRCUIT_OPEN; continue
            if not health.allow_call(now): health.release_half_open_trial(); per_provider[provider.name]=ProviderStatus.RATE_LIMITED; continue
            futures[self._executor.submit(self._call_provider,provider,query_ascii,query_diacritic,health.timeout_seconds)]=provider
        if not futures: return RouterResult(RouterStatus.ALL_PROVIDERS_DOWN,[],per_provider,reason="all_providers_circuit_open_or_rate_limited")
        deadline=time.monotonic()+self._global_timeout_seconds; remaining=deadline-time.monotonic()
        done,not_done=wait(set(futures),timeout=max(0.0,remaining)) if remaining>0 else (set(),set(futures))
        for fut in not_done:
            provider=futures[fut]; health=self._health[provider.name]
            try: fut.cancel()
            except Exception: pass
            health.record_failure(self._clock()); per_provider[provider.name]=ProviderStatus.TIMEOUT
        for fut in done:
            provider=futures[fut]; health=self._health[provider.name]
            try: results=fut.result(timeout=0)
            except FutureTimeout: health.record_failure(self._clock()); per_provider[provider.name]=ProviderStatus.TIMEOUT; continue
            except Exception: health.record_failure(self._clock()); per_provider[provider.name]=ProviderStatus.ERROR; continue
            results=results or []
            if not results:
                health.release_half_open_trial(); per_provider[provider.name]=ProviderStatus.OK; continue
            survived=[c for c in results if not (anchored_province_code and c.province_code!=anchored_province_code)]
            vetoed_list=[c for c in results if anchored_province_code and c.province_code!=anchored_province_code]
            vetoed.extend(vetoed_list)
            if survived:
                health.record_success(); health.record_match(self._clock()); per_provider[provider.name]=ProviderStatus.OK; candidates.extend(survived)
            else:
                health.record_mismatch(self._clock()); per_provider[provider.name]=ProviderStatus.VETOED_PROVINCE_MISMATCH
        if not candidates:
            if vetoed: return RouterResult(RouterStatus.UNVERIFIED,[],per_provider,vetoed=vetoed,reason="all_candidates_vetoed_province_mismatch")
            down={ProviderStatus.CIRCUIT_OPEN,ProviderStatus.RATE_LIMITED,ProviderStatus.TIMEOUT,ProviderStatus.ERROR}
            if per_provider and all(s in down for s in per_provider.values()): return RouterResult(RouterStatus.ALL_PROVIDERS_DOWN,[],per_provider,vetoed=vetoed,reason="all_providers_down_or_timed_out")
            return RouterResult(RouterStatus.UNVERIFIED,[],per_provider,vetoed=vetoed,reason="no_candidate_returned")
        return RouterResult(RouterStatus.VERIFIED_CANDIDATES,candidates,per_provider,vetoed=vetoed)
    def close(self) -> None:
        with self._close_lock:
            if self._closed: return
            self._closed=True
        try: self._executor.shutdown(wait=True,cancel_futures=True)
        except TypeError: self._executor.shutdown(wait=True)
    def __enter__(self) -> "GeocodingRouter": return self
    def __exit__(self,*exc) -> None: self.close()
