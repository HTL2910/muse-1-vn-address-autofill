# Sửa lỗi `fix/fix_fuzzy_exact_match_ignores_district`: Exact-match phải qua cùng veto tỉnh và huyện như nhánh thường

_algo-ml · Chuyên gia Thuật toán & AI · lần nộp 1_

## Việc được giao

Exact-match phải qua cùng veto tỉnh và huyện như nhánh thường

## Bài làm

Đã sửa `fix_fuzzy_exact_match_ignores_district` trên nhánh `fix/fix_fuzzy_exact_match_ignores_district`. Lưu ý: chưa chạy build / test / lint — kết quả thật do CI của repo báo lại. Cách chạy kiểm tra: `python -m py_compile src/address/domain/fuzzy_matcher.py && python -m pytest tests/test_fuzzy_exact_match_district_veto.py tests/golden/test_golden_regression.py -v`.

(đã nộp file `src/address/domain/fuzzy_matcher.py`)

(đã nộp file `tests/test_fuzzy_exact_match_district_veto.py`)

## File đã nộp

- `src/address/domain/fuzzy_matcher.py`
- `tests/test_fuzzy_exact_match_district_veto.py`
