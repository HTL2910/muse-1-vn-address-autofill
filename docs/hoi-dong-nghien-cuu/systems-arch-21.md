# Sửa lỗi `fix/fix_adapter_fail_closed_allowlist`: Đảo adapter sang allowlist fail-closed và thống nhất trạng thái

_systems-arch · Chuyên gia Kiến trúc Hệ thống · lần nộp 1_

## Việc được giao

Đảo adapter sang allowlist fail-closed và thống nhất trạng thái

## Bài làm

Nhánh `fix/fix_adapter_fail_closed_allowlist` — đảo adapter sang allowlist fail-closed và thống nhất trạng thái. Đã xem bản mới nhất của adapter trên main; `schemas.py`, `address_service.py`, `confidence_gate.py` truy vấn trả về không có trên main nên nộp lại `schemas.py` canonical làm nguồn mapping trạng thái duy nhất, adapter import nó theo dạng try/except để không sập khi file chưa merge. Chưa chạy build/test/lint; kết quả thật do CI báo lại. Cách chạy: `python -m pytest tests/test_adapter_allowlist.py -v`, `python -m py_compile src/integrations/pancake_goong_shipping_adapter.py src/address/domain/schemas.py`.

(đã nộp file `src/integrations/pancake_goong_shipping_adapter.py`)

(đã nộp file `src/address/domain/schemas.py`)

(đã nộp file `tests/test_adapter_allowlist.py`)

## File đã nộp

- `src/integrations/pancake_goong_shipping_adapter.py`
- `src/address/domain/schemas.py`
- `tests/test_adapter_allowlist.py`
