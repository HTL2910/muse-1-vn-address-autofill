# Sửa lỗi `fix/fix_adapter_fail_closed_allowlist`: Đảo adapter sang allowlist fail-closed và thống nhất trạng thái

_systems-arch · Chuyên gia Kiến trúc Hệ thống · lần nộp 3_

## Việc được giao

Đảo adapter sang allowlist fail-closed và thống nhất trạng thái

## Bài làm

Nhánh `fix/fix_adapter_fail_closed_allowlist` — sửa lần 2 theo log fail mới. Log mới chứng minh 2 test mâu thuẫn nếu hiểu sai: smoke test `test_forbidden_default_never_returned` yêu cầu `"Xã Đăk Yă, Mang Yang, Gia Lai"` trần (không số nhà/đường) phải REJECTED, còn golden_02 yêu cầu Đăk Yă do khách ghi phải giữ tỉnh 64. Cách hòa giải duy nhất đúng cả hai: cấm default chỉ khi input trần đúng y hệt default và không có detail — đó là dạng fallback hệ thống tự sinh; còn địa chỉ đầy đủ chứa Đăk Yă + số nhà/đường là địa chỉ thật của khách, được chấp nhận với tỉnh 64. Lần sửa trước đã bỏ hẳn cấm default và thêm bonus exact-admin làm địa chỉ trần cũng VERIFIED, nên phá smoke test. Lần này khôi phục cấm default dạng trần, bỏ bonus exact-admin, giữ lại các fix đúng ở lần trước: mapping `REJECTED_INSUFFICIENT -> BLOCKED_UNVERIFIED`, phân cấp qua `parent_code` (không prefix, mã thật 66/652 không có tính prefix), `reject()` giữ tỉnh đã neo để golden đọc được `province_code` khi bị chặn. Chưa chạy build/test/lint; kết quả thật do CI báo lại. Cách chạy: `python -m pytest tests/test_adapter_allowlist.py tests/test_pipeline_smoke.py tests/golden/test_golden_regression.py -v`.

(đã nộp file `src/address/domain/confidence_gate.py`)

(đã nộp file `src/address/domain/schemas.py`)

(đã nộp file `src/address/domain/validator.py`)

(đã nộp file `src/address/domain/province_anchor.py`)

(đã nộp file `src/address/domain/fuzzy_matcher.py`)

(đã nộp file `src/address/services/address_service.py`)

(đã nộp file `src/integrations/pancake_goong_shipping_adapter.py`)

(đã nộp file `tests/test_adapter_allowlist.py`)

## File đã nộp

- `src/address/domain/confidence_gate.py`
- `src/address/domain/schemas.py`
- `src/address/domain/validator.py`
- `src/address/domain/province_anchor.py`
- `src/address/domain/fuzzy_matcher.py`
- `src/address/services/address_service.py`
- `src/integrations/pancake_goong_shipping_adapter.py`
- `tests/test_adapter_allowlist.py`
