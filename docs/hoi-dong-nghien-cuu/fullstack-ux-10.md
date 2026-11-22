# Nhánh `expert/fullstack-ux-m1` (5 hướng giải quyết)

_fullstack-ux · Chuyên gia Ứng dụng & Tích hợp · lần nộp 1_

## Việc được giao

- [approach_04] Parser địa chỉ có cấu trúc Rule-based kết hợp NER nhẹ (`src/address/domain/structured_parser.py`): Tách địa chỉ thô thành trường có cấu trúc: số nhà, tên đường, thôn/xóm, phường/xã, quận/huyện, tỉnh, ghi chú. Tầng 1 dùng regex có thứ tự và từ điển ưu tiên từ chi tiết đến tổng quát cho mẫu phổ biến Việt Nam. Tầng 2 dùng CRF hoặc transformer nhẹ hoặc LLM nhỏ chỉ để trích xuất, xử lý viết tắt, thiếu dấu, sai chính tả. Parser trả về structured fields kèm confidence từng field, không tự suy ra tỉnh khi trường tỉnh trống, nếu các trường mâu thuẫn thì đánh dấu xung đột thay vì chọn một trường.
- [approach_08] Ensemble scoring và xếp hạng đa ứng viên top-k (`src/address/domain/ranker.py`): Từ mỗi input sinh 10-20 ứng viên từ DB nội bộ, Goong, GHN và SPX rồi xếp hạng lại bằng Learning-to-Rank LambdaRank, GBDT hoặc cross-encoder. Đặc trưng gồm khớp tỉnh/huyện/xã, tương đồng chuỗi, dân số và độ phổ biến, lịch sử giao thành công của địa chỉ, khoảng cách GPS nếu có, margin top-1 trừ top-2. Cộng điểm khi nhiều provider đồng thuận, trừ điểm khi lệch nhau, ưu tiên mã xã khớp canonical DB. Chỉ tự điền khi top-1 vượt ngưỡng tuyệt đối và margin đủ lớn, còn lại hiển thị tối đa 3 gợi ý đầy đủ tỉnh/huyện/xã để khách chọn một chạm.
- [approach_12] Address Intelligence Service bất đồng bộ, idempotency và outbox (`src/address/services/address_service.py`): Tách autofill khỏi app và Pancake thành service riêng giao tiếp qua REST, gRPC và Kafka hoặc RabbitMQ, đơn hàng chỉ lưu raw_address, service trả trạng thái PENDING, VERIFIED, NEEDS_REVIEW, REJECTED. Áp dụng Outbox Pattern, idempotency-key bằng hash của order_id cộng phone cộng raw_address với unique constraint để chống tạo trùng vận đơn khi webhook retry. Xử lý chuẩn hóa bất đồng bộ qua queue có giới hạn concurrency, retry exponential backoff cộng jitter tối đa 3 lần rồi vào Dead Letter Queue, auto-scale theo độ dài queue để chịu hàng nghìn đơn mỗi ngày mà không chặn luồng đặt hàng.
- [approach_16] Vòng lặp feedback từ SPX, correction store và active learning (`src/address/infrastructure/feedback_store.py`): Nhận webhook và file đối soát từ SPX, GHN, GHTK về giao thành công, giao thất bại, sai địa chỉ và hoàn đơn, lưu vào bảng address_feedback gắn với raw_address, resolved_address, provider và điểm tin cậy ban đầu. Địa chỉ từng bị hoàn bị hạ điểm và đưa vào blacklist tạm, địa chỉ khách sửa sau hoàn trở thành ground truth. Xây bảng address_corrections lưu cặp raw_input thành địa chỉ đúng đã giao thành công để làm bộ nhớ ưu tiên theo SĐT và khu vực. Dùng active learning ưu tiên gán nhãn ca margin thấp và ca hoàn mới, tự động học alias và lỗi chính tả phổ biến, hàng tuần retrain alias map và trọng số scoring.
- [approach_20] Triển khai an toàn: shadow mode, A-B, canary và feature flag (`src/address/infrastructure/rollout.py`): Không thay hệ thống cũ trong một lần: chạy Address Gateway ở shadow mode 1-2 tuần chỉ ghi log chênh lệch so với Pancake và Goong hiện tại mà chưa chặn đơn, đo chính xác bao nhiêu đơn sẽ bị chặn hoặc sai tỉnh. Sau đó A/B và canary theo tỉ lệ đơn tăng dần 5-10 phần trăm rồi 50 phần trăm rồi 100 phần trăm, đóng gói thành feature flag theo shop và khu vực để tắt nhanh khi có sự cố, có rollback một nút. KPI nghiệm thu: tỉ lệ sai tỉnh bằng 0 đối với đơn đã khai báo tỉnh, tỉ lệ đơn bị ép điền khi thiếu thông tin bằng 0, tỉ lệ giao thành công từ 60-70 phần trăm lên trên 90-92 phần trăm, tỉ lệ hoàn do sai địa chỉ giảm theo tuần.

## Bài làm

Chưa chạy build/test/lint — các file dưới đây là mã nguồn hoàn chỉnh để CI của repo kiểm tra. Cách chạy kiểm tra thủ công: `python -m py_compile src/address/domain/structured_parser.py src/address/domain/ranker.py src/address/services/address_service.py src/address/infrastructure/feedback_store.py src/address/infrastructure/rollout.py` và `pytest tests/golden -q` sau khi bổ sung test harness.

(đã nộp file `src/address/domain/structured_parser.py`)

(đã nộp file `src/address/domain/ranker.py`)

(đã nộp file `src/address/services/address_service.py`)

(đã nộp file `src/address/infrastructure/feedback_store.py`)

(đã nộp file `src/address/infrastructure/rollout.py`)

## File đã nộp

- `src/address/domain/structured_parser.py`
- `src/address/domain/ranker.py`
- `src/address/services/address_service.py`
- `src/address/infrastructure/feedback_store.py`
- `src/address/infrastructure/rollout.py`
