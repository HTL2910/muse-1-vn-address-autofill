# Nhánh `expert/algo-ml-m1` (5 hướng giải quyết)

_algo-ml · Chuyên gia Thuật toán & AI · lần nộp 1_

## Việc được giao

- [approach_02] Canonical Admin DB có version và ETL sáp nhập 2025 (`src/address/infrastructure/admin_boundary_store.py`): Xây master data tỉnh/huyện/xã chuẩn Việt Nam sau sáp nhập 2025 từ Tổng cục Thống kê, GHN, SPX và danh mục bưu chính, lưu PostgreSQL và PostGIS kèm mã hành chính, mã GHN/GHTK/SPX/Goong, tên có dấu, không dấu, tên cũ, tên mới, alias địa phương và centroid. Có version, ngày hiệu lực, nguồn và rollback. Dùng khóa ngoại và CHECK constraint: ward thuộc district, district thuộc province, cấm tổ hợp chéo. Cache Redis theo tỉnh/huyện/xã dưới 10ms, đồng bộ định kỳ qua ETL, mọi tra cứu đi qua DB này thay vì tin hoàn toàn geocoder.
- [approach_06] Khớp mờ đa tín hiệu trong phạm vi đã khóa (`src/address/domain/fuzzy_matcher.py`): Fuzzy matching chỉ chạy trong phạm vi tỉnh và huyện đã neo, không tìm toàn quốc. Kết hợp Levenshtein, Jaro-Winkler, Trigram pg_trgm, token-sort Jaccard, BM25 trên inverted index và embedding tiếng Việt PhoBERT hoặc bi-encoder cho sai chính tả và thiếu dấu. Dùng BK-tree để tìm ứng viên trong ngưỡng edit-distance mà không quét toàn bộ hơn 10000 phường/xã. Cộng điểm theo độ dài token khớp và độ hiếm token IDF để tên hiếm như Cuôr Đăng thắng tên phổ biến như Phả Lại. Token tường minh của khách là nguồn sự thật tối cao, provider không được ghi đè.
- [approach_10] Minimum input guard chặn input quá ngắn và mơ hồ (`src/address/domain/input_guard.py`): Hard gate ở tầng service và API trước khi gọi autofill: chặn input dưới 10 ký tự chữ, dưới 3 ký tự có nghĩa, chỉ gồm số như 73, 127, chỉ 1-2 âm tiết mơ hồ như bi, Bị, Xom, hoặc chỉ có số nhà và đường mà không có xã/huyện/tỉnh như Sói 799, đường Lam Sơn. Yêu cầu tối thiểu ít nhất 2 cấp hành chính hoặc tên xã/phường hay huyện rõ ràng. Các ca này trả về NEED_CLARIFICATION, không gọi geocoder, không cho tạo vận đơn, UI yêu cầu bổ sung tỉnh/huyện/xã hoặc chọn từ danh sách chuẩn.
- [approach_14] Diff và confirm UI bắt buộc khi có mâu thuẫn (`src/ui/components/AddressDiffConfirm.tsx`): Khi địa chỉ khách gõ và địa chỉ hệ thống chuẩn hóa khác nhau ở bất kỳ cấp nào, hiển thị modal hoặc inline diff song song: bên trái Bạn đã nhập, bên phải Hệ thống đề xuất, tô đỏ cấp bị đổi đặc biệt là tỉnh. Khách phải chủ động tick chọn một trong hai hoặc sửa lại, không có nút mặc định bỏ qua và không tự đóng. Thêm bản đồ pin preview, nút Địa chỉ này chưa đúng và tóm tắt địa chỉ chuẩn hóa trước nút Đặt hàng, lưu consent timestamp làm bằng chứng. Với đơn qua Pancake và chat, gửi link hoặc tin nhắn xác nhận rút gọn cho khách bấm Xác nhận hoặc Sửa trước khi đẩy sang vận chuyển, kèm timeout và trạng thái chờ trên dashboard.
- [approach_18] Quan trắc chất lượng, golden regression và cảnh báo drift (`src/address/infrastructure/observability.py`): Gắn metrics cho tỉ lệ VERIFIED, NEEDS_REVIEW, REJECTED, tỉ lệ hoàn đơn theo provider và theo tỉnh, độ trễ geocoding, tỉ lệ circuit breaker mở, log đầy đủ input, output và điểm tin cậy để truy vết từng đơn lỗi. Xây golden dataset hồi quy bắt buộc từ ca thật: Cuôr Đăng, Đắk Lắk không được ra Hải Dương, Xom, 73, 127, bi phải bị REJECTED, không ca nào được ra Đăk Yă mặc định. Đo precision@1, tỉ lệ từ chối đúng, tỉ lệ giao thành công lần đầu và tỉ lệ hoàn theo tỉnh, mọi thay đổi ngưỡng hoặc thuật toán phải qua eval này trước khi deploy. Giám sát drift: khi tỉ lệ từ chối hoặc hoàn ở một tỉnh tăng đột biến thì cảnh báo và khóa tạm autofill cho khu vực đó.

## Bài làm

Đã nộp đủ 5 hướng trên nhánh `expert/algo-ml-m1`. Lưu ý: chưa chạy build / test / lint — kết quả thật do CI của repo báo lại. Cách chạy kiểm tra: `python -m pytest tests/golden -v` cho golden regression, `python -m py_compile src/address/infrastructure/admin_boundary_store.py src/address/domain/fuzzy_matcher.py src/address/domain/input_guard.py src/address/infrastructure/observability.py` và `npx tsc --noEmit` cho `AddressDiffConfirm.tsx`.

(đã nộp file `src/address/infrastructure/admin_boundary_store.py`)

(đã nộp file `src/address/domain/fuzzy_matcher.py`)

(đã nộp file `src/address/domain/input_guard.py`)

(đã nộp file `src/ui/components/AddressDiffConfirm.tsx`)

(đã nộp file `src/address/infrastructure/observability.py`)

## File đã nộp

- `src/address/infrastructure/admin_boundary_store.py`
- `src/address/domain/fuzzy_matcher.py`
- `src/address/domain/input_guard.py`
- `src/ui/components/AddressDiffConfirm.tsx`
- `src/address/infrastructure/observability.py`
