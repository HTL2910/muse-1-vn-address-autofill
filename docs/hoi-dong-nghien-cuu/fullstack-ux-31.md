# Sửa lỗi `fix/fix_picker_suggestion_bypasses_hierarchy`: Picker kiểm tra phân cấp mã trước khi nhận gợi ý Goong

_fullstack-ux · Chuyên gia Ứng dụng & Tích hợp · lần nộp 2_

## Việc được giao

Picker kiểm tra phân cấp mã trước khi nhận gợi ý Goong

## Bài làm

Chưa chạy lại test trong phiên này (KHÔNG_CHẠY_LỆNH) — kết quả thật do CI báo lại.

Đọc kỹ log fail: **không có test nào của picker bị fail** (bản sửa `StructuredAddressPicker.tsx` đã đạt). Toàn bộ fail còn lại dồn vào `InputGuard` và lan sang golden — với bằng chứng cụ thể trong traceback:

- `InputGuard(known_provinces=..., known_districts=..., known_wards=...)` → `TypeError: InputGuard() takes no arguments`: constructor chưa nhận danh sách tên chuẩn để inject.
- `result.decision` → `'GuardDecision' object has no attribute 'decision'`: `check()` đang trả thẳng enum, trong khi contract đúng là object kết quả có `.decision` và `.passed`.
- `check()` chưa nhận `parsed` (positional/kwarg) làm bằng chứng thứ hai, và việc đếm cấp hành chính chưa gắn với *bằng chứng trong raw ở ranh giới từ, ngoài đoạn tên đường* — đúng các test `test_input_guard_parsed_evidence` / `test_input_guard_word_boundary` đang đỏ, kéo theo golden_01/04/05/10/11.

Vì vậy đợt nộp này giữ nguyên picker + test của nó, và viết lại `src/address/domain/input_guard.py` đúng contract đó (GuardResult vẫn `==` được với GuardDecision để không phá caller cũ trong service).

(đã nộp file `src/ui/components/StructuredAddressPicker.tsx`)

(đã nộp file `tests/test_picker_hierarchy.py`)

(đã nộp file `src/address/domain/input_guard.py`)

## File đã nộp

- `src/ui/components/StructuredAddressPicker.tsx`
- `tests/test_picker_hierarchy.py`
- `src/address/domain/input_guard.py`
