# Sửa lỗi `fix/fix_observability_dead_code`: Xóa code chết regionLock và sửa tỉ lệ forbidden_default

_fullstack-ux · Chuyên gia Ứng dụng & Tích hợp · lần nộp 1_

## Việc được giao

Xóa code chết regionLock và sửa tỉ lệ forbidden_default

## Bài làm

Chưa chạy test trong phiên này (KHÔNG_CHẠY_LỆNH) — kết quả thật do CI báo lại. Cách chạy: `python -m pytest tests/test_observability_dead_code.py tests/golden -v` và lint: `python -m pyflakes src/address/infrastructure/observability.py` (nếu repo có pyflakes/ruff).

Hai lỗi đã thống nhất, sửa đúng hai chỗ và giữ nguyên contract công khai của module (`Observability`, `run_golden_regression(run_one, cases=GOLDEN_CASES) -> EvalResult`, `GoldenCase`, `GOLDEN_CASES`):

1. **Code chết `regionLock`**: dòng `self.region_lock = regionLock if False else region_lock` tham chiếu tên không tồn tại rồi bị gán đè ngay dòng sau — xóa hẳn, chỉ giữ một lần gán `self.region_lock = region_lock`.
2. **`forbidden_default_rate` có thể vượt 1.0**: bản cũ tính `forbidden_hits / len(cases)` trong đó một ca trúng nhiều chuỗi cấm bị đếm nhiều lần. Bản mới đếm **số ca vi phạm** (mỗi ca tối đa 1, qua tập `forbidden_case_ids`) rồi mới chia cho tổng số ca; số lượt trúng vẫn được giữ riêng ở trường `forbidden_hits` để quan sát, không dùng làm tử số của tỉ lệ.

(đã nộp file `src/address/infrastructure/observability.py`)

(đã nộp file `tests/test_observability_dead_code.py`)

## File đã nộp

- `src/address/infrastructure/observability.py`
- `tests/test_observability_dead_code.py`
