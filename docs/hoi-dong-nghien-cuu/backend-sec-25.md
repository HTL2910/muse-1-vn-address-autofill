# Sửa lỗi `fix/fix_review_queue_state_machine`: Review queue sang store DB, state machine và re-validate địa chỉ khách sửa

_backend-sec · Chuyên gia Backend & Bảo mật · lần nộp 3_

## Việc được giao

Review queue sang store DB, state machine và re-validate địa chỉ khách sửa

## Bài làm

Chưa chạy (theo quy ước KHÔNG_CHẠY_LỆNH). CI chạy: `python -m pytest tests/test_review_queue_state_machine.py tests/test_pipeline_smoke.py tests/golden -q`.

Vòng này chỉ còn 2 lỗi thuộc nhánh này, cả hai đều rõ nguyên nhân từ output:

1. **`last_validation_errors` chứa repr của dict thay vì mã lỗi** — `["{'code': 'WARD_MISMATCH'}"]`: hàm trích lỗi của tôi dùng `getattr(e, "code", e)`; với error dạng dict, `getattr` trả về chính dict rồi `str()` thành repr. Sửa bằng helper `_error_code()`: dict -> `e["code"]`, object -> `e.code`, còn lại -> `str(e)`. Đây là lỗi thật ở tầng API (CS/UI đọc `last_validation_errors` sẽ nhận chuỗi vô nghĩa), không chỉ là chuyện test.
2. **ERROR ở test persistence** — hai kết nối SQLite mở đồng thời trên cùng file (Windows khóa file khi pytest dọn `tmp_path`). Đổi test sang mô hình đúng nghĩa "restart": đóng hẳn queue 1 rồi mới mở queue 2 trên cùng file DB — vừa hết khóa file, vừa chứng minh mạnh hơn rằng dữ liệu sống qua restart thật, không chỉ qua hai connection song song. Đồng thời `SQLiteReviewStore.close()` được làm idempotent để đóng lặp không lỗi.

6 ca golden còn lại vẫn là cụm guard/gate tích hợp (không đi qua review queue), giữ nguyên báo cáo ở các vòng trước.

(đã nộp file `src/ops/review_queue.py`)

(đã nộp file `tests/test_review_queue_state_machine.py`)

## File đã nộp

- `src/ops/review_queue.py`
- `tests/test_review_queue_state_machine.py`
