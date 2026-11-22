# Sửa lỗi `fix/fix_observability_dead_code`: Xóa code chết regionLock và sửa tỉ lệ forbidden_default

_fullstack-ux · Chuyên gia Ứng dụng & Tích hợp · lần nộp 3_

## Việc được giao

Xóa code chết regionLock và sửa tỉ lệ forbidden_default

## Bài làm

Chưa chạy lại test trong phiên này (KHÔNG_CHẠY_LỆNH) — kết quả thật do CI báo lại.

Đối chiếu log: **toàn bộ test của nhánh `fix_observability_dead_code` đã PASS** — danh sách fail không còn `tests/test_observability_dead_code.py` nào, số pass tăng 139 → 140. Các fail còn lại giữ nguyên ở 3 cụm ngoài phạm vi nhánh này, không thay đổi bởi file observability:

- `test_input_guard_*` (thiếu `detected_levels` trên GuardResult, guard chưa có danh sách tỉnh mặc định nên "xã Cuôr Đăng, Đắk Lắk" bị `MISSING_ADMIN_LEVELS`, kéo theo pipeline smoke + golden_03/10/11) — nhánh fix input_guard.
- `test_aho_corasick_missed_mention` — nhánh fix province_anchor.
- `ERROR test_review_queue_state_machine` — nhánh fix review_queue.

Nộp lại nguyên trạng 2 file của nhánh này để merge độc lập.

(đã nộp file `src/address/infrastructure/observability.py`)

(đã nộp file `tests/test_observability_dead_code.py`)

## File đã nộp

- `src/address/infrastructure/observability.py`
- `tests/test_observability_dead_code.py`
