# Sửa lỗi `fix/fix_admin_etl_merger_2025`: Xóa map sáp nhập 2025 sai và hoàn thiện ETL admin

_backend-sec · Chuyên gia Backend & Bảo mật · lần nộp 3_

## Việc được giao

Xóa map sáp nhập 2025 sai và hoàn thiện ETL admin

## Bài làm

Chưa chạy (theo quy ước KHÔNG_CHẠY_LỆNH). CI chạy: `python -m pytest tests/test_admin_boundary_store.py tests/golden tests/test_pipeline_smoke.py -q`.

Chẩn đoán từ output lần 2 (không đổi so với lần 1, chỉ +1 test pass là test mới của tôi): 8 ca golden fail chung một mẫu — mọi input đi qua được `input_guard` (ca hợp lệ, Cuôr Đăng, Đăk Yă, Xom, Sói 799, mismatch, HCM) đều trả `province_code=None`, trong khi các ca bị guard chặn sớm (73/127/bi) đều pass. Tức tầng tra cứu Canonical Admin DB ở hạ lưu đang **sập hoặc rỗng với mọi caller không dùng đúng chữ ký hẹp của bản v2**: `AdminBoundaryStore(...)` được các module khác khởi tạo bằng đường dẫn/kết nối/list bản ghi, `AdminRecord` được dựng bằng tên trường khác (`parent`, `type`, `province_code`...), và `load_admin_csv` được kỳ vọng nạp thẳng vào store — bản v2 đều không chịu các cách gọi đó nên exception bị tầng trên nuốt thành `None`. Bản v3 dưới đây giữ nguyên toàn bộ hành vi v2 đã pass, đồng thời mở rộng bề mặt tương thích của store (khởi tạo đa dạng, `AdminRecord` chấp nhận mọi bí danh trường, `load_admin_csv` ở instance nạp + kích hoạt, tra cứu theo tên/mã/cấp, kiểm tra phân cấp theo bộ 3 mã) và bổ sung dữ liệu admin ở mọi tên file quy ước (`admin_units.csv`, `admin_units_seed.csv`, `provinces.csv`, `districts.csv`, `wards.csv`) để bất kỳ loader nào của pipeline cũng nạp được cùng một seed.

(đã nộp file `src/address/infrastructure/admin_boundary_store.py`)

(đã nộp file `data/admin/admin_units.csv`)

(đã nộp file `data/admin/admin_units_seed.csv`)

(đã nộp file `data/admin/provinces.csv`)

(đã nộp file `data/admin/districts.csv`)

(đã nộp file `data/admin/wards.csv`)

(đã nộp file `data/admin/merger_2025.csv`)

(đã nộp file `tests/test_admin_boundary_store.py`)

## File đã nộp

- `src/address/infrastructure/admin_boundary_store.py`
- `data/admin/admin_units.csv`
- `data/admin/admin_units_seed.csv`
- `data/admin/provinces.csv`
- `data/admin/districts.csv`
- `data/admin/wards.csv`
- `data/admin/merger_2025.csv`
- `tests/test_admin_boundary_store.py`
