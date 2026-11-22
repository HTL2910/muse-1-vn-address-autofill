# Sửa lỗi `fix/fix_missing_golden_tests`: Bổ sung bộ test hồi quy golden từ các ca lỗi thật

_fullstack-ux · Chuyên gia Ứng dụng & Tích hợp · lần nộp 3_

## Việc được giao

Bổ sung bộ test hồi quy golden từ các ca lỗi thật

## Bài làm

Chưa chạy lại pytest trong phiên này (KHÔNG_CHẠY_LỆNH) — kết quả thật do CI báo lại. Nguyên nhân fail lần này đã rõ từ traceback và nằm trong *test harness của tôi*, không phải pipeline:

`AddressGateway.resolve_address` gọi `self._service.resolve(raw_input, carrier=carrier)`, nhưng hàm dựng instance dự phòng trong bản test trước đã truyền nhầm một `ConfidenceGate` vào vị trí `service` (gateway nhận theo duck-typing nên không báo ngay, đến khi gọi `.resolve` mới nổ `AttributeError: 'ConfidenceGate' object has no attribute 'resolve'`).

Bản sửa dưới đây:
- Xóa hoàn toàn fallback truyền bừa dependency. Chỉ truyền vào gateway một đối tượng *đã kiểm chứng có method `resolve`*.
- Dựng `AddressService` bằng bộ DI theo introspection: đọc chữ ký `__init__`, khớp tham số theo tên/annotation với các class thật trong repo (InputGuard, ProvinceAnchor, FuzzyMatcher, Validator, ConfidenceGate, GeocodingRouter, AdminBoundaryStore, Ranker, Normalizer, Parser...), đệ quy có giới hạn độ sâu và chống vòng lặp.
- Nếu không dựng được gateway thì dùng trực tiếp `AddressService.resolve` (chính là method gateway gọi) làm pipeline — vẫn kiểm thử đúng pipeline thật, không mock.
- Giữ nguyên bộ golden JSON v1.1.0 và toàn bộ assert.

(đã nộp file `tests/golden/golden_cases_vn.json`)

(đã nộp file `tests/golden/test_golden_regression.py`)

## File đã nộp

- `tests/golden/golden_cases_vn.json`
- `tests/golden/test_golden_regression.py`
