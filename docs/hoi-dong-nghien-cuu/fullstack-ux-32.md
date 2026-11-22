# Sửa lỗi `fix/fix_encoding_utf8`: Sửa mojibake UTF-8 và thêm CI kiểm tra encoding

_fullstack-ux · Chuyên gia Ứng dụng & Tích hợp · lần nộp 1_

## Việc được giao

Sửa mojibake UTF-8 và thêm CI kiểm tra encoding

## Bài làm

Chưa chạy kiểm tra trong phiên này (KHÔNG_CHẠY_LỆNH) — kết quả thật do CI báo lại. Đã đối chiếu trực tiếp với main: `src/__init__.py` đang chứa đúng chuỗi mojibake `GÃ³i mÃ£ nguá»“n chÃ­nh cá»§a dá»± Ã¡n`, và workflow hiện chưa có bước kiểm tra encoding nào.

Thiết kế fix: (1) ghi lại `src/__init__.py` bằng UTF-8 đúng; (2) thêm `scripts/encoding_check.py` quét toàn repo — mọi file văn bản phải giải mã được UTF-8, file code/dữ liệu không chứa mẫu mojibake (file `.md` chỉ kiểm tra giải mã, vì tài liệu tranh luận được phép *trích dẫn* chuỗi hỏng làm ví dụ), và bắt buộc tồn tại chuỗi mẫu đúng dấu "Đắk Lắk", "Cuôr Đăng" trong dữ liệu thật; script tự loại trừ chính nó và file test khỏi quét mẫu; (3) thêm bước chạy script + test encoding vào workflow, giữ nguyên mọi bước cũ; (4) `.editorconfig` khóa charset; (5) test hồi quy gồm cả test dương — script phải bắt được file mojibake mẫu trong thư mục tạm.

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
