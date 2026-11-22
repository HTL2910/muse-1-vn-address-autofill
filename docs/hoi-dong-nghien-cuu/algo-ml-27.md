# Sửa lỗi `fix/fix_input_guard_substring_false_level`: Input guard nhận diện cấp hành chính theo ranh giới từ, không substring

_algo-ml · Chuyên gia Thuật toán & AI · lần nộp 2_

## Việc được giao

Input guard nhận diện cấp hành chính theo ranh giới từ, không substring

## Bài làm

Nguyên nhân fail chính: `AddressService.resolve` gọi `self.guard.check(raw_input, parsed)` với 2 đối số, nhưng bản `input_guard.py` đã nộp chỉ nhận `check(raw)` nên `TypeError` làm sập toàn bộ pipeline — 12 golden + 6 pipeline smoke đều fail theo. Bản fix này giữ nguyên phương án ranh giới từ, nhưng khôi phục tương thích interface: `check(raw, parsed=None, *args, **kwargs)`, dùng cả mức đã parse có cấu trúc (chỉ tính khi tên parse khớp đúng tên chuẩn của đúng cấp đó, không substring). Lỗi thứ hai: `margin` float `1.0-0.9 = 0.09999999999999998 != 0.1` — làm tròn `margin` trong ranker về 4 chữ số và dùng `pytest.approx` trong test. Lưu ý: chưa chạy build / test / lint — kết quả thật do CI báo lại. Cách chạy: `python -m py_compile src/address/domain/input_guard.py src/address/domain/ranker.py && python -m pytest tests/test_input_guard_word_boundary.py tests/test_ranker_dedupe.py tests/golden/test_golden_regression.py tests/test_pipeline_smoke.py -v`.

(đã nộp file `src/address/domain/input_guard.py`)

(đã nộp file `src/address/domain/ranker.py`)

(đã nộp file `tests/test_input_guard_word_boundary.py`)

(đã nộp file `tests/test_ranker_dedupe.py`)

## File đã nộp

- `src/address/domain/input_guard.py`
- `src/address/domain/ranker.py`
- `tests/test_input_guard_word_boundary.py`
- `tests/test_ranker_dedupe.py`
