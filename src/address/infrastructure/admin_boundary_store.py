"""
Canonical Admin Boundary Store + ETL - fix_admin_etl_merger_2025
Branch: fix/fix_admin_etl_merger_2025

Council-agreed fix:
1. NO hard-coded 2025 merger map in code. The old constant contained
   '68' -> '62' (Lam Dong wrongly remapped to Kon Tum). It is removed as a
   data source; the name is kept only as an empty, deprecated placeholder so
   ``'68' in MERGER_2025_OLD_TO_NEW`` is always False.
2. Merger old->new mapping is DATA, loaded only from an official CSV
   (see data/admin/merger_2025.csv, produced from the General Statistics
   Office list) via load_merger_csv(). Nothing is merged by guessing.
3. A code that is ACTIVE in the currently active dataset is never remapped.
   Mapping is applied only to codes that are not active and are declared
   as old_codes of a unit, or appear in the loaded merger CSV whose target
   is active. This kills the bug class 'customer wrote province X, system
   filled province Y' at the data layer: resolve_old_code_2025('68') == '68'.
4. etl_load() is transactional (copy-on-write staging):
   stage -> validate_hierarchy -> verify checksum -> insert -> audit ->
   activate. Any failure leaves the previously active version untouched.
   rollback_to_version() is simply re-activating a previous version;
   no data is ever deleted by rollback.

Stdlib only. In-memory versioned datasets; production wiring can persist
the same datasets using build_ddl() tables without changing this API.
Status: chua chay - ket qua do CI bao.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

# Deprecated placeholder: intentionally EMPTY. Do not add entries here.
# 2025 merger data belongs in data/admin/merger_2025.csv, loaded at runtime.
MERGER_2025_OLD_TO_NEW: Dict[str, str] = {}


class UnknownAdminCode(ValueError):
    """Raised when a code is neither active nor a known old code."""


class HierarchyError(ValueError):
    """Raised when staged records break province>district>ward hierarchy."""


class ChecksumMismatch(ValueError):
    """Raised when staged data does not match the expected checksum."""


@dataclass(frozen=True)
class AdminRecord:
    code: str
    name: str
    level: str  # "province" | "district" | "ward"
    parent_code: str = ""
    old_codes: Tuple[str, ...] = ()
    effective_from: str = "2025-07-01"
    effective_to: str = ""

    def canonical(self) -> str:
        return "|".join([self.level, self.code, self.parent_code,
                         self.name, ",".join(sorted(self.old_codes)),
                         self.effective_from, self.effective_to])


@dataclass
class ETLResult:
    version: str
    source: str
    record_count: int
    checksum: str
    activated: bool
    loaded_at: float = field(default_factory=time.time)


def checksum_for(records: Sequence[AdminRecord]) -> str:
    payload = "\n".join(sorted(r.canonical() for r in records))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _split_old_codes(value: Union[str, Sequence[str], None]) -> Tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return tuple(c.strip() for c in value.replace(";", "|").split("|") if c.strip())
    return tuple(str(c).strip() for c in value if str(c).strip())


def load_admin_csv(source: Union[str, Path, io.TextIOBase]) -> List[AdminRecord]:
    """
    Parse an admin CSV into AdminRecords. Does NOT insert anything.
    Required columns: code, name, level. Optional: parent_code, old_codes
    ('|'-separated), effective_from, effective_to.
    `source` may be a path or an open text stream.
    """
    if isinstance(source, (str, Path)) and Path(source).exists():
        text = Path(source).read_text(encoding="utf-8")
    elif hasattr(source, "read"):
        text = source.read()  # type: ignore[union-attr]
    elif isinstance(source, str):
        text = source  # raw CSV text (used by tests / ETL jobs)
    else:
        raise FileNotFoundError(f"admin CSV source not found: {source!r}")

    records: List[AdminRecord] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        if not row.get("code") or not row.get("name") or not row.get("level"):
            raise HierarchyError(f"admin CSV row missing code/name/level: {row!r}")
        level = row["level"].strip().lower()
        if level not in ("province", "district", "ward"):
            raise HierarchyError(f"unknown admin level {level!r} for code {row['code']!r}")
        records.append(AdminRecord(
            code=str(row["code"]).strip(),
            name=row["name"].strip(),
            level=level,
            parent_code=str(row.get("parent_code") or "").strip(),
            old_codes=_split_old_codes(row.get("old_codes")),
            effective_from=str(row.get("effective_from") or "2025-07-01").strip(),
            effective_to=str(row.get("effective_to") or "").strip(),
        ))
    return records


def load_merger_csv(source: Union[str, Path, io.TextIOBase]) -> Dict[str, str]:
    """
    Load old_code -> new_code mapping from an official CSV with columns
    old_code, new_code. This is the ONLY way merger mapping enters the system.
    Chains (A->B, B->C) and self-maps are rejected.
    """
    if isinstance(source, (str, Path)) and Path(source).exists():
        text = Path(source).read_text(encoding="utf-8")
    elif hasattr(source, "read"):
        text = source.read()  # type: ignore[union-attr]
    elif isinstance(source, str):
        text = source
    else:
        raise FileNotFoundError(f"merger CSV source not found: {source!r}")

    mapping: Dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(text)):
        old = str(row.get("old_code") or "").strip()
        new = str(row.get("new_code") or "").strip()
        if not old or not new:
            raise HierarchyError(f"merger CSV row missing old_code/new_code: {row!r}")
        if old == new:
            raise HierarchyError(f"merger self-map is meaningless: {old} -> {new}")
        if old in mapping and mapping[old] != new:
            raise HierarchyError(f"conflicting merger target for old code {old}")
        mapping[old] = new
    for old, new in mapping.items():
        if new in mapping:
            raise HierarchyError(f"merger chain is not allowed: {old} -> {new} -> {mapping[new]}")
    return mapping


def validate_hierarchy(records: Sequence[AdminRecord]) -> List[str]:
    """Return a list of hierarchy errors (empty list means valid)."""
    errors: List[str] = []
    by_code: Dict[str, AdminRecord] = {}
    for rec in records:
        if rec.code in by_code:
            errors.append(f"duplicate code: {rec.code}")
        by_code[rec.code] = rec
    for rec in records:
        if rec.level == "province":
            if rec.parent_code:
                errors.append(f"province {rec.code} must not have parent {rec.parent_code}")
        elif rec.level == "district":
            parent = by_code.get(rec.parent_code)
            if parent is None or parent.level != "province":
                errors.append(f"district {rec.code} parent {rec.parent_code} is not a province")
        elif rec.level == "ward":
            parent = by_code.get(rec.parent_code)
            if parent is None or parent.level != "district":
                errors.append(f"ward {rec.code} parent {rec.parent_code} is not a district")
            elif parent.parent_code not in by_code or by_code[parent.parent_code].level != "province":
                errors.append(f"ward {rec.code} grandparent {parent.parent_code} is not a province")
        # An active code must never also be declared as an old code of
        # another unit - that is exactly the 68 -> 62 class of corruption.
        for old in rec.old_codes:
            if old in by_code and old != rec.code:
                errors.append(f"code {old} is both active and an old_code of {rec.code}")
    return errors


class AdminBoundaryStore:
    """Versioned canonical store. Lookups always use the active version."""

    def __init__(self, merger_mapping: Optional[Dict[str, str]] = None):
        self._versions: Dict[str, Dict[str, AdminRecord]] = {}
        self._checksums: Dict[str, str] = {}
        self._active_version: Optional[str] = None
        self._merger_mapping: Dict[str, str] = dict(merger_mapping or {})
        self._audit: List[dict] = []

    # -- DDL for production persistence --------------------------------------
    @staticmethod
    def build_ddl() -> str:
        return """
        CREATE TABLE IF NOT EXISTS admin_versions (
            version TEXT PRIMARY KEY, source TEXT NOT NULL,
            checksum TEXT NOT NULL, activated_at REAL);
        CREATE TABLE IF NOT EXISTS admin_units (
            version TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
            level TEXT NOT NULL, parent_code TEXT NOT NULL DEFAULT '',
            old_codes TEXT NOT NULL DEFAULT '',
            effective_from TEXT NOT NULL, effective_to TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (version, code));
        CREATE TABLE IF NOT EXISTS admin_mergers (
            old_code TEXT PRIMARY KEY, new_code TEXT NOT NULL,
            source TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS admin_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, version TEXT NOT NULL,
            action TEXT NOT NULL, source TEXT NOT NULL,
            checksum TEXT NOT NULL, detail TEXT NOT NULL, at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_admin_units_parent
            ON admin_units(version, parent_code);
        """

    # -- ETL -------------------------------------------------------------------
    def set_merger_mapping(self, mapping: Dict[str, str]) -> None:
        # Targets must be checked against a dataset at resolve time, not here,
        # because mapping may be loaded before the new dataset is activated.
        for old, new in mapping.items():
            if old == new or new in mapping:
                raise HierarchyError(f"invalid merger mapping entry: {old} -> {new}")
        self._merger_mapping = dict(mapping)

    def etl_load(self, records: Sequence[AdminRecord], version: str, source: str,
                 expected_checksum: Optional[str] = None,
                 merger_mapping: Optional[Dict[str, str]] = None,
                 activate: bool = True) -> ETLResult:
        """
        Transactional load (copy-on-write): everything is staged and
        validated first; the active dataset is swapped only at the very end.
        On any error the previously active version keeps serving lookups.
        """
        if not version:
            raise HierarchyError("ETL version must not be empty")
        if version in self._versions:
            raise HierarchyError(f"admin version already loaded (immutable): {version}")
        staged_records = list(records)
        if not staged_records:
            raise HierarchyError("ETL staged zero records - refusing to activate an empty version")

        digest = checksum_for(staged_records)
        if expected_checksum is not None and expected_checksum != digest:
            raise ChecksumMismatch(
                f"checksum mismatch for version {version}: expected {expected_checksum}, got {digest}")

        errors = validate_hierarchy(staged_records)
        if errors:
            raise HierarchyError("hierarchy validation failed: " + "; ".join(errors))

        if merger_mapping is not None:
            self.set_merger_mapping(merger_mapping)
        # A merger entry whose old_code is ACTIVE in the staged dataset is
        # corrupt data (e.g. 68 -> 62 while 68 is active Lam Dong): reject
        # the whole load rather than silently remapping a live province.
        staged_codes = {r.code for r in staged_records}
        bad = {o: n for o, n in self._merger_mapping.items() if o in staged_codes}
        if bad:
            raise HierarchyError(f"merger mapping remaps active codes, refusing load: {bad}")
        bad_targets = {o: n for o, n in self._merger_mapping.items()
                       if n not in staged_codes and any(r.level == 'province' for r in staged_records)
                       and o not in staged_codes and n not in {c for c in staged_codes}}
        # Targets are only enforced when the mapping is meant for this dataset:
        # entries pointing outside this (possibly partial/seed) dataset are
        # kept but resolve_old_code_2025() will refuse to use them.
        _ = bad_targets

        staged: Dict[str, AdminRecord] = {r.code: r for r in staged_records}
        # Commit point: single swap, then audit, then activation.
        self._versions[version] = staged
        self._checksums[version] = digest
        self._audit.append({"version": version, "action": "etl_load", "source": source,
                            "checksum": digest,
                            "detail": json.dumps({"records": len(staged_records)}, ensure_ascii=False),
                            "at": time.time()})
        if activate:
            self.activate_version(version)
        return ETLResult(version=version, source=source, record_count=len(staged_records),
                         checksum=digest, activated=activate and self._active_version == version)

    def activate_version(self, version: str) -> None:
        if version not in self._versions:
            raise UnknownAdminCode(f"unknown admin version: {version}")
        self._active_version = version
        self._audit.append({"version": version, "action": "activate", "source": "",
                            "checksum": self._checksums.get(version, ""), "detail": "",
                            "at": time.time()})

    def rollback_to_version(self, version: str) -> None:
        """Rollback is re-activation of a previous version; nothing is deleted."""
        self.activate_version(version)
        self._audit[-1]["action"] = "rollback_activate"

    @property
    def active_version(self) -> Optional[str]:
        return self._active_version

    def audit_log(self) -> List[dict]:
        return list(self._audit)

    # -- Code resolution ---------------------------------------------------------
    def _active(self) -> Dict[str, AdminRecord]:
        if self._active_version is None:
            raise UnknownAdminCode("no active admin version - run etl_load first")
        return self._versions[self._active_version]

    def resolve_old_code_2025(self, code: str) -> str:
        """
        Resolve a possibly-old province/unit code to its active code.
        Rule order: (1) active code wins, never remapped; (2) old_codes
        declared on an active unit; (3) loaded merger CSV, only if its
        target is active. Unknown codes are returned unchanged so callers
        can raise/report with full context instead of receiving a guess.
        """
        code = str(code).strip()
        active = self._active()
        if code in active:
            return code
        for rec in active.values():
            if code in rec.old_codes:
                return rec.code
        target = self._merger_mapping.get(code)
        if target is not None and target in active:
            return target
        return code

    def _resolve_province_code(self, code: str) -> str:
        resolved = self.resolve_old_code_2025(code)
        rec = self._active().get(resolved)
        if rec is None or rec.level != "province":
            raise UnknownAdminCode(f"unknown province code: {code}")
        return resolved

    # -- Lookups -------------------------------------------------------------------
    def get_unit(self, code: str) -> AdminRecord:
        rec = self._active().get(str(code).strip())
        if rec is None:
            raise UnknownAdminCode(f"unknown admin code: {code}")
        return rec

    def get_province(self, code: str) -> AdminRecord:
        return self.get_unit(self._resolve_province_code(code))

    def get_district(self, code: str) -> AdminRecord:
        rec = self.get_unit(code)
        if rec.level != "district":
            raise UnknownAdminCode(f"code is not a district: {code}")
        return rec

    def get_ward(self, code: str) -> AdminRecord:
        rec = self.get_unit(code)
        if rec.level != "ward":
            raise UnknownAdminCode(f"code is not a ward: {code}")
        return rec

    def get_districts_in_province(self, province_code: str) -> List[AdminRecord]:
        resolved = self._resolve_province_code(province_code)
        return sorted([r for r in self._active().values()
                       if r.level == "district" and r.parent_code == resolved],
                      key=lambda r: r.code)

    def get_wards_in_district(self, district_code: str) -> List[AdminRecord]:
        district = self.get_district(district_code)
        return sorted([r for r in self._active().values()
                       if r.level == "ward" and r.parent_code == district.code],
                      key=lambda r: r.code)

    def get_wards_in_province(self, province_code: str) -> List[AdminRecord]:
        district_codes = {d.code for d in self.get_districts_in_province(province_code)}
        return sorted([r for r in self._active().values()
                       if r.level == "ward" and r.parent_code in district_codes],
                      key=lambda r: r.code)

    # Convenience aliases used by other modules / older callers.
    load_admin_csv = staticmethod(load_admin_csv)
    load_merger_csv = staticmethod(load_merger_csv)
    validate_hierarchy = staticmethod(validate_hierarchy)
    checksum_for = staticmethod(checksum_for)
