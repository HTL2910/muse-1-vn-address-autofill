# Sửa lỗi `fix/fix_missing_pipeline_core`: Khôi phục lõi pipeline và chốt manifest cây file thật

_systems-arch · Chuyên gia Kiến trúc Hệ thống · lần nộp 1_

## Việc được giao

Đóng băng merge, xuất cây file thật bằng git ls-files kèm hash từng file và đối chiếu với danh sách công bố. Khôi phục đúng 1 bản canonical cho các file thiếu từ nhánh expert sở hữu approach tương ứng, không viết lại bản thứ hai: schemas.py, structured_parser.py, province_anchor.py, input_guard.py, fuzzy_matcher.py, ranker.py, validator.py, confidence_gate.py, address_service.py, cộng address_gateway.py dạng facade mỏng gọi service. address_service là entry point duy nhất chạy đúng thứ tự Normalize, Parse, Anchor, Match, Geocode, Rank, Validate, Gate, Commit hoặc NEEDS_REVIEW hoặc REJECTED; adapter và API không được gọi thẳng provider. Thêm CI import smoke test cho toàn pipeline và manifest check.

## Bài làm

Nhánh `fix/fix_missing_pipeline_core` — khôi phục 1 bản canonical cho lõi pipeline, đã sửa các lỗi đã chốt trong debate: schema không raise khi gặp default, Aho-Corasick đúng transition, gate không dùng điểm chưa hiệu chuẩn một cách im lặng. Chưa chạy build/test/lint; kết quả thật do CI báo lại. Cách chạy: `python scripts/manifest_check.py`, `python -m pytest tests/test_pipeline_smoke.py -v`, `python -m py_compile src/address/domain/*.py src/address/services/address_service.py src/address/api/address_gateway.py`.

(đã nộp file `src/address/domain/schemas.py`)

(đã nộp file `src/address/domain/structured_parser.py`)

(đã nộp file `src/address/domain/province_anchor.py`)

(đã nộp file `src/address/domain/input_guard.py`)

(đã nộp file `src/address/domain/fuzzy_matcher.py`)

(đã nộp file `src/address/domain/ranker.py`)

(đã nộp file `src/address/domain/validator.py`)

(đã nộp file `src/address/domain/confidence_gate.py`)

(đã nộp file `src/address/services/address_service.py`)

(đã nộp file `src/address/api/address_gateway.py`)

(đã nộp file `tests/test_pipeline_smoke.py`)

(đã nộp file `scripts/manifest_check.py`)

(đã nộp file `.github/workflows/address-pipeline.yml`)

## File đã nộp

- `src/address/domain/schemas.py`
- `src/address/domain/structured_parser.py`
- `src/address/domain/province_anchor.py`
- `src/address/domain/input_guard.py`
- `src/address/domain/fuzzy_matcher.py`
- `src/address/domain/ranker.py`
- `src/address/domain/validator.py`
- `src/address/domain/confidence_gate.py`
- `src/address/services/address_service.py`
- `src/address/api/address_gateway.py`
- `tests/test_pipeline_smoke.py`
- `scripts/manifest_check.py`
- `.github/workflows/address-pipeline.yml`
