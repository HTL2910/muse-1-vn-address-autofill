"""Manifest check — fix_missing_pipeline_core.

Chạy: python scripts/manifest_check.py
Đối chiếu cây file thật (git ls-files, nếu có git) với danh sách
công bố. Thiếu file nào thì exit 1, không cho merge. Đồng thời kiểm
tra mọi file .py trong pipeline decode được UTF-8 và không chứa
mojibake mẫu Ã / á».
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_FILES = [
    "src/address/domain/schemas.py",
    "src/address/domain/structured_parser.py",
    "src/address/domain/province_anchor.py",
    "src/address/domain/input_guard.py",
    "src/address/domain/fuzzy_matcher.py",
    "src/address/domain/ranker.py",
    "src/address/domain/validator.py",
    "src/address/domain/confidence_gate.py",
    "src/address/services/address_service.py",
    "src/address/api/address_gateway.py",
    "tests/test_pipeline_smoke.py",
    "scripts/manifest_check.py",
    ".github/workflows/address-pipeline.yml",
]


def git_ls_files() -> set[str] | None:
    try:
        out = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True, timeout=30)
        return {line.strip() for line in out.splitlines() if line.strip()}
    except Exception:
        return None


def main() -> int:
    missing = [f for f in EXPECTED_FILES if not (ROOT / f).is_file()]
    if missing:
        print(f"MISSING ON DISK: {missing}")
        return 1
    tracked = git_ls_files()
    if tracked is not None:
        not_tracked = [f for f in EXPECTED_FILES if f not in tracked]
        if not_tracked:
            print(f"NOT IN git ls-files (chưa commit / merge thiếu): {not_tracked}")
            return 1
    # UTF-8 + mojibake check cho pipeline.
    bad: list[str] = []
    for rel in EXPECTED_FILES:
        if not rel.endswith(".py"):
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            bad.append(rel)
            continue
        if "Ã" in text or "á»" in text:
            bad.append(rel)
    if bad:
        print(f"ENCODING/MOJIBAKE: {bad}")
        return 1
    print(f"MANIFEST OK: {len(EXPECTED_FILES)} files present and tracked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
