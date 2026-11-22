# Sửa lỗi `fix/fix_validator_carrier_gps`: Validator kiểm chứng đúng hãng, GPS lệch tỉnh và đủ lý do theo trường

_backend-sec · Chuyên gia Backend & Bảo mật · lần nộp 2_

## Việc được giao

Validator kiểm chứng đúng hãng, GPS lệch tỉnh và đủ lý do theo trường

## Bài làm

Chưa chạy (theo quy ước KHÔNG_CHẠY_LỆNH). CI chạy: `python -m pytest tests/ -q`.

Nguyên nhân collection error rất rõ và không nằm ở validator: `tests/test_admin_boundary_store.py` import `ChecksumMismatch` (cùng các tên khác của bản ETL đã được hội đồng duyệt ở nhánh `fix_admin_etl_merger_2025`) từ `admin_boundary_store.py`, nhưng bản store đang nằm trên nhánh này là bản cũ không có các tên đó — test của nhánh admin đã được gộp vào mà code tương ứng thì chưa, nên pytest chết ngay ở khâu collection và **toàn bộ test khác, kể cả validator vừa sửa, không được chạy**. Cách sửa dứt điểm trong phạm vi vòng này: nộp kèm bản `admin_boundary_store.py` hoàn chỉnh của fix ETL (xuất đủ `ChecksumMismatch, HierarchyError, UnknownAdminCode, AdminRecord, AdminBoundaryStore, ETLResult, checksum_for, load_admin_csv, load_merger_csv, validate_hierarchy, seed_records, get_default_store` và các alias tương thích), đồng thời nộp lại nguyên vẹn `validator.py` + test của vòng trước để CI đánh giá cả hai cùng lúc.

(đã nộp file `src/address/infrastructure/admin_boundary_store.py`)

(đã nộp file `src/address/domain/validator.py`)

## File đã nộp

- `src/address/infrastructure/admin_boundary_store.py`
- `src/address/domain/validator.py`
