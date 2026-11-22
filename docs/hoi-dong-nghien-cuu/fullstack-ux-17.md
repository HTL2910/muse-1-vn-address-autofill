# Sửa lỗi `fix/fix_missing_golden_tests`: Bổ sung bộ test hồi quy golden từ các ca lỗi thật

_fullstack-ux · Chuyên gia Ứng dụng & Tích hợp · lần nộp 1_

## Việc được giao

Tạo tests/golden/golden_cases_vn.json và test runner nối run_golden_regression với pipeline thật qua InputGuard, province anchor và fuzzy matcher. Bao phủ tối thiểu 8 ca gốc: không đọc được thì REJECTED và không ra Xã Đăk Yă; xã Cuôr Đăng, Đắk Lắk thì tỉnh phải là Đắk Lắk, không bao giờ Hải Dương; Xom, 73, 127, bi, Bị, Sói 799 đường Lam Sơn thiếu ngữ cảnh thì REJECTED hoặc NEEDS_REVIEW, không VERIFIED; đơn đã khai báo tỉnh mà kết quả lệch tỉnh thì bị chặn 100 phần trăm; cộng ca hợp lệ đối chứng. Mỗi ca assert status, province_code, district_code, ward_code và reason_code. Viết test sớm để khóa hành vi, đưa pytest tests/golden vào CI làm gate bắt buộc pass 100 phần trăm trước merge.

## Bài làm

Chưa chạy pytest — theo quy ước KHÔNG_CHẠY_LỆNH, kết quả thật do CI của repo báo lại. Cách chạy: `python -m pytest tests/golden -v` (gate bắt buộc trong `.github/workflows/address-pipeline.yml`).

Lưu ý trung thực về trạng thái nhánh: khi xin đọc `observability.py`, `address_gateway.py`, `schemas.py`, `tests/test_pipeline_smoke.py` hệ thống đều trả "không có trên nhánh main" dù danh sách file công bố có. Vì vậy runner dưới đây được viết theo đúng contract hội đồng đã thống nhất (InputGuard → province anchor → fuzzy matcher, `run_golden_regression` nếu import được), và các test pipeline sẽ ĐỎ rõ ràng với thông báo thiếu entry point cho tới khi các file lõi được khôi phục lên main — đó là hành vi đúng của một regression gate, không dùng `skip` để che. Các test toàn vẹn dữ liệu golden (bao phủ ca, schema, cấm địa chỉ mặc định) chạy độc lập và không phụ thuộc pipeline.

(đã nộp file `tests/golden/golden_cases_vn.json`)

(đã nộp file `tests/golden/test_golden_regression.py`)

## File đã nộp

- `tests/golden/golden_cases_vn.json`
- `tests/golden/test_golden_regression.py`
