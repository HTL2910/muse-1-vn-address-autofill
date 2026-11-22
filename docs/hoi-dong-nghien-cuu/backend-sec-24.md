# Sửa lỗi `fix/fix_validator_carrier_gps`: Validator kiểm chứng đúng hãng, GPS lệch tỉnh và đủ lý do theo trường

_backend-sec · Chuyên gia Backend & Bảo mật · lần nộp 1_

## Việc được giao

Validator kiểm chứng đúng hãng, GPS lệch tỉnh và đủ lý do theo trường

## Bài làm

Chưa chạy (theo quy ước KHÔNG_CHẠY_LỆNH). CI chạy: `python -m pytest tests/test_validator_carrier_gps.py tests/test_adapter_allowlist.py tests/test_pipeline_smoke.py tests/golden -q`.

Cơ sở sửa (từ bản `validator.py` và `schemas.py` vừa đọc trên main): bản hiện tại có 3 khiếm khuyết đúng như mã lỗi — (1) `carrier_servable_wards` là **một set duy nhất không gắn với hãng**, nên validate bằng dữ liệu SPX cho đơn GHN/GHTK vẫn "đúng" trên giấy; không có đối chiếu mã hành chính theo hãng và không phân biệt không-phục-vụ với sai mã sau sáp nhập; (2) GPS chỉ so khoảng cách tới centroid tỉnh với ngưỡng cứng 200km — vừa báo nhầm với tỉnh dài/rộng, vừa bỏ qua reverse-geocode theo ranh giới thật, và tọa độ không hợp lệ (0,0 / ngoài khoảng) không bị bắt; (3) lý do theo trường không đầy đủ: mã tỉnh/huyện không có trong Canonical DB thì không sinh lỗi (chỉ xã mới có), và các kiểm tra carrier/GPS bị bỏ qua khi đã có lỗi phân cấp. Bản dưới đây giữ nguyên interface cũ (`HierarchicalValidator(index, carrier_servable_wards)`, `validate(addr, preferred_carrier="SPX")`, `Decision/ValidationError/ValidationResult`, ngưỡng centroid 200km cho trường hợp không có reverse-geocoder) và chỉ bổ sung cơ chế đúng hãng, reverse-geocode và thu thập đủ lỗi.

(đã nộp file `src/address/domain/validator.py`)

(đã nộp file `tests/test_validator_carrier_gps.py`)

## File đã nộp

- `src/address/domain/validator.py`
- `tests/test_validator_carrier_gps.py`
