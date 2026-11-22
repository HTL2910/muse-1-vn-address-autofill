# Sửa lỗi `fix/fix_normalizer_alias_expansion`: Normalizer hết trùng khóa alias, gán đúng expanded_diacritic, cấm expand token đơn mơ hồ

_algo-ml · Chuyên gia Thuật toán & AI · lần nộp 2_

## Việc được giao

Normalizer hết trùng khóa alias, gán đúng expanded_diacritic, cấm expand token đơn mơ hồ

## Bài làm

Chưa chạy build / test / lint — kết quả thật do CI báo lại. Cách chạy: `python -m py_compile src/address/domain/vn_normalizer.py src/address/domain/fuzzy_matcher.py src/address/domain/ranker.py && python -m pytest tests/test_vn_normalizer_alias_expansion.py tests/test_fuzzy_exact_match_district_veto.py tests/test_ranker_dedupe.py tests/golden/test_golden_regression.py -v`.

Nguyên nhân `golden_11` còn fail, dù mã đúng `79/760/76002`: `FuzzyMatcher` tạo `AddressContract` mà không truyền `street` / `house_number` / `confidence` từ `ParsedAddress`. `AddressContract.confidence` mặc định `0.0`, `has_house_or_street` luôn `False`, nên `Validator` luôn cộng warning `MISSING_HOUSE_OR_STREET` + penalty `0.15`, gate hạ `VERIFIED` thành `NEEDS_REVIEW` cho mọi địa chỉ đúng. Fix ở đây: fuzzy truyền đúng `street`, `house_number`, `hamlet` và `confidence = score` vào contract ở cả hai nhánh exact và fallback, và fallback không khớp mờ cho tên xã quá ngắn (`<4` ký tự) — difflib trên chuỗi ngắn tạo ứng viên giả cho `bi` / `Xom`. Kèm theo đó là fix normalizer đã nộp (không trùng alias, gán đúng `expanded_diacritic`, cấm expand `hn`/`sg`/`d` đơn) và ranker dedupe theo `ward_code` để margin không về 0 khi cùng một xã được trả từ nhiều nguồn.

(đã nộp file `src/address/domain/vn_normalizer.py`)

(đã nộp file `src/address/domain/fuzzy_matcher.py`)

(đã nộp file `src/address/domain/ranker.py`)

(đã nộp file `tests/test_vn_normalizer_alias_expansion.py`)

(đã nộp file `tests/test_fuzzy_exact_match_district_veto.py`)

## File đã nộp

- `src/address/domain/vn_normalizer.py`
- `src/address/domain/fuzzy_matcher.py`
- `src/address/domain/ranker.py`
- `tests/test_vn_normalizer_alias_expansion.py`
- `tests/test_fuzzy_exact_match_district_veto.py`
