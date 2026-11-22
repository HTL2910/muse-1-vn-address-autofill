# Sửa lỗi `fix/fix_missing_golden_tests`: Bổ sung bộ test hồi quy golden từ các ca lỗi thật

_fullstack-ux · Chuyên gia Ứng dụng & Tích hợp · lần nộp 1_

## Việc được giao

Bổ sung bộ test hồi quy golden từ các ca lỗi thật

## Bài làm

Chưa chạy pytest — theo quy ước KHÔNG_CHẠY_LỆNH, kết quả thật do CI của repo báo lại. Cách chạy: `python -m pytest tests/golden -v`, gate bắt buộc pass 100% trước merge.

Ghi chú thiết kế (đã hiệu chỉnh so với bản thảo trước): ca địa chỉ "Xã Đăk Yă, Mang Yang, Gia Lai" khi khách *thật sự ghi đúng như vậy* không bị cấm — cái bị cấm là hệ thống *tự sinh* địa chỉ đó làm mặc định cho input không đọc được. Vì vậy ca cấm mặc định được khóa ở golden_01 (input không đọc được) và ở assert toàn cục trong runner, không khóa sai vào ca khách ghi thật.

(đã nộp file `tests/golden/golden_cases_vn.json`)

(đã nộp file `tests/golden/test_golden_regression.py`)

## File đã nộp

- `tests/golden/golden_cases_vn.json`
- `tests/golden/test_golden_regression.py`
