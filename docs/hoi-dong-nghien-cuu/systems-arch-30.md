# Sửa lỗi `fix/fix_schemas_forbidden_default_crash`: Schema cấm địa chỉ mặc định bằng status thay vì raise exception

_systems-arch · Chuyên gia Kiến trúc Hệ thống · lần nộp 3_

## Việc được giao

Schema cấm địa chỉ mặc định bằng status thay vì raise exception

## Bài làm

Nhánh `fix/fix_schemas_forbidden_default_crash` — tiến bộ ở lần chạy trước (anchor đã neo được Đắk Lắk) nhưng còn 3 lỗi rõ ràng từ log: (1) `ward_code` Cuôr Đăng ra `65211` trong khi test chờ `66011` — seed của tôi bịa mã huyện Cư M'gar là 652, mã thật là 660, và `_combined_index` ưu tiên seed của tôi hơn index test truyền vào, nên mã thật bị ghi đè; (2) `"Xã Đăk Yă, Mang Yang, Gia Lai"` ra `VERIFIED` thay vì `REJECTED` — parser gán `street="Gia Lai"` từ đoạn tỉnh/huyện đã gán, khiến `has_house_or_street=True` và gate bare-only không kích hoạt; (3) toàn bộ `test_aho_corasick_missed_mention.py` và `test_input_guard_parsed_evidence.py` fail — Aho của tôi chỉ nhận tuple `(pattern, code)`, trong khi test dùng cả list chuỗi/dict (nên `test_classic_ushers` lỗi `ValueError` khi unpack), `AnchorResult` thiếu thuộc tính `province_code`, và guard còn fallback đếm theo parser khi không có bằng chứng raw. Fix cả 3, giữ nguyên fix schema không raise. Chưa chạy build/test/lint; kết quả thật do CI báo lại. Cách chạy: `python -m pytest tests/test_pipeline_smoke.py tests/test_aho_corasick_missed_mention.py tests/test_input_guard_parsed_evidence.py tests/test_input_guard_word_boundary.py tests/golden/test_golden_regression.py -v`.

(đã nộp file `src/address/domain/province_anchor.py`)

(đã nộp file `src/address/domain/structured_parser.py`)

(đã nộp file `src/address/domain/input_guard.py`)

(đã nộp file `src/address/services/address_service.py`)

(đã nộp file `src/address/domain/schemas.py`)

## File đã nộp

- `src/address/domain/province_anchor.py`
- `src/address/domain/structured_parser.py`
- `src/address/domain/input_guard.py`
- `src/address/services/address_service.py`
- `src/address/domain/schemas.py`
