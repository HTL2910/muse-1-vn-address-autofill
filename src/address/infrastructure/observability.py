"""
Observability cho Address Pipeline — fix_observability_dead_code.

Nội dung sửa trong đợt này (giữ nguyên mọi contract công khai):
1) Xóa code chết trong Observability.__init__: dòng gán
   `self.region_lock = regionLock if False else region_lock` tham chiếu
   tên `regionLock` không tồn tại trong module và bị gán đè ngay sau đó.
   Nay chỉ còn đúng một lần gán `self.region_lock = region_lock`.
2) Sửa forbidden_default_rate trong run_golden_regression: trước đây tỉ
   lệ = forbidden_hits / len(cases), mà một ca có thể trúng nhiều chuỗi
   cấm nên tỉ lệ có thể vượt 1.0 và gây hiểu sai metric. Nay tỉ lệ =
   (số CA vi phạm, mỗi ca đếm tối đa 1) / (tổng số ca), luôn thuộc
   [0, 1]. Số lượt trúng thô vẫn được báo riêng ở forbidden_hits.

Chưa chạy ở đây — chạy bằng: python -m pytest tests/golden -v
"""
from __future__ import annotations

import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


# --------------------------------------------------------------------------- #
# Kiểu dữ liệu dùng chung cho golden regression
# --------------------------------------------------------------------------- #

class Decision(str, Enum):
    VERIFIED = "VERIFIED"
    NEED_CONFIRMATION = "NEED_CONFIRMATION"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"
    REJECTED_INSUFFICIENT = "REJECTED_INSUFFICIENT"


_DECISION_GROUPS = {
    Decision.VERIFIED: "accept",
    Decision.NEED_CONFIRMATION: "review",
    Decision.NEEDS_REVIEW: "review",
    Decision.REJECTED: "reject",
    Decision.REJECTED_INSUFFICIENT: "reject",
}

# Các mảnh của địa chỉ mặc định bị cấm — hệ thống không bao giờ được TỰ
# SINH địa chỉ này cho input không đọc được / thiếu thông tin.
FORBIDDEN_DEFAULT_PARTS = ("Đăk Yă", "Mang Yang")


def _as_decision(value) -> Optional[Decision]:
    if isinstance(value, Decision):
        return value
    if value is None:
        return None
    text = str(getattr(value, "value", value)).upper().rsplit(".", 1)[-1]
    try:
        return Decision(text)
    except ValueError:
        return None


@dataclass
class GoldenCase:
    raw_input: str
    expected_decision: Decision = Decision.REJECTED
    expected_province: Optional[str] = None
    case_id: str = ""


GOLDEN_CASES: list[GoldenCase] = [
    GoldenCase("asdf qwer zxcv", Decision.REJECTED, None,
               "golden_01_unreadable_no_default"),
    GoldenCase("xã Cuôr Đăng, Đắk Lắk", Decision.NEEDS_REVIEW, "Đắk Lắk",
               "golden_03_cuor_dang_dak_lak"),
    GoldenCase("Xom", Decision.REJECTED, None, "golden_04_xom"),
    GoldenCase("Sói 799, đường Lam Sơn", Decision.REJECTED, None,
               "golden_05_soi_799_lam_son"),
    GoldenCase("73", Decision.REJECTED, None, "golden_06_number_73"),
    GoldenCase("127", Decision.REJECTED, None, "golden_07_number_127"),
    GoldenCase("bi", Decision.REJECTED, None, "golden_08_bi"),
    GoldenCase("Bị", Decision.REJECTED, None, "golden_09_bi_accented"),
    GoldenCase("Số 12, đường Lê Lợi, Phường Bến Nghé, Quận 1, "
               "Thành phố Hồ Chí Minh",
               Decision.VERIFIED, "Hồ Chí Minh", "golden_11_valid_hcm"),
]


@dataclass
class EvalResult:
    total_cases: int = 0
    correct_top1: int = 0
    correct_reject: int = 0
    forbidden_hits: int = 0        # tổng LƯỢT trúng chuỗi cấm (chỉ để quan sát)
    forbidden_cases: int = 0       # số CA vi phạm (mỗi ca tối đa 1)
    failures: list = field(default_factory=list)

    @property
    def top1_accuracy(self) -> float:
        return self.correct_top1 / self.total_cases if self.total_cases else 0.0

    @property
    def forbidden_default_rate(self) -> float:
        """Tỉ lệ CA sinh địa chỉ mặc định cấm — luôn thuộc [0, 1] vì tử số
        là số ca vi phạm, không phải số lượt trúng chuỗi cấm."""
        if not self.total_cases:
            return 0.0
        return self.forbidden_cases / self.total_cases

    @property
    def province_mismatch_rate(self) -> float:
        mismatches = sum(1 for f in self.failures
                         if f.startswith("province_mismatch"))
        return mismatches / self.total_cases if self.total_cases else 0.0

    @property
    def passed(self) -> bool:
        return not self.failures and self.forbidden_cases == 0


# --------------------------------------------------------------------------- #
# Golden regression
# --------------------------------------------------------------------------- #

def run_golden_regression(
    run_one: Callable[[str], tuple[Decision, str, Optional[str]]],
    cases: list[GoldenCase] = GOLDEN_CASES,
) -> EvalResult:
    """run_one(raw) -> (decision, normalized_output, province_name_or_None).

    Dùng trong CI: kết quả trả về có .passed và .failures mô tả từng ca sai.
    Chưa chạy ở đây — chạy bằng: python -m pytest tests/golden -v
    """
    result = EvalResult(total_cases=len(cases))
    forbidden_case_ids: set[str] = set()

    for index, case in enumerate(cases):
        case_key = case.case_id or f"case_{index}"
        decision_raw, output, province = run_one(case.raw_input)
        decision = _as_decision(decision_raw)
        output_text = str(output or "")

        # --- Địa chỉ mặc định cấm: đếm lượt trúng để quan sát, nhưng mỗi
        # ca chỉ tính MỘT lần vào forbidden_cases (sửa lỗi tỉ lệ > 1.0).
        hits = sum(1 for part in FORBIDDEN_DEFAULT_PARTS if part in output_text)
        if hits:
            result.forbidden_hits += hits
            forbidden_case_ids.add(case_key)
            result.failures.append(
                f"forbidden_default:{case_key}: output chứa địa chỉ mặc "
                f"định cấm ({hits} mảnh khớp)")

        # --- Đúng/sai theo nhóm quyết định.
        expected_group = _DECISION_GROUPS[case.expected_decision]
        actual_group = _DECISION_GROUPS.get(decision) if decision else None
        if actual_group == expected_group:
            if expected_group == "reject":
                result.correct_reject += 1
                result.correct_top1 += 1
            elif expected_group == "accept":
                if (case.expected_province is None
                        or province == case.expected_province):
                    result.correct_top1 += 1
                else:
                    result.failures.append(
                        f"province_mismatch:{case_key}: kỳ vọng tỉnh "
                        f"{case.expected_province!r}, nhận {province!r}")
            else:
                result.correct_top1 += 1
        else:
            result.failures.append(
                f"decision_mismatch:{case_key}: kỳ vọng "
                f"{case.expected_decision.value}, nhận "
                f"{decision.value if decision else decision_raw!r}")

        # --- Lệch tỉnh trên ca có tỉnh kỳ vọng (kể cả nhóm review).
        if (case.expected_province is not None and province is not None
                and province != case.expected_province
                and actual_group != "reject"):
            marker = f"province_mismatch:{case_key}"
            if not any(f.startswith(marker) for f in result.failures):
                result.failures.append(
                    f"{marker}: kỳ vọng tỉnh {case.expected_province!r}, "
                    f"nhận {province!r}")

    result.forbidden_cases = len(forbidden_case_ids)
    return result


# --------------------------------------------------------------------------- #
# Observability runtime (đếm metric trong bộ nhớ, an toàn đa luồng)
# --------------------------------------------------------------------------- #

class Observability:
    """
    Bộ đếm metric tối thiểu cho pipeline: số quyết định theo loại, số lần
    lệch tỉnh, độ trễ tích lũy. region_lock (nếu được truyền) là khóa do
    tầng gọi sở hữu để đồng bộ theo khu vực; module này chỉ giữ tham
    chiếu, không tự tạo khóa thay thế.
    """

    def __init__(self, region_lock: Optional[threading.Lock] = None,
                 **_ignored) -> None:
        # Một lần gán duy nhất — bản cũ có thêm dòng chết tham chiếu tên
        # `regionLock` không tồn tại rồi gán đè ngay sau; đã xóa.
        self.region_lock = region_lock
        self._lock = threading.Lock()
        self._decision_counts: Counter = Counter()
        self._province_mismatches = 0
        self._total = 0
        self._latency_seconds_total = 0.0

    def record(self, decision, province_mismatch: bool = False,
               latency_seconds: Optional[float] = None) -> None:
        parsed = _as_decision(decision)
        key = parsed.value if parsed else str(decision)
        with self._lock:
            self._total += 1
            self._decision_counts[key] += 1
            if province_mismatch:
                self._province_mismatches += 1
            if latency_seconds is not None:
                self._latency_seconds_total += float(latency_seconds)

    def record_latency(self, latency_seconds: float) -> None:
        with self._lock:
            self._latency_seconds_total += float(latency_seconds)

    def snapshot(self) -> dict:
        with self._lock:
            total = self._total
            return {
                "total": total,
                "decisions": dict(self._decision_counts),
                "province_mismatches": self._province_mismatches,
                "province_mismatch_rate": (
                    self._province_mismatches / total if total else 0.0),
                "avg_latency_seconds": (
                    self._latency_seconds_total / total if total else 0.0),
                "recorded_at": time.time(),
            }

    def run_regression(self, run_one, cases: list[GoldenCase] = GOLDEN_CASES
                       ) -> EvalResult:
        return run_golden_regression(run_one, cases)
