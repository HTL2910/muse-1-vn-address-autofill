# Nhánh `expert/systems-arch-m1` (5 hướng giải quyết)

_systems-arch · Chuyên gia Kiến trúc Hệ thống · lần nộp 1_

## Việc được giao

- [approach_01] Schema địa chỉ chuẩn và pipeline 5 tầng thống nhất (`src/address/domain/schemas.py`): Định nghĩa một Address Contract duy nhất: raw_input, province, district, ward, street, house_number, hamlet, province_code, district_code, ward_code, lat, lng, confidence, source, status gồm PENDING, VERIFIED, NEEDS_REVIEW, REJECTED. Thiết kế pipeline Normalize -> Parse -> Anchor -> Geocode/Validate -> Gate/Commit, mọi module Goong, Pancake, SPX, GHN đều đi qua pipeline này. Mỗi tầng đều có quyền từ chối thay vì đoán, kết quả cuối phải khớp toàn bộ chuỗi thành phần, mâu thuẫn một cấp thì dừng và đánh dấu mơ hồ.
- [approach_05] Khóa neo tỉnh và lan truyền ràng buộc phân cấp (`src/address/domain/province_anchor.py`): Nhận diện tỉnh trước bằng khớp chính xác và mờ có alias, dùng Trie, inverted index và Aho-Corasick để nhận diện đồng thời mọi tên hành chính. Nếu khách ghi rõ tỉnh thì khóa toàn bộ không gian tìm kiếm chỉ trong tỉnh đó, sau đó mới tìm huyện rồi xã con của nó. Mọi ứng viên khác tỉnh bị loại 100 phần trăm dù điểm fuzzy cao. Dùng constraint propagation: nếu phường/xã không thuộc huyện đã xác định thì hạ điểm mạnh hoặc loại bỏ. Triệt tiêu lỗi xã Cuôr Đăng, Đắk Lắk bị nhảy sang Hải Dương và lỗi khách ghi tỉnh này máy điền tỉnh khác.
- [approach_09] Confidence gate fail-closed và cấm fallback mặc định (`src/address/domain/confidence_gate.py`): Xóa hoàn toàn default Xã Đăk Yă, Mang Yang, Gia Lai và mọi địa chỉ mặc định. Mọi địa chỉ có điểm tin cậy 0-1 đã hiệu chuẩn bằng Platt hoặc isotonic trên dữ liệu lịch sử, tổng hợp từ độ dài input, độ phủ thành phần, khớp tỉnh/huyện/xã, đồng thuận provider, margin top-1 và top-2. Quy tắc: từ 0.85 đến 0.90 trở lên và đủ tỉnh+huyện+xã mới auto-approve, 0.60-0.85 chuyển sang xác nhận tay, dưới 0.60 từ chối với trạng thái UNKNOWN và reason_code rõ ràng. API trả về 3 trạng thái VERIFIED, NEED_CONFIRMATION, REJECTED_INSUFFICIENT kèm confidence_score, matched_tokens, unmatched_tokens, source và lý do.
- [approach_13] Structured address picker 3 cấp và autofill có trạng thái (`src/ui/components/StructuredAddressPicker.tsx`): Thay ô địa chỉ tự do duy nhất bằng flow chọn Tỉnh/Thành rồi Quận/Huyện rồi Phường/Xã bằng dropdown searchable dùng master data nội bộ, mỗi bước lọc theo mã hành chính cha thay vì tìm toàn quốc. Ô chi tiết chỉ nhập số nhà, đường, thôn/xóm và validate độ dài. Ô tìm kiếm nhanh Goong chỉ gợi ý sau ít nhất 3 ký tự có nghĩa, debounce 300ms, giới hạn 5 gợi ý kèm đầy đủ tỉnh/huyện/xã, khi khách chọn thì tự bóc tách và highlight từng cấp để đối chiếu. Lưu session và lịch sử địa chỉ đã xác minh của khách để ưu tiên gợi ý. Backend chỉ nhận địa chỉ khi có đủ province_code, district_code, ward_code hợp lệ.
- [approach_17] Bảo mật PII địa chỉ và SĐT, quản trị secret và audit log (`src/security/address_pii_protection.py`): Địa chỉ và SĐT là PII nhạy cảm: mã hóa ở tầng cột bằng AES-GCM cho phone và address_detail, masking trong log và admin panel theo vai trò RBAC, CSKH chỉ thấy đủ để xác nhận, kho chỉ thấy địa chỉ đã chuẩn hóa. Tuyệt đối không log raw API key của Goong, Pancake, SPX, lưu trong Secret Manager hoặc Vault và rotate định kỳ. Ký webhook từ Pancake và SPX bằng HMAC và verify timestamp để chống giả mạo trạng thái hoàn đơn. Rate limiting theo shop, IP và token ở API Gateway để chống lạm dụng autofill làm cạn quota. Audit log bất biến ghi raw_input, provider_response, parsed_result, final_address, người override và timestamp để truy vết tranh chấp hoàn đơn.

## Bài làm

Nhánh `expert/systems-arch-m1` — đã triển khai đủ 5 hướng được giao theo kiến trúc Address Intelligence Service fail-closed. Chưa chạy build/test/lint; kết quả thật do CI của repo báo lại. Cách kiểm tra: `python -m pytest tests/ -k address`, `python -m py_compile src/address/domain/schemas.py src/address/domain/province_anchor.py src/address/domain/confidence_gate.py src/security/address_pii_protection.py`, `npx tsc --noEmit --skipLibCheck src/ui/components/StructuredAddressPicker.tsx`.

(đã nộp file `src/address/domain/schemas.py`)

(đã nộp file `src/address/domain/province_anchor.py`)

(đã nộp file `src/address/domain/confidence_gate.py`)

(đã nộp file `src/ui/components/StructuredAddressPicker.tsx`)

(đã nộp file `src/security/address_pii_protection.py`)

Hướng dẫn chạy (chưa chạy): `python -m py_compile src/address/domain/schemas.py src/address/domain/province_anchor.py src/address/domain/confidence_gate.py src/security/address_pii_protection.py` và `npx tsc --noEmit --skipLibCheck src/ui/components/StructuredAddressPicker.tsx`, sau đó `python -m pytest tests/ -k address` để chạy các ca hồi quy trong `tests/golden/`.

## File đã nộp

- `src/address/domain/schemas.py`
- `src/address/domain/province_anchor.py`
- `src/address/domain/confidence_gate.py`
- `src/ui/components/StructuredAddressPicker.tsx`
- `src/security/address_pii_protection.py`
