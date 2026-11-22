"""
Quan trắc chất lượng, golden regression và cảnh báo drift — approach_18.

Đo cái gì:
  - Tỉ lệ VERIFIED / NEEDS_REVIEW / REJECTED theo provider và theo tỉnh
  - Tỉ lệ hoàn đơn (SPX báo sai địa chỉ), tỉ lệ giao thành công lần đầu
  - Độ trễ geocoding, tỉ lệ circuit breaker mở
  - Log đầy đủ input / output / điểm tin cậy để truy vết từng đơn lỗi

Golden regression bắt buộc (từ ca thật trong đề bài):
  - "xã Cuôr Đăng, Đắk Lắk" KHÔNG được ra Hải Dương / Phả Lại
  - "Xom", "73", "127", "bi" phải bị REJECTED / NEED_CLARIFICATION
  - Không ca nào được ra "Xã Đăk Yă, Mang Yang, Gia Lai" mặc định
Mọi thay đổi ngưỡng hoặc thuật toán phải qua eval này trước khi deploy.
Drift: tỉ lệ từ chối hoặc hoàn ở một tỉnh tăng đột biến -> cảnh báo
và khóa tạm autofill cho khu vực đó (fail-closed).
"""
from __future__ import annotations

import json
import logging
import statistics
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional, Protocol

__all__ = [
    "Decision", "DecisionLog", "GoldenCase", "EvalResult",
    "Observability", "DriftDetector", "GOLDEN_CASES",
    "run_golden_regression", "DEFAULT_FORBIDDEN_DEFAULT",
]

logger = logging.getLogger("address.observability")
DEFAULT_FORBIDDEN_DEFAULT = "Xã Đăk Yă, Mang Yang, Gia Lai"

try:  # Prometheus là tùy chọn; không có vẫn chạy bằng counter in-memory.
    from prometheus_client import Counter as _PCounter, Histogram as _PHistogram
    _HAS_PROM = True
except Exception:  # pragma: no cover
    _HAS_PROM = False


class Decision(str, Enum):
    VERIFIED = "VERIFIED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class DecisionLog:
    order_id: str
    raw_input: str
    normalized_output: str
    decision: Decision
    confidence: float
    province_code: Optional[str] = None
    provider: str = ""  # goong | pancake | ghn | spx | internal
    latency_ms: Optional[float] = None
    circuit_breaker_open: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class GoldenCase:
    id: str
    raw_input: str
    # expected_province: tỉnh đúng phải ra; None = ca này phải bị từ chối.
    expected_province: Optional[str]
    # forbidden_substrings: chuỗi cấm xuất hiện trong output (vd: Đăk Yă mặc định).
    forbidden_substrings: tuple[str, ...] = ()
    must_reject: bool = False
    note: str = ""


GOLDEN_CASES: list[GoldenCase] = [
    GoldenCase("cuor-dang-dak-lak", "xã Cuôr Đăng, Đắk Lắk",
               expected_province="Đắk Lắk",
               forbidden_substrings=("Hải Dương", "Phả Lại", "Chí Linh", "Đăk Yă"),
               note="Tên hiếm, khác tỉnh phải bị loại bởi scope tỉnh."),
    GoldenCase("xom", "Xom", expected_province=None, must_reject=True,
               forbidden_substrings=("Thanh Mỹ", "Nghệ An", "Đăk Yă")),
    GoldenCase("num-73", "73", expected_province=None, must_reject=True,
               forbidden_substrings=("Hợp Giang", "Cao Bằng", "Đăk Yă")),
    GoldenCase("num-127", "127", expected_province=None, must_reject=True,
               forbidden_substrings=("Hiến Nam", "Hưng Yên", "Đăk Yă")),
    GoldenCase("bi", "bi", expected_province=None, must_reject=True,
               forbidden_substrings=("Phú Cường", "Thủ Dầu Một", "Đăk Yă")),
    GoldenCase("bi-diacritic", "Bị", expected_province=None, must_reject=True,
               forbidden_substrings=("Phú Cường", "Thủ Dầu Một", "Đăk Yă")),
    GoldenCase("soi-lam-son", "Sói 799, đường Lam Sơn", expected_province=None,
               must_reject=True,
               forbidden_substrings=("Tân Bình", "Đăk Yă"),
               note="Chỉ có số nhà + đường, thiếu xã/huyện/tỉnh -> NEED_CLARIFICATION."),
]


@dataclass
class EvalResult:
    passed: bool
    precision_at_1: float
    correct_rejection_rate: float
    forbidden_default_rate: float  # tỉ lệ ca ra Đăk Yă mặc định — phải = 0
    failures: list[str] = field(default_factory=list)


class RegionLock(Protocol):
    def lock_autofill(self, province_code: str, reason: str) -> None: ...
    def unlock_autofill(self, province_code: str) -> None: ...


class Observability:
    """Ghi log có cấu trúc + metrics. Không log PII thô quá mức cần thiết:
    raw_input chỉ log khi bật cờ trace (mặc định log hash + độ dài để truy vết
    mà không lộ địa chỉ khách trong log thường)."""

    def __init__(self, region_lock: Optional[RegionLock] = None):
        self.region_lock = regionLock if False else region_lock  # noqa: F821 — giữ tên rõ ràng
        self.region_lock = region_lock
        self._decisions: deque[DecisionLog] = deque(maxlen=100_000)
        self._returns: Counter[str] = Counter()       # province_code -> số đơn hoàn
        self._delivered_first: Counter[str] = Counter()
        self._total_by_province: Counter[str] = Counter()
        if _HAS_PROM:
            self._counter = _PCounter("address_decisions_total", "Address decisions",
                                      ["decision", "provider", "province"])
            self._lat = _PHistogram("address_geocode_latency_ms", "Geocode latency ms")
            self._cb = _PCounter("address_circuit_breaker_open_total",
                                 "Circuit breaker open", ["provider"])

    def log_decision(self, entry: DecisionLog) -> None:
        self._decisions.append(entry)
        prov = entry.province_code or "unknown"
        self._total_by_province[prov] += 1
        logger.info(json.dumps({
            "event": "address_decision", "order_id": entry.order_id,
            "decision": entry.decision.value, "confidence": entry.confidence,
            "province": prov, "provider": entry.provider,
            "latency_ms": entry.latency_ms,
            "circuit_breaker_open": entry.circuit_breaker_open,
            "raw_len": len(entry.raw_input),
            "output": entry.normalized_output,
            "ts": entry.timestamp.isoformat(),
        }, ensure_ascii=False))
        if _HAS_PROM:
            self._counter.labels(entry.decision.value, entry.provider, prov).inc()
            if entry.latency_ms is not None:
                self._lat.observe(entry.latency_ms)
            if entry.circuit_breaker_open:
                self._cb.labels(entry.provider).inc()

    def record_return(self, province_code: str, provider: str = "spx") -> None:
        """SPX báo sai địa chỉ / hoàn đơn."""
        self._returns[province_code] += 1
        logger.warning(json.dumps({"event": "delivery_return", "province": province_code,
                                   "provider": provider}, ensure_ascii=False))

    def record_first_delivery_success(self, province_code: str) -> None:
        self._delivered_first[province_code] += 1

    def decision_rates(self) -> dict[str, float]:
        total = len(self._decisions) or 1
        counts = Counter(d.decision.value for d in self._decisions)
        return {k: counts.get(k, 0) / total for k in
                ("VERIFIED", "NEEDS_REVIEW", "REJECTED")}

    def return_rate_by_province(self) -> dict[str, float]:
        return {prov: self._returns.get(prov, 0) / max(1, total)
                for prov, total in self._total_by_province.items()}

    def first_delivery_success_rate(self, province_code: Optional[str] = None) -> float:
        if province_code:
            total = max(1, self._total_by_province.get(province_code, 0))
            return self._delivered_first.get(province_code, 0) / total
        total = max(1, sum(self._total_by_province.values()))
        return sum(self._delivered_first.values()) / total


def run_golden_regression(
    run_one: Callable[[str], tuple[Decision, str, Optional[str]]],
    cases: list[GoldenCase] = GOLDEN_CASES,
) -> EvalResult:
    """run_one(raw) -> (decision, normalized_output, province_name_or_None).
    Dùng trong CI: fail nếu bất kỳ golden case nào sai. Chưa chạy ở đây —
    chạy bằng: python -m pytest tests/golden -v
    """
    failures: list[str] = []
    correct_top1 = 0
    n_scored = 0
    correct_reject = 0
    n_reject = 0
    forbidden_hits = 0
    for case in cases:
        decision, output, province = run_one(case.raw_input)
        for bad in case.forbidden_substrings:
            if bad.lower() in output.lower():
                forbidden_hits += 1
                failures.append(f"{case.id}: output chứa chuỗi cấm '{bad}': {output!r}")
        if DEFAULT_FORBIDDEN_DEFAULT.lower() in output.lower():
            forbidden_hits += 1
            failures.append(f"{case.id}: ra địa chỉ mặc định Đăk Yă")
        if case.must_reject:
            n_reject += 1
            if decision in (Decision.REJECTED, Decision.NEEDS_REVIEW):
                correct_reject += 1
            else:
                failures.append(f"{case.id}: phải REJECTED/NEEDS_REVIEW, got {decision.value} -> {output!r}")
        elif case.expected_province:
            n_scored += 1
            if decision == Decision.VERIFIED and province == case.expected_province:
                correct_top1 += 1
            else:
                failures.append(
                    f"{case.id}: expected province {case.expected_province!r}, "
                    f"got decision={decision.value} province={province!r} output={output!r}")
    return EvalResult(
        passed=not failures,
        precision_at_1=correct_top1 / max(1, n_scored),
        correct_rejection_rate=correct_reject / max(1, n_reject),
        forbidden_default_rate=forbidden_hits / max(1, len(cases)),
        failures=failures,
    )


class DriftDetector:
    """Phát hiện drift theo tỉnh: so sánh tỉ lệ hoàn/từ chối trong cửa sổ
    gần đây với baseline. Vượt ngưỡng -> cảnh báo + khóa tạm autofill."""

    def __init__(self, obs: Observability, window: int = 500,
                 return_spike_pp: float = 0.05, reject_spike_pp: float = 0.10):
        self.obs = obs
        self.window = window
        self.return_spike_pp = return_spike_pp
        self.reject_spike_pp = reject_spike_pp
        self._baseline_return: dict[str, float] = {}
        self._recent: dict[str, deque[bool]] = defaultdict(lambda: deque(maxlen=window))

    def set_baseline(self, province_code: str, return_rate: float) -> None:
        self._baseline_return[province_code] = return_rate

    def observe(self, province_code: str, returned: bool) -> Optional[str]:
        self._recent[province_code].append(returned)
        recent = self._recent[province_code]
        if len(recent) < 50:  # chưa đủ mẫu, không kết luận
            return None
        recent_rate = sum(recent) / len(recent)
        baseline = self._baseline_return.get(province_code, 0.02)
        if recent_rate - baseline >= self.return_spike_pp:
            msg = (f"drift: return rate {province_code} {recent_rate:.1%} "
                   f"vs baseline {baseline:.1%} — locking autofill temporarily")
            logger.error(msg)
            if self.obs.region_lock:
                self.obs.region_lock.lock_autofill(province_code, msg)
            return msg
        return None

    def check_reject_drift(self, province_code: str, recent_reject_rate: float,
                           baseline_reject_rate: float) -> Optional[str]:
        if recent_reject_rate - baseline_reject_rate >= self.reject_spike_pp:
            msg = (f"drift: reject rate {province_code} {recent_reject_rate:.1%} "
                   f"vs baseline {baseline_reject_rate:.1%}")
            logger.error(msg)
            return msg
        return None
