# Nhánh `expert/backend-sec-m1` (5 hướng giải quyết)

_backend-sec · Chuyên gia Backend & Bảo mật · lần nộp 1_

## Việc được giao

- [approach_03] Bộ chuẩn hóa tiếng Việt và từ điển alias địa danh (`src/address/domain/vn_normalizer.py`): Module tiền xử lý trước mọi matching: chuẩn hóa Unicode NFC, lowercase, bỏ dấu và giữ dấu song song, chuẩn hóa viết tắt P., Q., H., Tp., Đ., Ng., X., sửa lỗi gõ Telex và VNI, chuẩn hóa Đăk, Dak, Đắk, Cuôr, Cuor, Ea, Buôn, Krông. Xây từ điển alias version hóa cho tên dân tộc, tên cũ và mới sau sáp nhập, xã trùng tên khác tỉnh, cập nhật từ ca hoàn đơn thực tế. Giữ lại token gốc chưa khớp để tính rủi ro, tuyệt đối không vứt token lạ rồi đoán bừa.
- [approach_07] Multi-provider geocoding router với circuit breaker (`src/address/infrastructure/geocoding_router.py`): Không phụ thuộc một Goong: router gọi song song hoặc tuần tự Goong, Pancake, Viettel Maps, Vietmap, Nominatim/OSM và provider dự phòng, mỗi provider có timeout 3-5 giây, circuit breaker, rate limit và quota riêng. Provider trả kết quả trái tỉnh đã neo bị veto ngay. Nếu tỉ lệ mismatch tỉnh hoặc tỉ lệ hoàn theo provider vượt ngưỡng trong 15 phút đến 1 giờ thì tự ngắt provider đó, chuyển sang provider còn lại hoặc chế độ manual. Khi tất cả provider lỗi thì trả về trạng thái chưa xác minh thay vì đoán bừa.
- [approach_11] Validator nhất quán phân cấp, kiểm chứng chéo hãng vận chuyển và reverse-geocode (`src/address/domain/validator.py`): Validator chạy trước khi tạo đơn giao: kiểm tra phường/xã thuộc huyện, huyện thuộc tỉnh, tên đường có tồn tại trong phường/xã đó không nếu có dữ liệu đường. Đối chiếu kết quả Goong với API mã hành chính của SPX, GHN, GHTK, nếu mâu thuẫn tỉnh hoặc xã thì chặn đơn và gắn cờ kèm lý do theo từng trường. Bổ sung reverse-geocoding khi có GPS, IP hoặc vị trí app: tọa độ phải nằm trong tỉnh/huyện đã suy ra, nếu lệch thì hạ điểm mạnh. Mọi ca bị chặn trả về lý do cụ thể để CS sửa đúng chỗ.
- [approach_15] Hàng đợi rà soát thủ công phân tầng rủi ro (`src/ops/review_queue.py`): Không rà soát tất cả mà phân tầng rủi ro trong dashboard vận hành: Xanh là confidence cao và nhất quán 3 cấp thì đi thẳng, Vàng là thiếu số nhà, trùng tên xã, đổi tỉnh đề xuất thì nhân viên duyệt nhanh bằng phím tắt với giao diện so sánh một màn hình, Đỏ là input quá ngắn, mâu thuẫn tỉnh, từng hoàn đơn thì giữ lại và tự động nhắn Zalo, SMS hoặc app cho khách bổ sung kèm link sửa một chạm. Ngưỡng gợi ý: từ 0.65-0.90 vào queue CS xác nhận 1-click hoặc gửi cho khách chọn lại xã/phường đúng tỉnh. Thiết kế SLA, phân ca và đo thời gian duyệt trung bình để hàng đợi không thành nút thắt doanh thu.
- [approach_19] Hợp đồng tích hợp Pancake, Goong và hãng vận chuyển (`src/integrations/pancake_goong_shipping_adapter.py`): Định nghĩa lại integration contract: Pancake chỉ là nguồn lead và đơn nháp, không được tự chốt địa chỉ; Goong chỉ là một provider gợi ý trong danh sách có thể thay thế và bổ sung gồm Vietmap và Google Maps. Trước khi tạo vận đơn, gọi bước validate cuối với mã hành chính của hãng vận chuyển, nếu hãng báo xã/phường không thuộc tuyến hoặc không khớp tỉnh thì chặn và trả về UI. Lưu idempotency_key, raw_input, provider_response và địa chỉ khách xác nhận cuối cùng để truy vết khi tranh chấp hoàn đơn, đồng thời có circuit-breaker hạ trọng số hoặc tắt autofill của provider có tỉ lệ sai tỉnh vượt ngưỡng.

## Bài làm

Trạng thái kiểm thử: chưa chạy (theo quy ước KHÔNG_CHẠY_LỆNH). Chạy kiểm tra bằng CI của repo hoặc thủ công: `python -m py_compile src/address/domain/vn_normalizer.py src/address/domain/validator.py src/address/infrastructure/geocoding_router.py src/ops/review_queue.py src/integrations/pancake_goong_shipping_adapter.py && python -m pytest tests/ -q`. Toàn bộ module dùng stdlib, logic domain thuần, fail-closed: không bao giờ tự điền địa chỉ mặc định, không bao giờ đoán khi thiếu bằng chứng.

(đã nộp file `src/address/domain/vn_normalizer.py`)

(đã nộp file `src/address/infrastructure/geocoding_router.py`)

(đã nộp file `src/address/domain/validator.py`)

(đã nộp file `src/ops/review_queue.py`)

(đã nộp file `src/integrations/pancake_goong_shipping_adapter.py`)

## File đã nộp

- `src/address/domain/vn_normalizer.py`
- `src/address/infrastructure/geocoding_router.py`
- `src/address/domain/validator.py`
- `src/ops/review_queue.py`
- `src/integrations/pancake_goong_shipping_adapter.py`
