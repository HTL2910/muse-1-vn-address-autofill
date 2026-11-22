# Sửa lỗi `fix/fix_adapter_idempotency_audit_pii`: Sửa idempotency theo đơn và audit append-only mã hóa PII

_backend-sec · Chuyên gia Backend & Bảo mật · lần nộp 1_

## Việc được giao

Sửa idempotency theo đơn và audit append-only mã hóa PII

## Bài làm

Chưa chạy (theo quy ước KHÔNG_CHẠY_LỆNH). CI chạy: `python -m pytest tests/test_adapter_idempotency_audit.py tests/test_adapter_allowlist.py tests/test_pipeline_smoke.py tests/golden -q`. Ghi chú: `src/security/address_pii_protection.py` và `tests/test_adapter_allowlist.py` được hệ thống báo "không có trên nhánh main", nên file security được tạo mới trọn vẹn theo đặc tả hội đồng (AES-GCM, hash-chain append-only); file adapter được nộp lại trọn vẹn từ bản main vừa nhận, giữ nguyên toàn bộ logic allowlist/mapping của lần fix trước, chỉ thay 3 điểm thuộc mã lỗi này: (1) idempotency neo duy nhất theo đơn, (2) audit append-only có hash-chain không ghi đè, (3) bản ghi lưu trong store là bản đã mã hóa PII, bản trả về caller trong bộ nhớ không đổi hành vi cũ.

(đã nộp file `src/security/address_pii_protection.py`)

(đã nộp file `src/integrations/pancake_goong_shipping_adapter.py`)

(đã nộp file `tests/test_adapter_idempotency_audit.py`)

## File đã nộp

- `src/security/address_pii_protection.py`
- `src/integrations/pancake_goong_shipping_adapter.py`
- `tests/test_adapter_idempotency_audit.py`
