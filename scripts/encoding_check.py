#!/usr/bin/env python3
"""
Kiểm tra encoding UTF-8 + mojibake cho toàn repo — fix_encoding_utf8.

Chạy:  python scripts/encoding_check.py [repo_root]
Mã thoát 0 nếu sạch, 1 nếu có vi phạm (dùng làm CI gate).

Quy tắc:
1) Mọi file văn bản (đuôi thuộc TEXT_EXTENSIONS, trừ .git/cache và các
   file loại trừ) PHẢI giải mã được bằng UTF-8.
2) File code/dữ liệu (.py .json .csv .ts .tsx .yml .yaml) không được
   chứa mẫu mojibake — dấu vết UTF-8 bị đọc/ghi như Latin-1/CP1252
   (ví dụ chuỗi đúng 'Đắk Lắk' khi hỏng sẽ thành dạng chứa 'Ä' + ký tự
   lạ và mất dấu gốc). Chỉ dùng các cụm đủ đặc trưng, không dùng ký tự
   đơn như 'Â'/'Ã' đứng một mình vì chúng có thể xuất hiện hợp lệ.
   File .md chỉ áp quy tắc 1: tài liệu được phép trích dẫn chuỗi hỏng
   làm ví dụ khi tranh luận, ép quy tắc 2 lên .md sẽ đỏ giả vĩnh viễn.
3) Chuỗi mẫu tiếng Việt đúng dấu bắt buộc tồn tại trong dữ liệu thật
   của repo: 'Đắk Lắk' và 'Cuôr Đăng' (hai địa danh trong ca lỗi gốc).
   Nếu một đợt sửa encoding làm hỏng cả hai chuỗi này ở mọi nơi, CI đỏ.

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


def iter_text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in TEXT_EXTENSIONS or path.name in (
                ".editorconfig", ".gitignore"):
            yield path


def find_violations(root: Path) -> list:
    """Trả danh sách chuỗi mô tả vi phạm; rỗng nghĩa là sạch."""
    root = Path(root)
    violations: list[str] = []
    sample_hits = {s: 0 for s in REQUIRED_SAMPLE_STRINGS}
    for path in iter_text_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            violations.append(f"{rel}: không giải mã được UTF-8 ({exc})")
            continue
        if rel not in SELF_EXCLUDE:
            for sample in REQUIRED_SAMPLE_STRINGS:
                if sample in text:
                    sample_hits[sample] += 1
        if (path.suffix.lower() in MOJIBAKE_SCAN_EXTENSIONS
                and rel not in SELF_EXCLUDE):
            for pattern in MOJIBAKE_PATTERNS:
                if pattern in text:
                    violations.append(
                        f"{rel}: chứa mẫu mojibake {pattern!r} — "
                        "file có dấu hiệu bị ghi sai encoding, hãy lưu lại "
                        "bằng UTF-8 đúng")
                    break
    for sample, hits in sample_hits.items():
        if hits == 0:
            violations.append(
                f"repo: không tìm thấy chuỗi mẫu đúng dấu {sample!r} ở bất "
                "kỳ file dữ liệu/code nào — dấu tiếng Việt có thể đã bị "
                "hỏng hàng loạt")
    return violations


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
