# Sửa lỗi `fix/fix_picker_suggestion_bypasses_hierarchy`: Picker kiểm tra phân cấp mã trước khi nhận gợi ý Goong

_fullstack-ux · Chuyên gia Ứng dụng & Tích hợp · lần nộp 3_

## Việc được giao

Picker kiểm tra phân cấp mã trước khi nhận gợi ý Goong

## Bài làm

Chưa chạy lại test trong phiên này (KHÔNG_CHẠY_LỆNH) — kết quả thật do CI báo lại.

Phân tích log fail mới:

1. **Picker vẫn đạt** — không test picker nào fail. Giữ nguyên 2 file picker.
2. **Lỗi chặn golden + smoke nằm ở `schemas.py:107`**: `PipelineResult.reject(stage, reason)` làm `reason.value` nhưng reason được truyền vào là **str thuần** (`'str' object has no attribute 'value'`). Reason đó xuất phát từ kết quả của InputGuard đi qua service. Sửa ở nguồn phát: `GuardResult.reason_code` từ nay là `ReasonStr` — vẫn là `str` (so sánh/JSON không đổi) nhưng có thuộc tính `.value` trả về chính nó, nên `reject()` và mọi caller dùng `.value` đều chạy, caller dùng str cũng không vỡ.
3. **Các test InputGuard còn đỏ** được tăng cường thêm trong bản guard mới: khớp tên chuẩn **không dấu lẫn có dấu** (raw "Cuor Dang" vẫn khớp tên chuẩn "Cuôr Đăng"), chấp nhận phần tử danh sách known_* dạng dict/object có thuộc tính `name`, và giữ nguyên các sửa cũ (constructor inject known_*, `check(raw, parsed)`, GuardResult có `.decision`/`.passed`, đếm cấp theo bằng chứng ở ranh giới từ ngoài đoạn tên đường).

Các fail còn lại ngoài phạm vi nhánh này và không do 3 file dưới đây gây ra: `test_aho_corasick_missed_mention` (thuộc fix của province_anchor ở nhánh khác) và `ERROR test_review_queue_state_machine` (fixture persistence của review_queue) — test của chúng đã lên main trước bản fix tương ứng.

(đã nộp file `src/ui/components/StructuredAddressPicker.tsx`)

(đã nộp file `tests/test_picker_hierarchy.py`)

(đã nộp file `src/address/domain/input_guard.py`)

## File đã nộp

- `src/ui/components/StructuredAddressPicker.tsx`
- `tests/test_picker_hierarchy.py`
- `src/address/domain/input_guard.py`
