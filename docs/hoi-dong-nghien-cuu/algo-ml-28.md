# Sửa lỗi `fix/fix_normalizer_alias_expansion`: Normalizer hết trùng khóa alias, gán đúng expanded_diacritic, cấm expand token đơn mơ hồ

_algo-ml · Chuyên gia Thuật toán & AI · lần nộp 1_

## Việc được giao

Normalizer hết trùng khóa alias, gán đúng expanded_diacritic, cấm expand token đơn mơ hồ

## Bài làm

File `src/address/domain/vn_normalizer.py` được hệ thống báo không có trên nhánh `main` khi đọc trực tiếp, nên nộp lại trọn file từ bản trên nhánh fix, với đúng phương án hội đồng đã thống nhất. Lưu ý: chưa chạy build / test / lint — kết quả thật do CI báo lại. Cách chạy: `python -m py_compile src/address/domain/vn_normalizer.py && python -m pytest tests/test_vn_normalizer_alias_expansion.py -v`.

(đã nộp file `src/address/domain/vn_normalizer.py`)

(đã nộp file `tests/test_vn_normalizer_alias_expansion.py`)

## File đã nộp

- `src/address/domain/vn_normalizer.py`
- `tests/test_vn_normalizer_alias_expansion.py`
