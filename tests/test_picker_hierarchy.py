"""
Test tĩnh cho fix_picker_suggestion_bypasses_hierarchy.

Chưa chạy trong phiên này (KHÔNG_CHẠY_LỆNH); CI chạy:
    python -m pytest tests/test_picker_hierarchy.py -v

Component là TypeScript/React nên bộ test Python của repo kiểm tra các
bất biến cấu trúc bắt buộc trong mã nguồn:
1) pickSuggestion phải validate phân cấp TRƯỚC khi setForm/emit.
2) hasValidCodes + validateSuggestionHierarchy phải tồn tại và được dùng
   cả ở bước lọc gợi ý hiển thị lẫn bước nhận gợi ý.
3) updateDetail phải tính trạng thái từ form mới bằng deriveStatus, không
   được mang status cũ sang form đã sửa.
4) Nút submit phải bị khóa khi hasValidCodes(form) là false.
"""
from pathlib import Path

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "src" / "ui" / "components" / "StructuredAddressPicker.tsx"
).read_text(encoding="utf-8")


def _segment(start_marker: str, end_marker: str) -> str:
    start = SOURCE.index(start_marker)
    end = SOURCE.index(end_marker, start)
    return SOURCE[start:end]


def test_hierarchy_helpers_exist():
    assert "export function hasValidCodes" in SOURCE
    assert "export function validateSuggestionHierarchy" in SOURCE
    assert "DISTRICT_NOT_IN_PROVINCE" in SOURCE
    assert "WARD_NOT_IN_DISTRICT" in SOURCE
    assert "PROVINCE_ANCHOR_MISMATCH" in SOURCE


def test_pick_suggestion_validates_before_applying():
    segment = _segment("const pickSuggestion", "const updateDetail")
    assert "validateSuggestionHierarchy" in segment
    assert "hasValidCodes(suggestion)" in segment
    # Validate phải đứng trước mọi lần áp mã vào form.
    assert segment.index("validateSuggestionHierarchy") < segment.index("emit(nextForm)")
    # Nhánh lỗi không được gán mã: phải return false trước khi emit.
    assert "return false" in segment


def test_suggestion_list_is_prefiltered_by_hierarchy():
    segment = _segment("const search", "const pickSuggestion")
    assert "validateSuggestionHierarchy" in segment


def test_update_detail_recomputes_status_from_new_form():
    segment = _segment("const updateDetail", "const pickProvince")
    assert "deriveStatus(base)" in segment
    assert "status: form.status" not in SOURCE
    assert "status: deriveStatus(base)" in segment


def test_submit_requires_valid_codes():
    assert "const canSubmit = hasValidCodes(form)" in SOURCE
    assert "disabled={!canSubmit}" in SOURCE
