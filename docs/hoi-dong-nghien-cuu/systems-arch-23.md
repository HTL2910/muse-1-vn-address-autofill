# Sửa lỗi `fix/fix_router_deadline_mismatch_leak`: Sửa router deadline chung, đếm mismatch theo request và rò rỉ executor

_systems-arch · Chuyên gia Kiến trúc Hệ thống · lần nộp 1_

## Việc được giao

Sửa router deadline chung, đếm mismatch theo request và rò rỉ executor

## Bài làm

[[BAT-DAU-9a5973]]
Nhánh `fix/fix_router_deadline_mismatch_leak` — sửa router theo đúng 3 lỗi đã chốt trong debate, đã xem bản mới nhất trên main. Chưa chạy build/test/lint; kết quả thật do CI báo lại. Cách chạy: `python -m pytest tests/test_geocoding_router.py -v`, `python -m py_compile src/address/infrastructure/geocoding_router.py`.

(đã nộp file `src/address/infrastructure/geocoding_router.py`)

(đã nộp file `tests/test_geocoding_router.py`)
[[HET-9a5973]]

## File đã nộp

- `src/address/infrastructure/geocoding_router.py`
- `tests/test_geocoding_router.py`
