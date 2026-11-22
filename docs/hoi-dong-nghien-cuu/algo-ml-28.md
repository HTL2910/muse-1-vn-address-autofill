# Sửa lỗi `fix/fix_normalizer_alias_expansion`: Normalizer hết trùng khóa alias, gán đúng expanded_diacritic, cấm expand token đơn mơ hồ

_algo-ml · Chuyên gia Thuật toán & AI · lần nộp 3_

## Việc được giao

Normalizer hết trùng khóa alias, gán đúng expanded_diacritic, cấm expand token đơn mơ hồ

## Bài làm

Chưa chạy build / test / lint — kết quả thật do CI báo lại. Cách chạy: `python -m py_compile src/address/domain/vn_normalizer.py src/address/domain/input_guard.py src/address/domain/fuzzy_matcher.py src/address/domain/ranker.py src/address/domain/validator.py && python -m pytest tests/test_vn_normalizer_alias_expansion.py tests/test_input_guard_parsed_evidence.py tests/test_fuzzy_exact_match_district_veto.py tests/test_ranker_dedupe.py tests/test_validator_raw_house_street.py tests/golden/test_golden_regression.py -v`.

Lần chạy trước vẫn 7 golden fail (111 passed, 1 error `test_review_queue` đã có từ trước, ngoài phạm vi nhánh này). Phân tích lại cho thấy ba nguyên nhân còn lại, đều nằm trong các file nộp dưới đây, không phải do normalizer:

1. `golden_11` đúng mã `79/760/76002` vẫn `NEEDS_REVIEW`: parser có thể bỏ sót `street`/`house_number` ở dạng `Số 12, đường Lê Lợi` (trong khi `127 Nguyễn Trãi` ở golden_12 parse được), và `FuzzyMatcher` trước đây đã tạo contract không có street/house/confidence nên `Validator` luôn cộng warning `MISSING_HOUSE_OR_STREET`. Fix ở hai lớp: `FuzzyMatcher` tự suy street/house từ `raw_input` khi `parsed` thiếu (nhận `Số <n>`, `đường <tên>`, `<n> <tên đường>`), và `Validator` chỉ warning khi cả contract lẫn `raw_input` đều không có số nhà + tên đường.
2. `golden_04/05/08/09` (`Xom`, `Sói 799, đường Lam Sơn`, `bi`/`Bị`) phải `REJECTED` nhưng vẫn lọt qua: `InputGuard._levels_from_parsed` trước đây đếm mọi tên cấp hành chính mà parser điền, kể cả tên không hề xuất hiện trong `raw_input` (parser đoán từ tra cứu mờ, ví dụ `Lam Sơn` → suy ra huyện/tỉnh ở Thanh Hóa). Fix: một cấp chỉ được tính khi tên chuẩn hóa của nó xuất hiện đúng như dãy token liên tiếp trong `raw_input`, và khi có bảng known thì phải khớp đúng tên của chính cấp đó. Với `bi`/`Xom`, tên parser đoán (`Phú Cường`, `Thanh Mỹ`...) không có trong raw nên không được tính, chỉ còn tối đa 1 cấp → reject `MISSING_PROVINCE`.
3. `golden_10` và phần còn lại của `golden_11`: giữ nguyên fix đã nộp — normalizer không trùng alias, gán đúng `expanded_diacritic`, cấm expand `hn`/`sg`/`d` đơn; ranker dedupe theo `ward_code` để margin không về 0 khi cùng một xã về từ nhiều nguồn; fuzzy veto chỉ bằng `parent_code`, không kiểm tra level, và không khớp mờ cho tên xã dưới 4 ký tự.

(đã nộp file `src/address/domain/vn_normalizer.py`)

(đã nộp file `src/address/domain/input_guard.py`)

(đã nộp file `src/address/domain/fuzzy_matcher.py`)

(đã nộp file `src/address/domain/ranker.py`)

(đã nộp file `src/address/domain/validator.py`)

(đã nộp file `tests/test_vn_normalizer_alias_expansion.py`)

(đã nộp file `tests/test_input_guard_parsed_evidence.py`)

(đã nộp file `tests/test_fuzzy_exact_match_district_veto.py`)

(đã nộp file `tests/test_validator_raw_house_street.py`)

## File đã nộp

- `src/address/domain/vn_normalizer.py`
- `src/address/domain/input_guard.py`
- `src/address/domain/fuzzy_matcher.py`
- `src/address/domain/ranker.py`
- `src/address/domain/validator.py`
- `tests/test_vn_normalizer_alias_expansion.py`
- `tests/test_input_guard_parsed_evidence.py`
- `tests/test_fuzzy_exact_match_district_veto.py`
- `tests/test_validator_raw_house_street.py`
