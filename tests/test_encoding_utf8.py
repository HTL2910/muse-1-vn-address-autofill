"""
Test hồi quy cho fix_encoding_utf8.

Chưa chạy trong phiên này (KHÔNG_CHẠY_LỆNH); CI chạy:
    python -m pytest tests/test_encoding_utf8.py -v

Bao phủ:
1) src/__init__.py chứa đúng docstring tiếng Việt UTF-8, không mojibake.
2) Toàn repo sạch theo chính scripts/encoding_check.py (gate dùng ở CI).
3) Chuỗi mẫu 'Đắk Lắk' và 'Cuôr Đăng' tồn tại nguyên vẹn trong dữ liệu.
4) Test dương trên dữ liệu trong bộ nhớ (không tạo thư mục tạm trên đĩa
   — môi trường Windows từng báo PermissionError với tmp_path): bộ quét
   PHẢI bắt được file mojibake mẫu và file không giải mã được UTF-8, và
   PHẢI cho qua một cây sạch.
"""
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "encoding_check", REPO_ROOT / "scripts" / "encoding_check.py")
encoding_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(encoding_check)

CLEAN_DATA = '{"tinh": "Đắk Lắk", "xa": "Cuôr Đăng"}'.encode("utf-8")


def test_init_docstring_is_correct_utf8_vietnamese():
    text = (REPO_ROOT / "src" / "__init__.py").read_bytes().decode("utf-8")
    assert "Gói mã nguồn chính của dự án MuseAssistantTeam" in text
    for pattern in encoding_check.MOJIBAKE_PATTERNS:
        assert pattern not in text


def test_repo_has_no_encoding_violations():
    violations = encoding_check.find_violations(REPO_ROOT)
    assert violations == [], "\n".join(violations)


def test_sample_vietnamese_place_names_intact():
    golden = (REPO_ROOT / "tests" / "golden" / "golden_cases_vn.json")
    text = golden.read_bytes().decode("utf-8")
    assert "Đắk Lắk" in text
    assert "Cuôr Đăng" in text


def test_scanner_detects_mojibake_file():
    files = {
        "bad.py": 'x = "GÃ³i mÃ£ nguá»“n"\n'.encode("utf-8"),
        "data.json": CLEAN_DATA,
    }
    violations = encoding_check.find_violations_in_bytes(files)
    assert any("bad.py" in v for v in violations)


def test_scanner_detects_non_utf8_file():
    files = {
        "broken.py": b'x = "\xff\xfe invalid"\n',
        "data.json": CLEAN_DATA,
    }
    violations = encoding_check.find_violations_in_bytes(files)
    assert any("broken.py" in v for v in violations)


def test_scanner_accepts_clean_tree():
    files = {
        "clean.py": '"""Gói mã nguồn chính."""\n'.encode("utf-8"),
        "data.json": CLEAN_DATA,
    }
    assert encoding_check.find_violations_in_bytes(files) == []


def test_scanner_flags_missing_sample_strings():
    files = {"clean.py": b'x = "hello"\n'}
    violations = encoding_check.find_violations_in_bytes(files)
    assert any("Đắk Lắk" in v for v in violations)
    assert any("Cuôr Đăng" in v for v in violations)
