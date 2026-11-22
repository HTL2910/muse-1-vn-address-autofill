# Sửa lỗi `fix/fix_encoding_utf8`: Sửa mojibake UTF-8 và thêm CI kiểm tra encoding

_fullstack-ux · Chuyên gia Ứng dụng & Tích hợp · lần nộp 3_

## Việc được giao

Sửa mojibake UTF-8 và thêm CI kiểm tra encoding

## Bài làm

Chưa chạy lại test trong phiên này (KHÔNG_CHẠY_LỆNH) — kết quả thật do CI báo lại.

Đối chiếu log lần này với lần trước: **toàn bộ test encoding đã PASS** — danh sách fail không còn test nào của `tests/test_encoding_utf8.py`, và số test pass tăng 130 → 134 (đúng các test encoding mới: docstring đúng dấu, quét repo sạch, chuỗi mẫu, 3 test quét trong bộ nhớ, test thiếu chuỗi mẫu). Các fail còn lại nằm trọn ở 3 cụm ngoài phạm vi nhánh `fix/fix_encoding_utf8`:

- `test_input_guard_*`: GuardResult trên main còn thiếu thuộc tính `detected_levels` (log: `AttributeError ... 'detected_levels'` tại `test_input_guard_word_boundary.py:146`) và guard mặc định chưa có danh sách tỉnh tích hợp nên "xã Cuôr Đăng, Đắk Lắk" bị tính 1 cấp → `MISSING_ADMIN_LEVELS` ở tầng PARSE, kéo theo `test_pipeline_smoke::test_cuor_dang_dak_lak_never_hai_duong` và golden_03/10/11. Đây là phạm vi của nhánh fix input_guard.
- `test_aho_corasick_missed_mention`: phạm vi nhánh fix province_anchor.
- `ERROR test_review_queue_state_machine`: phạm vi nhánh fix review_queue.

Nộp lại nguyên trạng bộ file encoding đã đạt để nhánh này merge độc lập; ba cụm trên cần merge từ đúng nhánh sở hữu, sửa chéo tại đây sẽ xung đột với các nhánh đó.

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
