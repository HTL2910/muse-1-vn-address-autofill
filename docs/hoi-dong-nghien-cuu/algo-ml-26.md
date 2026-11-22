# Sửa lỗi `fix/fix_fuzzy_exact_match_ignores_district`: Exact-match phải qua cùng veto tỉnh và huyện như nhánh thường

_algo-ml · Chuyên gia Thuật toán & AI · lần nộp 2_

## Việc được giao

Exact-match phải qua cùng veto tỉnh và huyện như nhánh thường

## Bài làm

Nguyên nhân gốc của lần fail trước: bản fix trước áp kiểm tra `level == "ward"/"district"` lên cả đường mentions, trong khi `AdminUnit.level` không đảm bảo là chuỗi đó — đường mentions gốc không hề kiểm tra level, chỉ kiểm tra `parent_code`. Khi level không khớp, ward/district đúng bị veto oan, fuzzy trả rỗng hoặc trả nhiều ứng viên, ranker `margin = top1-top2 = 0` nên `golden_11` đúng mã `79/760/76002` vẫn bị hạ thành `NEEDS_REVIEW` thay vì `VERIFIED`. Ngoài ra `_unique_code` bỏ khóa huyện khi có nhiều mention, làm exact-match trả nhiều ứng viên cùng điểm 1.0. Bản fix này quay về veto duy nhất bằng `parent_code` (đúng như validator đã quy định), giữ nguyên khóa huyện bằng `district_units[0]`, giữ nguyên thứ tự gốc, chỉ thêm veto `parent_code=None` và chống trùng ứng viên. Lưu ý: chưa chạy build / test / lint — kết quả thật do CI báo lại. Cách chạy: `python -m py_compile src/address/domain/fuzzy_matcher.py && python -m pytest tests/test_fuzzy_exact_match_district_veto.py tests/golden/test_golden_regression.py tests/test_pipeline_smoke.py -v`.

(đã nộp file `src/address/domain/fuzzy_matcher.py`)

(đã nộp file `tests/test_fuzzy_exact_match_district_veto.py`)

## File đã nộp

- `src/address/domain/fuzzy_matcher.py`
- `tests/test_fuzzy_exact_match_district_veto.py`
