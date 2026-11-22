# Sửa lỗi `fix/fix_adapter_idempotency_audit_pii`: Sửa idempotency theo đơn và audit append-only mã hóa PII

_backend-sec · Chuyên gia Backend & Bảo mật · lần nộp 2_

## Việc được giao

Sửa idempotency theo đơn và audit append-only mã hóa PII

## Bài làm

Chưa chạy (theo quy ước KHÔNG_CHẠY_LỆNH). CI chạy: `python -m pytest tests/test_adapter_idempotency_audit.py tests/test_adapter_allowlist.py tests/test_pipeline_smoke.py -q`.

Nguyên nhân test fail lần trước nằm ngay trong logic tôi vừa nộp, không phải ở test: tôi giữ quy tắc cũ "mọi trạng thái thuộc `_TERMINAL_STATUSES` (gồm cả `BLOCKED_UNVERIFIED`) đều không bị thay thế ở latest-view", nên chuỗi hợp lệ `BLOCKED_UNVERIFIED -> NEEDS_REVIEW -> COMMITTED` bị kẹt ở bản ghi chặn đầu tiên và `get_by_idempotency_key` không bao giờ thấy `COMMITTED`. Quy tắc đúng cho idempotency theo đơn: `BLOCKED_*/NEEDS_REVIEW/LABEL_FAILED` chỉ là kết thúc của **một lần thử** — đơn được xử lý lại sau khi khách sửa địa chỉ thì quyết định mới phải thắng; chỉ `COMMITTED` mới là trạng thái **dính theo đơn** (đã có nhãn thì không quyết định nào thay thế được, muốn đổi phải hủy nhãn trước), và `DRAFT` không bao giờ thay thế một quyết định thật. Bản dưới đây sửa đúng điểm đó trong `InMemoryAuditStore.save()`, giữ nguyên mọi phần còn lại của lần nộp trước (allowlist, key theo đơn, hash-chain, mã hóa PII). 9 ca golden còn fail là lỗi pipeline ở các file không thuộc nhánh này (đã fail giống hệt ở mọi nhánh fix khác, `run_one` không đi qua adapter) — tôi không sửa được chúng từ file adapter và không tuyên bố đã sửa.

(đã nộp file `src/integrations/pancake_goong_shipping_adapter.py`)

(đã nộp file `tests/test_adapter_idempotency_audit.py`)

## File đã nộp

- `src/integrations/pancake_goong_shipping_adapter.py`
- `tests/test_adapter_idempotency_audit.py`
