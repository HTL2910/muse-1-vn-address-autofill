# Sửa lỗi `fix/fix_adapter_idempotency_audit_pii`: Sửa idempotency theo đơn và audit append-only mã hóa PII

_backend-sec · Chuyên gia Backend & Bảo mật · lần nộp 3_

## Việc được giao

Sửa idempotency theo đơn và audit append-only mã hóa PII

## Bài làm

Báo cáo trạng thái nhánh `fix/fix_adapter_idempotency_audit_pii` — không nộp lại file, vì bản sửa đầy đủ (adapter + `src/security/address_pii_protection.py` + `tests/test_adapter_idempotency_audit.py`) đã nằm trên nhánh từ vòng trước và vòng này không có thay đổi nào mới cần thêm:

**Phần thuộc mã lỗi được giao đã PASS hoàn toàn.** Đối chiếu hai lần chạy: vòng trước `10 failed, 46 passed` (gồm 1 fail của chính nhánh này: `test_audit_is_append_only_and_keeps_blocked_history`), vòng này `9 failed, 48 passed` và cả 9 fail còn lại đều nằm trong `tests/golden/test_golden_regression.py`. Tức test của nhánh này — idempotency neo theo đơn (sửa địa chỉ/SĐT không sinh key mới, không tạo nhãn thứ hai), audit append-only + hash-chain (chuỗi `BLOCKED_UNVERIFIED -> NEEDS_REVIEW -> COMMITTED` lên đúng latest-view, `COMMITTED` là trạng thái dính duy nhất), PII mã hóa khi lưu và không plaintext trong `audit_to_json` — đã xanh toàn bộ, cùng với `test_adapter_allowlist.py`, `test_admin_boundary_store.py` và `test_pipeline_smoke.py`.

**9 ca golden còn lại không thuộc phạm vi sửa được của nhánh này, và bằng chứng cho thấy chúng không do adapter gây ra:**
- Chúng fail **giống hệt, cùng 9 ca, cùng assertion `golden_02 ... province_code (None)`** ở mọi vòng chạy của mọi nhánh fix khác nhau (`fix_admin_etl_merger_2025` trước cả khi nhánh adapter này tồn tại, và các vòng audit trước đó), trong khi các file được sửa ở mỗi nhánh hoàn toàn khác nhau.
- Đường chạy của golden `run_one` là pipeline địa chỉ (`input_guard -> province_anchor -> matcher -> admin data -> gate` qua `address_service`/`observability`), không đi qua `pancake_goong_shipping_adapter` hay audit store: adapter chỉ được gọi sau khi pipeline đã chốt địa chỉ để tạo nhãn.
- Sửa chúng từ nhánh này đồng nghĩa viết đè lên file của các chuyên gia khác đang có nhánh fix riêng (`input_guard.py`, `province_anchor.py`, `address_service.py`, dữ liệu admin), vi phạm quy ước "giữ nguyên phần của người khác" và sẽ gây xung đột khi hội đồng gộp tất cả các nhánh — đúng thời điểm 9 ca này mới có thể xanh đồng thời, khi mọi mắt xích của pipeline cùng có mặt trên `main`.

Đề xuất với Trưởng nhóm: merge nhánh này trên cơ sở test riêng của nó đã pass, và đánh giá 9 ca golden ở vòng tích hợp sau khi gộp toàn bộ các nhánh fix, thay vì chặn từng nhánh riêng lẻ vì một bộ test tích hợp liên-nhánh.

(đã nộp file `tests/test_fix_adapter_idempotency_audit_pii.py`)

## File đã nộp

- `tests/test_fix_adapter_idempotency_audit_pii.py`
