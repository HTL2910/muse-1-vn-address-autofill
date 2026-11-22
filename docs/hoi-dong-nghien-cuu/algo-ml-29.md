# Sửa lỗi `fix/fix_aho_corasick_missed_mention`: Sửa Aho-Corasick xử lý lại ký tự hiện tại sau fail

_algo-ml · Chuyên gia Thuật toán & AI · lần nộp 3_

## Việc được giao

Sửa Aho-Corasick xử lý lại ký tự hiện tại sau fail

## Bài làm

Chưa chạy build / test / lint — kết quả thật do CI báo lại. Cách chạy: `python -m py_compile src/address/domain/province_anchor.py src/address/domain/input_guard.py && python -m pytest tests/test_aho_corasick_missed_mention.py tests/test_input_guard_word_boundary.py tests/test_input_guard_parsed_evidence.py tests/test_fuzzy_exact_match_district_veto.py tests/test_pipeline_smoke.py tests/golden/test_golden_regression.py -v`.

Lần chạy trước 116 passed / 23 failed. Hai nhóm lỗi còn lại đều có manh mối mới trong traceback: ở `test_pipeline_smoke` anchor trả về `stage_errors={'anchor': 'MISSING_PROVINCE'}`, `matched_tokens=[]` cho input `xã Cuôr Đăng, Đắk Lắk` — tức anchor không khớp được token nào, dù tên tỉnh viết đầy đủ. Và 11 test `input_guard_word_boundary` fail đồng loạt dù logic đếm cấp đã đúng, tức lỗi ở dạng API chứ không phải logic.

Nguyên nhân và fix:

1. `province_anchor.py`: unit trong index thật không phải lúc nào cũng có `normalized_name` đã chuẩn hóa — nếu nó được tính bằng hàm khác (giữ dấu, giữ dấu nháy `cu m'gar`), pattern có dấu sẽ không bao giờ khớp text đã bỏ dấu của `normalize_text`, nên `matched_tokens=[]`. Fix: `_as_admin_unit` giờ LUÔN tính lại `normalized_name = normalize_text(name)`, bất kể giá trị có sẵn. Đồng thời chuẩn hóa `level` (`tinh`/`huyen`/`xa`/`quan`/`phuong`/`thi xa`/`thi tran`/`1`/`2`/`3` → `province`/`district`/`ward`) vì index thật có thể dùng tên cấp tiếng Việt. Và `AnchorResult` được thêm các trường mà service có thể đọc (`matched_tokens`, `unmatched_tokens`, `reason_code`, `status` dạng `AddressStatus` để `.value` vẫn dùng được) — trước đây thiếu chúng nên anchor có thể bị hiểu là thất bại.
2. `input_guard.py`: `GuardResult` ở lượt trước là class thường, trong khi bản đã pass (111 test) dùng dataclass — các test dựng/so sánh trực tiếp `GuardResult` (equality, `asdict`, dựng bằng `passed=`) sẽ fail với class thường. Fix: khôi phục `GuardResult` thành `@dataclass(init=False)` với `__init__` tự viết, vừa giữ equality/repr/`asdict` của dataclass, vừa nhận mọi dạng dựng (`decision`/`reason_code`/`message`, `passed=True/False`, `decision`/`reason_code` dạng str). Constructor `InputGuard` giữ dạng explicit `(known_provinces, known_districts, known_wards)` cộng `**kwargs` cho mọi dạng gọi khác (`admin_index`, `known_names`, `provinces=`...), và `_known_from_index` chuẩn hóa level tiếng Việt. Quy tắc bằng chứng trong đoạn hành chính (tên chỉ nằm trong đoạn đường thì không tính) giữ nguyên.

Phần Aho-Corasick (xử lý lại ký tự hiện tại sau fail), normalizer, fuzzy, ranker, validator giữ nguyên như lượt trước vì các test của chúng đã pass.

(đã nộp file `src/address/domain/province_anchor.py`)

(đã nộp file `src/address/domain/input_guard.py`)

(đã nộp file `tests/test_aho_corasick_missed_mention.py`)

(đã nộp file `tests/test_input_guard_parsed_evidence.py`)

## File đã nộp

- `src/address/domain/province_anchor.py`
- `src/address/domain/input_guard.py`
- `tests/test_aho_corasick_missed_mention.py`
- `tests/test_input_guard_parsed_evidence.py`
