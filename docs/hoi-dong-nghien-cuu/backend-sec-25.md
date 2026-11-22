# Sửa lỗi `fix/fix_review_queue_state_machine`: Review queue sang store DB, state machine và re-validate địa chỉ khách sửa

_backend-sec · Chuyên gia Backend & Bảo mật · lần nộp 2_

## Việc được giao

Review queue sang store DB, state machine và re-validate địa chỉ khách sửa

## Bài làm

Chưa chạy (theo quy ước KHÔNG_CHẠY_LỆNH). CI chạy: `python -m pytest tests/test_review_queue_state_machine.py tests/test_pipeline_smoke.py tests/golden -q`.

Đọc kỹ output vòng trước, 5 lỗi thuộc test của chính nhánh này đều truy được về 4 khiếm khuyết thật trong bản tôi nộp (không phải do test sai), và tôi sửa ở tầng implementation:

1. **Token bị ép cả khi không ai phát hành được nó** — `enqueue` RED luôn ghi `fix_token_hash` kể cả khi không có messenger/secret, nên `customer_confirmed` không kèm token (luồng nội bộ/CS và 2 test re-validate) bị `InvalidTokenError` oan. Sửa: token là **bắt buộc chỉ khi queue chạy chế độ production có `token_secret`**; hễ caller *có* gửi token thì luôn verify hash + hạn dùng. Test token (có secret) giữ nguyên kỳ vọng.
2. **CS approve lách qua state machine** — `WAITING_CUSTOMER -> APPROVED` nằm trong bảng chuyển đổi chung (vốn dành cho đường khách xác nhận) nên `approve()` duyệt thẳng được đơn đang chờ khách, đúng ca test bắt được. Sửa: mỗi actor có tập trạng thái nguồn riêng — CS approve/reject chỉ từ `PENDING_CS | ESCALATED`; khách confirm chỉ từ `WAITING_CUSTOMER | ESCALATED | PENDING_CS`.
3. **`created_at` lệch đồng hồ** — `ReviewItem.created_at` lấy `time.time` thật lúc khởi tạo, trong khi SLA so với `clock` của queue, nên test dùng fake clock không bao giờ thấy breach. Sửa: `enqueue` đóng dấu `created_at` bằng clock của queue (thời điểm tiếp nhận mới là mốc SLA đúng nghĩa).
4. **Lỗi ERROR ở test persistence** — hai kết nối SQLite cùng mở file trên Windows khiến dọn `tmp_path` bị khóa file. Sửa: thêm `ReviewQueue.close()` và test đóng cả hai queue trước khi kết thúc.

6 ca golden còn lại vẫn là cụm guard/gate tích hợp đã báo cáo ở nhánh validator (không đi qua review queue), không thuộc phạm vi file của nhánh này.

(đã nộp file `src/ops/review_queue.py`)

(đã nộp file `tests/test_review_queue_state_machine.py`)

## File đã nộp

- `src/ops/review_queue.py`
- `tests/test_review_queue_state_machine.py`
