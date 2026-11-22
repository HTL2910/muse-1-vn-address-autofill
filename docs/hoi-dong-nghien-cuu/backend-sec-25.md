# Sửa lỗi `fix/fix_review_queue_state_machine`: Review queue sang store DB, state machine và re-validate địa chỉ khách sửa

_backend-sec · Chuyên gia Backend & Bảo mật · lần nộp 1_

## Việc được giao

Review queue sang store DB, state machine và re-validate địa chỉ khách sửa

## Bài làm

Chưa chạy (theo quy ước KHÔNG_CHẠY_LỆNH). CI chạy: `python -m pytest tests/test_review_queue_state_machine.py tests/test_pipeline_smoke.py tests/golden -q`.

Cơ sở sửa (từ bản `review_queue.py` vừa đọc trên main, cũng là bản approach_15 gốc): store là `dict` trong process (restart mất đơn Đỏ, nhiều worker giẫm nhau), `_decide` không kiểm tra trạng thái hiện tại (duyệt hai lần vẫn tính metrics hai lần), và `customer_confirmed` gán thẳng địa chỉ khách gửi lên rồi đặt `APPROVED` — vòng qua mọi cổng kiểm chứng, link sửa 1 chạm bị chuyển tiếp là thành đường bypass. Bản dưới đây giữ nguyên toàn bộ API/threshold/SLA cũ, thay 4 điểm thuộc mã lỗi này: store DB (SQLite) qua port với `UNIQUE(order_id)` + optimistic version, state machine tường minh, re-validate bắt buộc khi khách sửa, và token link ký + hết hạn + gắn theo đơn.

(đã nộp file `src/ops/review_queue.py`)

(đã nộp file `tests/test_review_queue_state_machine.py`)

## File đã nộp

- `src/ops/review_queue.py`
- `tests/test_review_queue_state_machine.py`
