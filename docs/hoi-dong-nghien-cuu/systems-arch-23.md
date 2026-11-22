# Sửa lỗi `fix/fix_router_deadline_mismatch_leak`: Sửa router deadline chung, đếm mismatch theo request và rò rỉ executor

_systems-arch · Chuyên gia Kiến trúc Hệ thống · lần nộp 2_

## Việc được giao

Sửa router deadline chung, đếm mismatch theo request và rò rỉ executor

## Bài làm

Nhánh `fix/fix_router_deadline_mismatch_leak` — router đã pass ở lần chạy trước (58 passed), golden vẫn fail 9 ca với cùng mẫu: `province_code=None` cho mọi ca có `declared_province_code`, kể cả ca hợp lệ. Nguyên nhân không phải router mà là lõi pipeline trên nhánh này vẫn thiếu/không chạy được: `XEM_FILE` trả về không có `schemas.py`, `address_service.py`, `structured_parser.py`, `admin_boundary_store.py`, `observability.py`, `golden_cases_vn.json` dù danh sách main liệt kê có — tức mỗi nhánh fix bắt đầu lại từ main và lõi chưa được khôi phục ở nhánh này. Nộp lại 1 bản canonical cho toàn bộ lõi + router + admin store + observability để `run_one` trong golden test có đường chạy thật, thay vì trả về dict rỗng/None. Các fix đã chốt vẫn giữ: phân cấp qua `parent_code` (không prefix, mã thật 66/652 không có tính prefix), `reject()` giữ tỉnh đã neo, cấm default trần chỉ khi không có detail (địa chỉ đầy đủ chứa Đăk Yă + số nhà/đường là khách ghi thật, tỉnh 64), mapping adapter `REJECTED_INSUFFICIENT -> BLOCKED_UNVERIFIED`, router deadline chung + đếm mismatch theo request + half-open single trial + `close(wait=True)`. Chưa chạy build/test/lint; kết quả thật do CI báo lại. Cách chạy: `python -m pytest tests/test_geocoding_router.py tests/test_pipeline_smoke.py tests/golden/test_golden_regression.py -v`.

(đã nộp file `src/address/domain/schemas.py`)

(đã nộp file `src/address/domain/structured_parser.py`)

(đã nộp file `src/address/domain/province_anchor.py`)

(đã nộp file `src/address/domain/input_guard.py`)

(đã nộp file `src/address/domain/fuzzy_matcher.py`)

(đã nộp file `src/address/domain/ranker.py`)

(đã nộp file `src/address/domain/validator.py`)

(đã nộp file `src/address/domain/confidence_gate.py`)

(đã nộp file `src/address/infrastructure/admin_boundary_store.py`)

(đã nộp file `src/address/infrastructure/observability.py`)

(đã nộp file `src/address/services/address_service.py`)

(đã nộp file `src/address/api/address_gateway.py`)

(đã nộp file `src/address/infrastructure/geocoding_router.py`)

(đã nộp file `tests/test_geocoding_router.py`)

## File đã nộp

- `src/address/domain/schemas.py`
- `src/address/domain/structured_parser.py`
- `src/address/domain/province_anchor.py`
- `src/address/domain/input_guard.py`
- `src/address/domain/fuzzy_matcher.py`
- `src/address/domain/ranker.py`
- `src/address/domain/validator.py`
- `src/address/domain/confidence_gate.py`
- `src/address/infrastructure/admin_boundary_store.py`
- `src/address/infrastructure/observability.py`
- `src/address/services/address_service.py`
- `src/address/api/address_gateway.py`
- `src/address/infrastructure/geocoding_router.py`
- `tests/test_geocoding_router.py`
