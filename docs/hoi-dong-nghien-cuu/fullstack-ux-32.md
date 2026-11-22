# Sửa lỗi `fix/fix_encoding_utf8`: Sửa mojibake UTF-8 và thêm CI kiểm tra encoding

_fullstack-ux · Chuyên gia Ứng dụng & Tích hợp · lần nộp 2_

## Việc được giao

Sửa mojibake UTF-8 và thêm CI kiểm tra encoding

## Bài làm

Chưa chạy lại test trong phiên này (KHÔNG_CHẠY_LỆNH) — kết quả thật do CI báo lại.

Phân tích log: phần encoding cốt lõi đã đạt (docstring đúng, quét repo sạch, chuỗi mẫu nguyên vẹn). Chỉ còn 3 test của chính tôi bị `ERROR ... Permi...` — cả 3 đều là test dùng `tmp_path` để tạo cây thư mục tạm cho bộ quét (môi trường chạy test trên Windows, thư mục tạm gây PermissionError ở setup/teardown, cùng kiểu lỗi với test persistence của review_queue). Sửa tận gốc: tách lõi quét thành hàm thuần trên dữ liệu trong bộ nhớ `find_violations_in_bytes({đường_dẫn: bytes})`, `find_violations(root)` chỉ còn là lớp đọc đĩa (và lỗi đọc file được ghi thành vi phạm thay vì ném exception). 3 test đó chuyển sang API trong bộ nhớ — không chạm đĩa nữa. Các file còn lại giữ nguyên bản đã đạt.

Các fail pipeline/golden/input_guard/aho trong log thuộc các nhánh fix khác chưa merge (guard ở tầng PARSE trả `MISSING_ADMIN_LEVELS` cho "xã Cuôr Đăng, Đắk Lắk" là lỗi của input_guard/province_anchor trên main hiện tại), không phát sinh từ thay đổi encoding này.

(đã nộp file `src/__init__.py`)

(đã nộp file `scripts/encoding_check.py`)

(đã nộp file `.editorconfig`)

(đã nộp file `.github/workflows/address-pipeline.yml`)

(đã nộp file `tests/test_encoding_utf8.py`)

## File đã nộp

- `src/__init__.py`
- `scripts/encoding_check.py`
- `.editorconfig`
- `.github/workflows/address-pipeline.yml`
- `tests/test_encoding_utf8.py`
