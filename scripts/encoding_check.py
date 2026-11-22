#!/usr/bin/env python3
"""
Kiểm tra encoding UTF-8 + mojibake cho toàn repo — fix_encoding_utf8.

Chạy:  python scripts/encoding_check.py [repo_root]
Mã thoát 0 nếu sạch, 1 nếu có vi phạm (dùng làm CI gate).

Quy tắc:
1) Mọi file văn bản (đuôi thuộc TEXT_EXTENSIONS, trừ .git/cache và các
   file loại trừ) PHẢI giải mã được bằng UTF-8.
2) File code/dữ liệu (.py .json .csv .ts .tsx .yml .yaml) không được
   chứa mẫu mojibake — dấu vết UTF-8 bị đọc/ghi như Latin-1/CP1252.
   Chỉ dùng các cụm đủ đặc trưng, không dùng ký tự đơn như 'Â'/'Ã' đứng
   một mình vì chúng có thể xuất hiện hợp lệ.
   File .md chỉ áp quy tắc 1: tài liệu được phép trích dẫn chuỗi hỏng
   làm ví dụ khi tranh luận, ép quy tắc 2 lên .md sẽ đỏ giả vĩnh viễn.
3) Chuỗi mẫu tiếng Việt đúng dấu bắt buộc tồn tại trong dữ liệu thật
   của repo: 'Đắk Lắk' và 'Cuôr Đăng' (hai địa danh trong ca lỗi gốc).

Cấu trúc: lõi kiểm tra là hàm thuần find_violations_in_bytes() trên
dict {đường_dẫn_tương_đối: bytes}; find_violations(root) chỉ đọc đĩa và
đổ vào lõi đó (lỗi đọc file được ghi thành vi phạm, không ném exception).
Nhờ vậy test không cần tạo thư mục tạm trên đĩa.

File này và tests/test_encoding_utf8.py được loại trừ khỏi quy tắc 2
vì chúng buộc phải chứa các mẫu mojibake làm hằng số kiểm tra.
"""
from __future__ import annotations

import sys
from pathlib import Path

TEXT_EXTENSIONS = {
    ".py", ".md", ".json", ".csv", ".ts", ".tsx", ".js",
    ".yml", ".yaml", ".txt",
}
MOJIBAKE_SCAN_EXTENSIONS = {
    ".py", ".json", ".csv", ".ts", ".tsx", ".js", ".yml", ".yaml",
}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
SELF_EXCLUDE = {
    "scripts/encoding_check.py",
    "tests/test_encoding_utf8.py",
}

# Các cụm mojibake điển hình (UTF-8 bị xử lý như Latin-1/CP1252).
MOJIBAKE_PATTERNS = (
    "Ã³", "Ã¡", "Ã©", "Ã¨", "Ãº", "Ã¹", "Ã¬", "Ã²", "Ã´",
    "á»“", "á»™", "á»±", "á»§", "á»‹", "â€",
    "Ä‘", "Æ°", "Æ¡", "áº¯", "áº±",
)

REQUIRED_SAMPLE_STRINGS = ("Đắk Lắk", "Cuôr Đăng")


def _suffix(rel: str) -> str:
    return Path(rel).suffix.lower()


def find_violations_in_bytes(files: dict) -> list:
    """
    Lõi thuần: files là {đường_dẫn_tương_đối: nội_dung_bytes}.
    Trả danh sách chuỗi mô tả vi phạm; rỗng nghĩa là sạch.
    """
    violations: list[str] = []
    sample_hits = {s: 0 for s in REQUIRED_SAMPLE_STRINGS}
    for rel in sorted(files):
        raw = files[rel]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            violations.append(f"{rel}: không giải mã được UTF-8 ({exc})")
            continue
        if rel not in SELF_EXCLUDE:
            for sample in REQUIRED_SAMPLE_STRINGS:
                if sample in text:
                    sample_hits[sample] += 1
        if _suffix(rel) in MOJIBAKE_SCAN_EXTENSIONS and rel not in SELF_EXCLUDE:
            for pattern in MOJIBAKE_PATTERNS:
                if pattern in text:
                    violations.append(
                        f"{rel}: chứa mẫu mojibake {pattern!r} — "
                        "file có dấu hiệu bị ghi sai encoding, hãy lưu "
                        "lại bằng UTF-8 đúng")
                    break
    for sample, hits in sample_hits.items():
        if hits == 0:
            violations.append(
                f"repo: không tìm thấy chuỗi mẫu đúng dấu {sample!r} ở "
                "bất kỳ file dữ liệu/code nào — dấu tiếng Việt có thể "
                "đã bị hỏng hàng loạt")
    return violations


def iter_text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in TEXT_EXTENSIONS or path.name in (
                ".editorconfig", ".gitignore"):
            yield path


def find_violations(root) -> list:
    """Đọc cây thư mục rồi kiểm tra bằng lõi thuần."""
    root = Path(root)
    files: dict[str, bytes] = {}
    read_errors: list[str] = []
    for path in iter_text_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            files[rel] = path.read_bytes()
        except OSError as exc:
            read_errors.append(f"{rel}: không đọc được file ({exc})")
    return read_errors + find_violations_in_bytes(files)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(argv[0]) if argv else Path(__file__).resolve().parents[1]
    violations = find_violations(root)
    if violations:
        print("ENCODING CHECK FAILED:")
        for violation in violations:
            print(f" - {violation}")
        return 1
    print("Encoding check OK: mọi file văn bản là UTF-8 hợp lệ, "
          "không có mojibake, chuỗi mẫu tiếng Việt nguyên vẹn.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
