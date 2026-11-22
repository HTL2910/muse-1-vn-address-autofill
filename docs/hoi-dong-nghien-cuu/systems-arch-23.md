# Sửa lỗi `fix/fix_router_deadline_mismatch_leak`: Sửa router deadline chung, đếm mismatch theo request và rò rỉ executor

_systems-arch · Chuyên gia Kiến trúc Hệ thống · lần nộp 3_

## Việc được giao

Sửa router deadline chung, đếm mismatch theo request và rò rỉ executor

## Bài làm

Nhánh `fix/fix_router_deadline_mismatch_leak` — lỗi ở lần chạy trước chỉ là collection, không phải logic router/golden: `tests/test_admin_boundary_store.py` import `MERGER_2025_OLD_TO_NEW` từ `src/address/infrastructure/admin_boundary_store.py` nhưng bản nộp trước của tôi không export tên này, nên pytest dừng ở collection và không chạy test nào. Sửa: thêm lại hằng số đó ở cấp module, nạp từ `data/admin/merger_2025.csv` khi có file (ưu tiên CSV làm nguồn thật), fallback chỉ khi không có CSV, và đảm bảo không còn map sai `68 -> 62` (Lâm Đồng 68 vẫn active; Kon Tum 62 mới là mã cũ, map sang Quảng Ngãi 51). Các file router/pipeline đã nộp ở lần trước giữ nguyên trên nhánh, không nộp lại. Chưa chạy build/test/lint; kết quả thật do CI báo lại. Cách chạy: `python -m pytest tests/test_admin_boundary_store.py tests/test_geocoding_router.py tests/test_pipeline_smoke.py tests/golden/test_golden_regression.py -v`.

(đã nộp file `src/address/infrastructure/admin_boundary_store.py`)

## File đã nộp

- `src/address/infrastructure/admin_boundary_store.py`
