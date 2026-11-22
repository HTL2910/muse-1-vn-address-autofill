# Sửa lỗi `fix/fix_admin_etl_merger_2025`: Xóa map sáp nhập 2025 sai và hoàn thiện ETL admin

_backend-sec · Chuyên gia Backend & Bảo mật · lần nộp 1_

## Việc được giao

Xóa map sáp nhập 2025 sai và hoàn thiện ETL admin

## Bài làm

Chưa chạy (theo quy ước KHÔNG_CHẠY_LỆNH). CI chạy: `python -m pytest tests/test_admin_boundary_store.py tests/test_pipeline_smoke.py tests/golden -q`. Lưu ý bắt buộc: hệ thống vừa trả lời `src/address/infrastructure/admin_boundary_store.py`, `src/address/domain/schemas.py`, `tests/test_pipeline_smoke.py` là "không có trên nhánh main" dù danh sách công bố có, nên không thể giữ nguyên phần của người khác trong file gốc — bản dưới đây viết lại trọn vẹn `admin_boundary_store.py` theo đúng các hàm đã được hội đồng mô tả trong vòng debate (`build_ddl`, `load_admin_csv`, `activate_version`, `rollback_to_version`, `get_province`, `get_districts_in_province`, `get_wards_in_province`, `resolve_old_code_2025`, `UnknownAdminCode`), đồng thời thực thi phương án đã thống nhất cho `fix_admin_etl_merger_2025`.

(đã nộp file `src/address/infrastructure/admin_boundary_store.py`)

(đã nộp file `data/admin/merger_2025.csv`)

(đã nộp file `tests/test_admin_boundary_store.py`)

## File đã nộp

- `src/address/infrastructure/admin_boundary_store.py`
- `data/admin/merger_2025.csv`
- `tests/test_admin_boundary_store.py`
