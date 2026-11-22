"""
Test hồi quy cho fix_observability_dead_code.

Chưa chạy trong phiên này (KHÔNG_CHẠY_LỆNH); CI chạy:
    python -m pytest tests/test_observability_dead_code.py -v

Bao phủ:
1) Mã nguồn không còn tên chết `regionLock` và __init__ chỉ gán
   region_lock đúng một lần; Observability giữ đúng object được truyền.
2) forbidden_default_rate tính theo SỐ CA vi phạm: một ca trúng cả hai
   mảnh chuỗi cấm ('Đăk Yă' và 'Mang Yang') vẫn chỉ tính 1 ca — tỉ lệ
   không bao giờ vượt 1.0 (bản cũ chia theo số lượt trúng).
3) Ca sạch -> rate 0 và passed True.
"""
import inspect
from pathlib import Path

from src.address.infrastructure import observability
from src.address.infrastructure.observability import (
    Decision,
    GoldenCase,
    Observability,
    run_golden_regression,
)

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "src" / "address" / "infrastructure" / "observability.py"
).read_text(encoding="utf-8")


def test_no_dead_region_lock_code():
    assert "regionLock" not in SOURCE
    assert SOURCE.count("self.region_lock =") == 1


def test_observability_keeps_passed_region_lock():
    import threading
    lock = threading.Lock()
    obs = Observability(region_lock=lock)
    assert obs.region_lock is lock
    assert Observability().region_lock is None


def _violating_run_one(raw):
    # Output chứa CẢ HAI mảnh cấm trong cùng một ca.
    return (Decision.REJECTED, "Xã Đăk Yă, Mang Yang, Gia Lai", "Gia Lai")


def _clean_run_one(raw):
    return (Decision.REJECTED, "", None)


def test_forbidden_rate_counts_cases_not_hits():
    cases = [GoldenCase("khong doc duoc", Decision.REJECTED, None, "c1")]
    result = run_golden_regression(_violating_run_one, cases)
    # 2 lượt trúng nhưng chỉ 1 ca: tỉ lệ phải là 1.0, không phải 2.0.
    assert result.forbidden_hits == 2
    assert result.forbidden_cases == 1
    assert result.forbidden_default_rate == 1.0
    assert result.forbidden_default_rate <= 1.0
    assert result.passed is False


def test_forbidden_rate_half_when_one_of_two_cases_violates():
    cases = [
        GoldenCase("khong doc duoc", Decision.REJECTED, None, "c1"),
        GoldenCase("73", Decision.REJECTED, None, "c2"),
    ]

    def run_one(raw):
        if raw == "khong doc duoc":
            return _violating_run_one(raw)
        return _clean_run_one(raw)

    result = run_golden_regression(run_one, cases)
    assert result.forbidden_cases == 1
    assert result.forbidden_default_rate == 0.5


def test_clean_run_has_zero_forbidden_rate():
    cases = [GoldenCase("73", Decision.REJECTED, None, "c1")]
    result = run_golden_regression(_clean_run_one, cases)
    assert result.forbidden_default_rate == 0.0
    assert result.passed is True


def test_run_golden_regression_signature_unchanged():
    params = list(inspect.signature(run_golden_regression).parameters)
    assert params == ["run_one", "cases"]
    assert observability.GOLDEN_CASES, "GOLDEN_CASES mặc định không được rỗng"
