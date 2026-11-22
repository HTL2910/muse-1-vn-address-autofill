# Sửa lỗi `fix/fix_input_guard_substring_false_level`: Input guard nhận diện cấp hành chính theo ranh giới từ, không substring

_algo-ml · Chuyên gia Thuật toán & AI · lần nộp 1_

## Việc được giao

Input guard nhận diện cấp hành chính theo ranh giới từ, không substring

## Bài làm

File `src/address/domain/input_guard.py` được hệ thống báo không có trên nhánh `main` khi đọc trực tiếp, nên nộp lại trọn file từ bản trên nhánh fix, với đúng phương án hội đồng đã thống nhất. Lưu ý: chưa chạy build / test / lint — kết quả thật do CI báo lại. Cách chạy: `python -m py_compile src/address/domain/input_guard.py && python -m pytest tests/test_input_guard_word_boundary.py tests/golden/test_golden_regression.py -v`.

(đã nộp file `src/address/domain/input_guard.py`)

(đã nộp file `tests/test_input_guard_word_boundary.py`)

## File đã nộp

- `src/address/domain/input_guard.py`
- `tests/test_input_guard_word_boundary.py`
