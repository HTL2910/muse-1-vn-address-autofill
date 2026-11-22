# Sửa lỗi `fix/fix_schemas_forbidden_default_crash`: Schema cấm địa chỉ mặc định bằng status thay vì raise exception

_systems-arch · Chuyên gia Kiến trúc Hệ thống · lần nộp 2_

## Việc được giao

Schema cấm địa chỉ mặc định bằng status thay vì raise exception

## Bài làm

Nhánh `fix/fix_schemas_forbidden_default_crash` — lần chạy trước 23 failed / 1 error, không phải do schema nữa mà do pipeline không neo được tỉnh: `test_pipeline_smoke` trả về `REJECTED / MISSING_PROVINCE` ở stage `ANCHOR` cho cả `"xã Cuôr Đăng, Đắk Lắk"` lẫn địa chỉ đầy đủ `"127 Nguyễn Trãi, xã Cuôr Đăng, huyện Cư M'gar, tỉnh Đắk Lắk"`, và toàn bộ `test_input_guard_word_boundary.py` fail. Nguyên nhân đã chốt từ các lỗi này: (1) service dùng index được truyền vào dù index đó rỗng (object rỗng vẫn truthy nên không fallback), anchor không bao giờ tìm được tỉnh; (2) anchor trả `ambiguous` khi có hơn 1 tỉnh được nhắc (tên tỉnh nằm trong tên đường), thay vì lấy tỉnh ở đoạn cuối như quy ước địa chỉ Việt Nam; (3) guard đếm `admin_level_count` của parser một cách mù, kể cả khi tên tỉnh/xã chỉ nằm trong đoạn đường (substring), nên các test word-boundary fail. Fix cả 3, giữ nguyên fix schema ở lần trước (không raise, cấm default bằng status). Chưa chạy build/test/lint; kết quả thật do CI báo lại. Cách chạy: `python -m pytest tests/test_pipeline_smoke.py tests/test_input_guard_word_boundary.py tests/test_schemas_forbidden_default.py tests/golden/test_golden_regression.py -v`.

(đã nộp file `src/address/domain/schemas.py`)

(đã nộp file `src/address/domain/province_anchor.py`)

(đã nộp file `src/address/domain/structured_parser.py`)

(đã nộp file `src/address/domain/input_guard.py`)

(đã nộp file `src/address/services/address_service.py`)

(đã nộp file `src/address/domain/confidence_gate.py`)

(đã nộp file `tests/test_schemas_forbidden_default.py`)

## File đã nộp

- `src/address/domain/schemas.py`
- `src/address/domain/province_anchor.py`
- `src/address/domain/structured_parser.py`
- `src/address/domain/input_guard.py`
- `src/address/services/address_service.py`
- `src/address/domain/confidence_gate.py`
- `tests/test_schemas_forbidden_default.py`
