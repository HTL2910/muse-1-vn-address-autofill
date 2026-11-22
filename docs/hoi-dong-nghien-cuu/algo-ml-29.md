# Sửa lỗi `fix/fix_aho_corasick_missed_mention`: Sửa Aho-Corasick xử lý lại ký tự hiện tại sau fail

_algo-ml · Chuyên gia Thuật toán & AI · lần nộp 1_

## Việc được giao

Sửa Aho-Corasick xử lý lại ký tự hiện tại sau fail

## Bài làm

[[BAT-DAU-ca58e5]]
Chưa chạy build / test / lint — kết quả thật do CI báo lại. Cách chạy: `python -m py_compile src/address/domain/province_anchor.py && python -m pytest tests/test_aho_corasick_missed_mention.py tests/test_fuzzy_exact_match_district_veto.py tests/test_pipeline_smoke.py -v`.

Lỗi: trong vòng lặp tìm kiếm Aho-Corasick, khi trạng thái hiện tại không có chuyển đổi cho ký tự đang xét, code cũ đi theo fail-link rồi bỏ luôn ký tự đó và chuyển sang ký tự tiếp theo. Ký tự đó chính là ký tự đầu tiên của một pattern khác (ví dụ sau khi khớp `ab`, ký tự `a` tiếp theo mở ra `ba` trong `aba`; hoặc sau một match một phần, ký tự đầu của tên tỉnh/huyện/xã tiếp theo bị nuốt mất), nên mention đó không bao giờ được tìm thấy — anchor mất tỉnh, fuzzy mất xã, pipeline trả về `NEEDS_REVIEW`/`REJECTED` cho địa chỉ đúng.

Fix, đúng phương án hội đồng: sau khi đi theo fail-link, xử lý lại chính ký tự hiện tại tại trạng thái mới (`while state != root and ch not in children[state]: state = fail[state]`, rồi mới thử chuyển đổi bằng `ch`), không bao giờ bỏ qua `ch`. Kèm theo đó là thu thập output qua dict-suffix-link để các pattern là hậu tố của pattern khác (ví dụ `lak` trong `dak lak`, `he` trong `she`) cũng được trả về, và kiểm tra ranh giới từ khi trích mention để `dak lakx` không khớp `dak lak`.

(đã nộp file `src/address/domain/province_anchor.py`)

(đã nộp file `tests/test_aho_corasick_missed_mention.py`)
[[HET-ca58e5]]

## File đã nộp

- `src/address/domain/province_anchor.py`
- `tests/test_aho_corasick_missed_mention.py`
