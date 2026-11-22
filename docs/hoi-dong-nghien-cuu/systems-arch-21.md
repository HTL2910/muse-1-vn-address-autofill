# Sửa lỗi `fix/fix_adapter_fail_closed_allowlist`: Đảo adapter sang allowlist fail-closed và thống nhất trạng thái

_systems-arch · Chuyên gia Kiến trúc Hệ thống · lần nộp 2_

## Việc được giao

Đảo adapter sang allowlist fail-closed và thống nhất trạng thái

## Bài làm

Nhánh `fix/fix_adapter_fail_closed_allowlist` — sửa theo log test fail. Nguyên nhân gốc đã tìm ra: (1) mapping `REJECTED_INSUFFICIENT` bị collapse qua `AddressStatus.REJECTED` thành `BLOCKED_GATE_REJECTED` thay vì `BLOCKED_UNVERIFIED`; (2) `Đăk Yă` do khách tự ghi bị coi là default cấm, trong khi default cấm chỉ là fallback do hệ thống tự sinh — khách ghi thì là địa chỉ thật, tỉnh 64; (3) nghiêm trọng nhất: kiểm tra phân cấp bằng prefix chuỗi `district.startswith(province)`, `ward.startswith(district)` sai với mã hành chính thật Việt Nam (vd Đắk Lắk 66 / Cư M'gar 652 / Cuôr Đăng không bắt đầu bằng 652, Hà Nội 01 / Ba Đình 001), nên mọi địa chỉ hợp lệ đều bị `BLOCKED`, và `reject()` trả về contract rỗng làm `province_code=None` cho cả ca hợp lệ lẫn ca mismatch. Fix: phân cấp chỉ kiểm tra qua `parent_code` trong Canonical Admin DB, `reject()` giữ lại tỉnh đã neo, gate không reject input khách ghi Đăk Yă, exact-match hành chính đủ 3 cấp được VERIFIED dù thiếu số nhà/đường. Chưa chạy build/test/lint; kết quả thật do CI báo lại. Cách chạy: `python -m pytest tests/test_adapter_allowlist.py tests/golden/test_golden_regression.py -v`, `python -m py_compile src/integrations/pancake_goong_shipping_adapter.py src/address/domain/*.py src/address/services/address_service.py`.

(đã nộp file `src/integrations/pancake_goong_shipping_adapter.py`)

(đã nộp file `src/address/domain/schemas.py`)

(đã nộp file `src/address/domain/confidence_gate.py`)

(đã nộp file `src/address/domain/validator.py`)

(đã nộp file `src/address/domain/province_anchor.py`)

(đã nộp file `src/address/domain/fuzzy_matcher.py`)

(đã nộp file `src/address/services/address_service.py`)

(đã nộp file `tests/test_adapter_allowlist.py`)

## File đã nộp

- `src/integrations/pancake_goong_shipping_adapter.py`
- `src/address/domain/schemas.py`
- `src/address/domain/confidence_gate.py`
- `src/address/domain/validator.py`
- `src/address/domain/province_anchor.py`
- `src/address/domain/fuzzy_matcher.py`
- `src/address/services/address_service.py`
- `tests/test_adapter_allowlist.py`
