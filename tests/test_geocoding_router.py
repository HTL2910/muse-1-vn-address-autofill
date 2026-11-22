"""Tests cho fix_router_deadline_mismatch_leak."""
from __future__ import annotations
import sys, threading, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.address.infrastructure.geocoding_router import GeocodeCandidate, GeocodingRouter, ProviderHealth, ProviderStatus, RouterStatus
def _cand(provider: str, province: str, ward: str="w1") -> GeocodeCandidate:
    return GeocodeCandidate(provider=provider,province_code=province,district_code="d1",ward_code=ward,province_name=province,provider_score=0.9)
class FakeProvider:
    def __init__(self,name,results=None,delay: float=0.0,exc: Exception|None=None):
        self.name=name; self._results=list(results or []); self._delay=delay; self._exc=exc; self.calls=0; self._lock=threading.Lock()
    def geocode(self,query_ascii,query_diacritic):
        with self._lock: self.calls+=1
        if self._delay: time.sleep(self._delay)
        if self._exc is not None: raise self._exc
        return list(self._results)
def test_global_deadline_does_not_sum_provider_timeouts():
    providers=[FakeProvider(f"p{i}",results=[_cand(f"p{i}","66")],delay=1.0) for i in range(3)]
    router=GeocodingRouter(providers,global_timeout_seconds=0.4)
    try:
        start=time.monotonic(); result=router.geocode("cuor dang","Cuôr Đăng","66"); elapsed=time.monotonic()-start
        assert elapsed<1.2,f"elapsed={elapsed:.2f}s"
        assert result.status in (RouterStatus.UNVERIFIED,RouterStatus.ALL_PROVIDERS_DOWN)
        assert all(s==ProviderStatus.TIMEOUT for s in result.per_provider.values()); assert result.candidates==[]
    finally: router.close()
def test_mismatch_counted_per_request_not_per_candidate():
    bad=[_cand("goong","22",f"w{i}") for i in range(10)]; provider=FakeProvider("goong",results=bad); router=GeocodingRouter([provider])
    try:
        for _ in range(2):
            result=router.geocode("x","x","66"); assert result.candidates==[]; assert len(result.vetoed)==10; assert result.per_provider["goong"]==ProviderStatus.VETOED_PROVINCE_MISMATCH
        assert not router._health["goong"].is_open(time.time()); assert len(router._health["goong"]._events)==2
    finally: router.close()
def test_mixed_candidates_not_overwritten_to_vetoed():
    good=_cand("goong","66","good"); bad=[_cand("goong","22",f"bad{i}") for i in range(9)]; provider=FakeProvider("goong",results=[good]+bad); router=GeocodingRouter([provider])
    try:
        result=router.geocode("x","x","66"); assert len(result.candidates)==1; assert result.candidates[0].ward_code=="good"; assert len(result.vetoed)==9; assert result.per_provider["goong"]==ProviderStatus.OK; assert result.status==RouterStatus.VERIFIED_CANDIDATES; assert len(router._health["goong"]._events)==1
    finally: router.close()
def test_empty_result_does_not_record_success():
    provider=FakeProvider("goong",results=[]); router=GeocodingRouter([provider]); health=router._health["goong"]
    try:
        for _ in range(4): health.record_failure(time.time())
        assert health._failures==4; result=router.geocode("x","x","66"); assert result.candidates==[]; assert health._failures==4; health.record_failure(time.time()); assert health.is_open(time.time())
    finally: router.close()
def test_vetoed_only_status_is_distinct():
    provider=FakeProvider("goong",results=[_cand("goong","22")]); router=GeocodingRouter([provider])
    try:
        result=router.geocode("x","x","66"); assert result.status==RouterStatus.UNVERIFIED; assert "veto" in result.reason; assert result.candidates==[]
    finally: router.close()
def test_half_open_single_trial():
    health=ProviderHealth(name="goong",failure_threshold=2,cooldown_seconds=0.1); now=time.time(); health.record_failure(now); health.record_failure(now); assert health.is_open(now); time.sleep(0.15); later=time.time(); assert not health.is_open(later); assert health.try_acquire_half_open_trial(later) is True; assert health.try_acquire_half_open_trial(later) is False; health.record_success(); assert not health.is_open(time.time()); assert health.try_acquire_half_open_trial(time.time()) is True; health.release_half_open_trial()
def test_half_open_concurrent_geocode_only_one_calls_provider():
    provider=FakeProvider("goong",results=[_cand("goong","66")],delay=0.3); router=GeocodingRouter([provider]); health=router._health["goong"]; health.cooldown_seconds=0.1; health.failure_threshold=1; health.record_failure(time.time()); assert health.is_open(time.time()); time.sleep(0.15); results=[]
    def _call(): results.append(router.geocode("x","x","66"))
    threads=[threading.Thread(target=_call) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=5)
    try:
        assert provider.calls==1,f"calls={provider.calls}"; statuses=sorted(r.per_provider["goong"].value for r in results); assert ProviderStatus.CIRCUIT_OPEN.value in statuses; assert ProviderStatus.OK.value in statuses
    finally: router.close()
def test_close_is_graceful_and_geocode_after_close_is_down():
    provider=FakeProvider("goong",results=[_cand("goong","66")]); router=GeocodingRouter([provider]); result=router.geocode("x","x","66"); assert result.status==RouterStatus.VERIFIED_CANDIDATES; router.close(); router.close(); assert router._executor._shutdown is True; result2=router.geocode("x","x","66"); assert result2.status==RouterStatus.ALL_PROVIDERS_DOWN; assert result2.reason=="router_closed"
def test_provider_error_records_failure_once():
    provider=FakeProvider("goong",exc=RuntimeError("down")); router=GeocodingRouter([provider])
    try:
        result=router.geocode("x","x","66"); assert result.per_provider["goong"]==ProviderStatus.ERROR; assert router._health["goong"]._failures==1
    finally: router.close()
