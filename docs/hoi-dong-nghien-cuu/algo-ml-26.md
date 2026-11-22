# Sửa lỗi `fix/fix_fuzzy_exact_match_ignores_district`: Exact-match phải qua cùng veto tỉnh và huyện như nhánh thường

_algo-ml · Chuyên gia Thuật toán & AI · lần nộp 3_

## Việc được giao

Exact-match phải qua cùng veto tỉnh và huyện như nhánh thường

## Bài làm

Chưa chạy build / test / lint — kết quả thật do CI báo lại. Cách chạy: `python -m py_compile src/address/domain/fuzzy_matcher.py src/address/domain/ranker.py && python -m pytest tests/test_fuzzy_exact_match_district_veto.py tests/test_ranker_dedupe.py tests/golden/test_golden_regression.py tests/test_pipeline_smoke.py -v`.

Nguyên nhân `golden_11` vẫn fail sau bản fix trước: fuzzy đã đúng mã `79/760/76002` nhưng pipeline vẫn trả `NEEDS_REVIEW`. Ranker gốc không dedupe theo `ward_code`: cùng một xã `76002` được trả về từ nhiều nguồn (parser / fuzzy / geocoder) với cùng điểm `1.0` thì `top1 == top2`, `margin = 0`, gate hạ thành `NEEDS_REVIEW` dù địa chỉ đúng hoàn toàn. Dedupe trong fuzzy chỉ xử lý trùng trong một nguồn, không xử lý trùng liên nguồn. Fix ranker: gộp ứng viên theo `(province_code, district_code, ward_code)`, giữ điểm cao nhất, tính `margin` với xã khác tiếp theo; fuzzy giữ nguyên veto `parent_code` tối thiểu đã nộp trước (không kiểm tra `level`, giữ khóa huyện `district_units[0]`, veto `parent_code=None`, chống trùng).

(đã nộp file `src/address/domain/fuzzy_matcher.py`)

(đã nộp file `src/address/domain/ranker.py`)

(đã nộp file `tests/test_fuzzy_exact_match_district_veto.py`)

(đã nộp file `tests/test_ranker_dedupe.py`)

## File đã nộp

- `src/address/domain/fuzzy_matcher.py`
- `src/address/domain/ranker.py`
- `tests/test_fuzzy_exact_match_district_veto.py`
- `tests/test_ranker_dedupe.py`
