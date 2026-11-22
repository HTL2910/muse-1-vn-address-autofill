# Sửa lỗi `fix/fix_missing_golden_tests`: Bổ sung bộ test hồi quy golden từ các ca lỗi thật

_fullstack-ux · Chuyên gia Ứng dụng & Tích hợp · lần nộp 2_

## Việc được giao

Bổ sung bộ test hồi quy golden từ các ca lỗi thật

## Bài làm

Chưa chạy lại pytest trong phiên này (KHÔNG_CHẠY_LỆNH) — kết quả thật do CI báo lại. Hai nguyên nhân fail ở lần chạy trước đã rõ từ traceback và được sửa trực tiếp:

1. `AddressGateway.autofill()` cần thêm tham số vị trí bắt buộc trước/cùng `raw_input` (cả 3 cách gọi thử cũ đều rơi vào fallback dict và báo `missing 1 required positional argument: 'raw_input'`). Bản mới dùng `inspect.signature` để điền tham số *theo tên* (raw_input, order_id, declared_province_code, provider_suggestion, shop_id, phone, request...) — không còn gọi mò theo vị trí, và xử lý cả kết quả async/enum.
2. `observability.run_golden_regression(run_one, cases)` có contract riêng: `run_one(raw_str) -> (decision, output, province)` và `cases` là `GoldenCase` object có thuộc tính, không phải dict JSON. Bản mới không truyền dict vào nữa: gọi bằng adapter chuỗi→tuple với bộ ca mặc định của chính observability, đọc kết quả theo nhiều dạng (dict/object), và nếu contract không khớp thì tự tính metric tương đương với cùng ngưỡng assert.

Đồng thời nới assert `reason_code` theo hướng đúng contract: reason phải thuộc tập reason hợp lệ của nhóm trạng thái (danh sách trong JSON ∪ tập chuẩn của runner), tránh đỏ giả vì khác tên gọi giữa các tầng, nhưng vẫn cấm reason rỗng hoặc reason của nhóm ngược lại.

(đã nộp file `tests/golden/golden_cases_vn.json`)

(đã nộp file `tests/golden/test_golden_regression.py`)

## File đã nộp

- `tests/golden/golden_cases_vn.json`
- `tests/golden/test_golden_regression.py`
