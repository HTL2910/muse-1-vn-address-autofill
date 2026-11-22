# Sửa lỗi `fix/fix_schemas_forbidden_default_crash`: Schema cấm địa chỉ mặc định bằng status thay vì raise exception

_systems-arch · Chuyên gia Kiến trúc Hệ thống · lần nộp 1_

## Việc được giao

Schema cấm địa chỉ mặc định bằng status thay vì raise exception

## Bài làm

Nhánh `fix/fix_schemas_forbidden_default_crash` — đã xem bản mới nhất trên main của `schemas.py`: `__post_init__` vẫn `raise ValueError` khi mã tỉnh/huyện/xã sai định dạng hoặc `confidence` ngoài [0,1]. Đó chính là crash cần sửa: một địa chỉ mặc định cấm hoặc một mã do provider trả về bị lỗi sẽ làm sập cả pipeline thay vì trả về một contract fail-closed. Fix: `__post_init__` không raise nữa, mọi lỗi dữ liệu đều chuyển thành status; địa chỉ mặc định cấm ở dạng trần (không số nhà/đường) thành `REJECTED` + `FORBIDDEN_DEFAULT`, mã sai định dạng thành `REJECTED` + `INVALID_CODE` và mã đó bị xóa, `confidence` ngoài khoảng được clamp về [0,1] thay vì raise. Địa chỉ đầy đủ có số nhà/đường mà khách ghi thật (ví dụ Đăk Yă + số nhà) không bị reject ở schema, giữ nguyên quy ước bare-vs-full đã chốt trước đây; phân cấp vẫn qua `parent_code`, không prefix. Các file `confidence_gate.py`, `address_service.py`, `test_pipeline_smoke.py` được báo không có trên main nên không nộp lại ở lần này. Chưa chạy build/test/lint; kết quả thật do CI báo lại. Cách chạy: `python -m pytest tests/test_schemas_forbidden_default.py -v`, `python -m py_compile src/address/domain/schemas.py`.

(đã nộp file `src/address/domain/schemas.py`)

(đã nộp file `tests/test_schemas_forbidden_default.py`)

## File đã nộp

- `src/address/domain/schemas.py`
- `tests/test_schemas_forbidden_default.py`
