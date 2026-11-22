# Sửa lỗi `fix/fix_aho_corasick_missed_mention`: Sửa Aho-Corasick xử lý lại ký tự hiện tại sau fail

_algo-ml · Chuyên gia Thuật toán & AI · lần nộp 2_

## Việc được giao

Sửa Aho-Corasick xử lý lại ký tự hiện tại sau fail

## Bài làm

[[BAT-DAU-7fd09e]]
Chưa chạy build / test / lint — kết quả thật do CI báo lại. Cách chạy: `python -m py_compile src/address/domain/province_anchor.py src/address/domain/input_guard.py src/address/domain/validator.py src/address/domain/fuzzy_matcher.py src/address/domain/ranker.py src/address/domain/vn_normalizer.py && python -m pytest tests/test_aho_corasick_missed_mention.py tests/test_input_guard_word_boundary.py tests/test_input_guard_parsed_evidence.py tests/test_fuzzy_exact_match_district_veto.py tests/test_validator_raw_house_street.py tests/test_pipeline_smoke.py tests/golden/test_golden_regression.py -v`.

Lần chạy trước tụt từ 111 xuống 113 passed nhưng 26 failed, và phân tích cho thấy các file tôi nộp ở lượt trước tự gây ra phần lớn hồi quy, không phải do Aho-Corasick gốc:

1. `province_anchor.py` của tôi bắt `isinstance(unit, AdminUnit)` trong `extract_admin_mentions` — các unit trong index thật (đọc từ CSV / `admin_boundary_store`) không phải class `AdminUnit` của file này nên mọi mention bị loại, anchor mất tỉnh, pipeline `REJECTED`/`REJECTED_INSUFFICIENT` cho cả địa chỉ đúng `golden_02/03/12` và `test_valid_full_address_verified`. Fix: chuyển sang duck-typing và chuẩn hóa mọi unit (dict hay object, khóa `level`/`type`, `parent_code`/`parent`) thành `AdminUnit` của file này ngay khi nạp index.
2. Cùng file: `dak lak` khớp cả pattern `lak` (huyện Lắk) ở trong lòng nó, nên mention huyện bị nhiễm thêm `645` ngoài `Cư M'gar`. Fix: giải quyết chồng lấn theo longest-match — sắp xếp theo vị trí bắt đầu, độ dài giảm dần, chỉ giữ match không chồng lấn với match đã giữ.
3. `test_classic_ushers` trong test của tôi tự sai chỉ số: `hers` trong `ushers` là `(2, 6)`, không phải `(1, 5)`. Sửa test.
4. `input_guard.py` của tôi làm 11 test word-boundary fail: (a) constructor không nhận `AdminIndex`/dict/kwarg khác tên nên raise khi test dựng guard theo cách đó — giờ constructor chấp nhận mọi dạng đó; (b) `GuardResult` là dataclass không nhận `passed=` khi test dựng trực tiếp — giờ là class thường nhận `decision`/`reason_code`/`passed`/`status` ở mọi dạng; (c) quan trọng nhất, `_levels_from_parsed` kiểm tra bằng chứng trên toàn bộ `raw_tokens` nên tên xã nằm trong đoạn đường (`đường Lam Sơn`) vẫn được tính là cấp — giờ bằng chứng chỉ tính trên các đoạn hành chính (sau khi bỏ đoạn nhà/đường), đúng như các test `parsed_street_name_misparsed_as_ward_does_not_count` / `ward_name_inside_street_segment_does_not_count` yêu cầu.
5. `validator.py`: `_raw_has_house_or_street` đòi số nhà và tên đường nằm trong cùng một đoạn, nhưng địa chỉ thật tách chúng ra hai đoạn (`Số 12`, `đường Lê Lợi`) nên vẫn warning. Fix: gom dấu hiệu trên toàn bộ `raw_input` (có số nhà ở đâu đó VÀ có tên đường ở đâu đó), trong khi `Số 12` đứng một mình vẫn warning.

Giữ nguyên phần Aho-Corasick đã sửa (xử lý lại ký tự hiện tại sau fail) và các fix normalizer / fuzzy / ranker đã nộp trước đó.

(đã nộp file `src/address/domain/province_anchor.py`)

(đã nộp file `src/address/domain/input_guard.py`)

(đã nộp file `src/address/domain/validator.py`)

(đã nộp file `src/address/domain/vn_normalizer.py`)

(đã nộp file `src/address/domain/fuzzy_matcher.py`)

(đã nộp file `src/address/domain/ranker.py`)

(đã nộp file `tests/test_aho_corasick_missed_mention.py`)

(đã nộp file `tests/test_input_guard_parsed_evidence.py`)

(đã nộp file `tests/test_fuzzy_exact_match_district_veto.py`)

(đã nộp file `tests/test_validator_raw_house_street.py`)
[[HET-7fd09e]]

## File đã nộp

- `src/address/domain/province_anchor.py`
- `src/address/domain/input_guard.py`
- `src/address/domain/validator.py`
- `src/address/domain/vn_normalizer.py`
- `src/address/domain/fuzzy_matcher.py`
- `src/address/domain/ranker.py`
- `tests/test_aho_corasick_missed_mention.py`
- `tests/test_input_guard_parsed_evidence.py`
- `tests/test_fuzzy_exact_match_district_veto.py`
- `tests/test_validator_raw_house_street.py`
