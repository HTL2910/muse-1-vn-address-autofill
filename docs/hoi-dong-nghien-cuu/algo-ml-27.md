# Sửa lỗi `fix/fix_input_guard_substring_false_level`: Input guard nhận diện cấp hành chính theo ranh giới từ, không substring

_algo-ml · Chuyên gia Thuật toán & AI · lần nộp 3_

## Việc được giao

Input guard nhận diện cấp hành chính theo ranh giới từ, không substring

## Bài làm

Nguyên nhân fail mới: `AddressService.resolve` gọi `guard.passed` và `guard.reason_code` (dòng 76, `if not guard.passed: return result.reject(..., guard.reason_code)`), nhưng `GuardResult` đã nộp chỉ có `decision` dạng enum và `reason_code` dạng `str`, không có thuộc tính `passed`, cũng không dùng `ReasonCode` của `schemas.py` — nên `PipelineResult.reject` nhận `str` thay vì enum. Fix: thêm `passed` (và alias `is_pass`) vào `GuardResult`, đổi mọi `reason_code` sang `ReasonCode` trong `schemas.py`, giữ nguyên phương án ranh giới từ và interface `check(raw, parsed=None)`. Lưu ý: chưa chạy build / test / lint — kết quả thật do CI báo lại. Cách chạy: `python -m py_compile src/address/domain/input_guard.py src/address/domain/ranker.py && python -m pytest tests/test_input_guard_word_boundary.py tests/test_ranker_dedupe.py tests/golden/test_golden_regression.py tests/test_pipeline_smoke.py -v`.

(đã nộp file `src/address/domain/input_guard.py`)

(đã nộp file `src/address/domain/ranker.py`)

(đã nộp file `tests/test_input_guard_word_boundary.py`)

(đã nộp file `tests/test_ranker_dedupe.py`)

## File đã nộp

- `src/address/domain/input_guard.py`
- `src/address/domain/ranker.py`
- `tests/test_input_guard_word_boundary.py`
- `tests/test_ranker_dedupe.py`
