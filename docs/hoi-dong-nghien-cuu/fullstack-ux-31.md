# Sửa lỗi `fix/fix_picker_suggestion_bypasses_hierarchy`: Picker kiểm tra phân cấp mã trước khi nhận gợi ý Goong

_fullstack-ux · Chuyên gia Ứng dụng & Tích hợp · lần nộp 1_

## Việc được giao

Picker kiểm tra phân cấp mã trước khi nhận gợi ý Goong

## Bài làm

Chưa chạy build/test trong phiên này (KHÔNG_CHẠY_LỆNH) — kết quả thật do CI báo lại. Cách kiểm tra: `python -m pytest tests/test_picker_hierarchy.py -v` và type-check TS của repo nếu có.

Nguyên nhân lỗi (đã thống nhất ở vòng tranh luận): `pickSuggestion` gán thẳng 3 mã từ gợi ý Goong vào form mà không kiểm tra quan hệ phân cấp, nên gợi ý sai tỉnh kiểu Phả Lại/Chí Linh/Hải Dương vẫn lọt vào UI cho input Cuôr Đăng/Đắk Lắk; thêm lỗi phụ là `updateDetail` dùng `status` cũ trong closure nên trạng thái VERIFIED còn sống sau khi khách đã sửa địa chỉ. Bản dưới đây sửa cả hai, giữ nguyên vai trò component picker trong repo.

(đã nộp file `src/ui/components/StructuredAddressPicker.tsx`)

(đã nộp file `tests/test_picker_hierarchy.py`)

## File đã nộp

- `src/ui/components/StructuredAddressPicker.tsx`
- `tests/test_picker_hierarchy.py`
