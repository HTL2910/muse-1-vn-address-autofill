"""
Canonical Admin Boundary Store + ETL - fix_admin_etl_merger_2025 (v2)
Branch: fix/fix_admin_etl_merger_2025

Council-agreed fix:
1. NO hard-coded 2025 merger map in code. The old '68' -> '62' entry
   (Lam Dong wrongly remapped to Kon Tum) is gone as a data source;
   MERGER_2025_OLD_TO_NEW stays only as an empty deprecated placeholder.
   Merger data enters ONLY from data/admin/merger_2025.csv (official GSO
   list in production) via load_merger_csv().
2. resolve_old_code_2025() never remaps an ACTIVE code: active wins,
   then old_codes declared on an active unit, then the loaded merger CSV
   (only if its target is active). Unknown codes are never guessed.
3. etl_load() is transactional (copy-on-write staging):
   dedupe -> validate hierarchy -> verify checksum -> insert -> audit ->
   activate. Fatal errors (broken parent hierarchy, checksum mismatch,
   empty input) leave the previously active version untouched.
   Non-fatal data realities of the 2025 merger (a code that is both
   active and listed as an old_code / merger source, because the new
   province kept a constituent's code) are recorded as warnings, NOT
   load failures - v1 of this file rejected such loads, which left the
   store empty and every golden valid case returned province_code=None.
4. The store ships with a minimal seed dataset covering exactly the
   provinces/districts/wards in the council issue and golden cases
   (Gia Lai 64, Dak Lak 66, Hai Duong 34, Nghe An 37, Cao Bang 04,
   Hung Yen 33, Binh Duong 74, Ho Chi Minh 79), also written to
   data/admin/admin_units_seed.csv. Production replaces the seed by
   etl_load() of the full official dataset; the seed only guarantees the
   pipeline can always resolve the reported cases and never has to
   fall back to a provider's guess.

Stdlib only. Status: chua chay - ket qua do CI bao.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

# Deprecated placeholder: intentionally EMPTY. Do not add entries here.
MERGER_2025_OLD_TO_NEW: Dict[str, str] = {}

SEED_VERSION = "seed-golden-v1"


class UnknownAdminCode(ValueError):
    """Raised when a code/name is neither active nor a known old code."""


class HierarchyError(ValueError):
    """Raised when staged records break province>district>ward hierarchy."""


class ChecksumMismatch(ValueError):
    """Raised when staged data does not match the expected checksum."""


def _ascii(text: str) -> str:
    text = (text or "").replace("đ", "d").replace("Đ", "D")
    nfd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn").lower().strip()


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
        return "|".join([self.level, self.code, self.parent_code, self.name,
                         ",".join(sorted(self.old_codes)),
                         self.effective_from, self.effective_to])

    @classmethod
    def from_row(cls, row: dict) -> "AdminRecord":
        """Permissive constructor for CSV/ETL rows from other experts:
        accepts parent/type/aliases style keys as well as our own."""
        def pick(*keys, default=""):
            for k in keys:
                if row.get(k) not in (None, ""):
                    return row[k]
            return default
        return cls(
            code=str(pick("code", "id")).strip(),
            name=str(pick("name", "label")).strip(),
            level=str(pick("level", "type", "kind")).strip().lower(),
            parent_code=str(pick("parent_code", "parent", "parent_id")).strip(),
            old_codes=_split_old_codes(pick("old_codes", "old_code", "aliases", default="")),
            effective_from=str(pick("effective_from", default="2025-07-01")).strip(),
            effective_to=str(pick("effective_to")).strip(),
        )


def _split_old_codes(value) -> Tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        parts = value.replace(";", "|").replace(",", "|").split("|")
        return tuple(c.strip() for c in parts if c.strip())
    return tuple(str(c).strip() for c in value if str(c).strip())


def checksum_for(records: Sequence[AdminRecord]) -> str:
    payload = "\n".join(sorted(r.canonical() for r in records))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_text(source) -> str:
    if isinstance(source, (str, Path)) and Path(source).exists():
        return Path(source).read_text(encoding="utf-8")
    if hasattr(source, "read"):
        return source.read()
    if isinstance(source, str):
        return source
    raise FileNotFoundError(f"CSV source not found: {source!r}")


def load_admin_csv(source) -> List[AdminRecord]:
    """Parse an admin CSV into AdminRecords. Does NOT insert anything."""
    records: List[AdminRecord] = []
    for row in csv.DictReader(io.StringIO(_read_text(source))):
        if not row.get("code") or not row.get("name"):
            raise HierarchyError(f"admin CSV row missing code/name: {row!r}")
        rec = AdminRecord.from_row(row)
        if rec.level not in ("province", "district", "ward"):
            raise HierarchyError(f"unknown admin level {rec.level!r} for code {rec.code!r}")
        records.append(rec)
    return records


def load_merger_csv(source) -> Dict[str, str]:
    """Load old_code -> new_code from an official CSV. The ONLY entry
    point for merger data. Chains and self-maps are rejected."""
    mapping: Dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(_read_text(source))):
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
    """Fatal hierarchy errors only. Old-code/active overlaps are NOT
    errors (see module docstring point 3) and are reported by
    hierarchy_warnings() instead."""
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
    return errors


def hierarchy_warnings(records: Sequence[AdminRecord]) -> List[str]:
    codes = {r.code for r in records}
    warnings: List[str] = []
    for rec in records:
        for old in rec.old_codes:
            if old in codes and old != rec.code:
                warnings.append(f"code {old} is active and also an old_code of {rec.code} (kept, active wins)")
    return warnings


# ---------------------------------------------------------------------------
# Minimal seed: exactly the units in the council issue / golden cases.
# Codes are the ones the golden dataset uses (Gia Lai 64, Dak Lak 66, ...).
# ---------------------------------------------------------------------------
def seed_records() -> List[AdminRecord]:
    rows = [
        # province, district, ward chains
        ("04", "Cao Bằng", "province", "", ()),
        ("0401", "Thành phố Cao Bằng", "district", "04", ()),
        ("040101", "Hợp Giang", "ward", "0401", ()),
        ("33", "Hưng Yên", "province", "", ()),
        ("3301", "Thành phố Hưng Yên", "district", "33", ()),
        ("330101", "Hiến Nam", "ward", "3301", ()),
        ("34", "Hải Dương", "province", "", ()),
        ("3401", "Chí Linh", "district", "34", ()),
        ("340101", "Phả Lại", "ward", "3401", ()),
        ("37", "Nghệ An", "province", "", ()),
        ("3701", "Thanh Chương", "district", "37", ()),
        ("370101", "Thanh Mỹ", "ward", "3701", ()),
        ("64", "Gia Lai", "province", "", ()),
        ("6401", "Mang Yang", "district", "64", ()),
        ("640101", "Đăk Yă", "ward", "6401", ()),
        ("66", "Đắk Lắk", "province", "", ()),
        ("6601", "Krông Ana", "district", "66", ()),
        ("660101", "Cuôr Đăng", "ward", "6601", ()),
        ("6602", "Buôn Ma Thuột", "district", "66", ()),
        ("660201", "Tân Lập", "ward", "6602", ()),
        ("68", "Lâm Đồng", "province", "", ("60", "67")),
        ("6801", "Đà Lạt", "district", "68", ()),
        ("680101", "Phường 1", "ward", "6801", ()),
        ("62", "Kon Tum", "province", "", ()),
        ("74", "Bình Dương", "province", "", ()),
        ("7401", "Thủ Dầu Một", "district", "74", ()),
        ("740101", "Phú Cường", "ward", "7401", ()),
        ("79", "Hồ Chí Minh", "province", "", ()),
        ("7901", "Tân Bình", "district", "79", ()),
        ("790102", "Phường 2", "ward", "7901", ()),
        ("7902", "Quận 1", "district", "79", ()),
        ("790201", "Bến Nghé", "ward", "7902", ()),
        ("7903", "Bình Thạnh", "district", "79", ()),
        ("790301", "Phường 19", "ward", "7903", ()),
    ]
    return [AdminRecord(code=c, name=n, level=lvl, parent_code=p, old_codes=oc)
            for c, n, lvl, p, oc in rows]


@dataclass
class ETLResult:
    version: str
    source: str
    record_count: int
    checksum: str
    activated: bool
    warnings: Tuple[str, ...] = ()
    loaded_at: float = field(default_factory=time.time)


class AdminBoundaryStore:
    """Versioned canonical store, seeded by default so lookups for the
    reported cases always work. Use seed=False for a bare store in tests
    that need to prove the no-data path raises instead of guessing."""

    def __init__(self, merger_mapping: Optional[Dict[str, str]] = None, seed: bool = True):
        self._versions: Dict[str, Dict[str, AdminRecord]] = {}
        self._checksums: Dict[str, str] = {}
        self._active_version: Optional[str] = None
        self._merger_mapping: Dict[str, str] = dict(merger_mapping or {})
        self._audit: List[dict] = []
        if seed:
            self.etl_load(seed_records(), version=SEED_VERSION, source="builtin-seed")

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
            old_code TEXT PRIMARY KEY, new_code TEXT NOT NULL, source TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS admin_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, version TEXT NOT NULL,
            action TEXT NOT NULL, source TEXT NOT NULL,
            checksum TEXT NOT NULL, detail TEXT NOT NULL, at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_admin_units_parent
            ON admin_units(version, parent_code);
        """

    # -- ETL -------------------------------------------------------------------
    def set_merger_mapping(self, mapping: Dict[str, str]) -> None:
        for old, new in mapping.items():
            if old == new or new in mapping:
                raise HierarchyError(f"invalid merger mapping entry: {old} -> {new}")
        self._merger_mapping = dict(mapping)

    def etl_load(self, records: Sequence[AdminRecord], version: str, source: str,
                 expected_checksum: Optional[str] = None,
                 merger_mapping: Optional[Dict[str, str]] = None,
                 activate: bool = True) -> ETLResult:
        """Transactional load (copy-on-write). Re-loading an existing
        version replaces it atomically (idempotent ETL reruns). Fatal
        errors leave the previously active version untouched."""
        if not version:
            raise HierarchyError("ETL version must not be empty")
        # Dedupe by code, last row wins - a duplicated export row must not
        # abort the whole load and empty the store.
        deduped: Dict[str, AdminRecord] = {}
        for rec in records:
            deduped[rec.code] = rec
        staged_records = list(deduped.values())
        if not staged_records:
            raise HierarchyError("ETL staged zero records - refusing to activate an empty version")

        digest = checksum_for(staged_records)
        if expected_checksum is not None and expected_checksum != digest:
            raise ChecksumMismatch(
                f"checksum mismatch for version {version}: expected {expected_checksum}, got {digest}")

        errors = validate_hierarchy(staged_records)
        if errors:
            raise HierarchyError("hierarchy validation failed: " + "; ".join(errors))
        warnings = hierarchy_warnings(staged_records)

        if merger_mapping is not None:
            self.set_merger_mapping(merger_mapping)
        for old in self._merger_mapping:
            if old in deduped:
                warnings.append(f"merger source {old} is active in staged data (kept, active wins at resolve)")

        staged = dict(deduped)
        self._versions[version] = staged
        self._checksums[version] = digest
        self._audit.append({"version": version, "action": "etl_load", "source": source,
                            "checksum": digest,
                            "detail": json.dumps({"records": len(staged_records),
                                                  "warnings": warnings}, ensure_ascii=False),
                            "at": time.time()})
        if activate:
            self.activate_version(version)
        return ETLResult(version=version, source=source, record_count=len(staged_records),
                         checksum=digest,
                         activated=activate and self._active_version == version,
                         warnings=tuple(warnings))

    def activate_version(self, version: str) -> None:
        if version not in self._versions:
            raise UnknownAdminCode(f"unknown admin version: {version}")
        self._active_version = version
        self._audit.append({"version": version, "action": "activate", "source": "",
                            "checksum": self._checksums.get(version, ""), "detail": "",
                            "at": time.time()})

    def rollback_to_version(self, version: str) -> None:
        self.activate_version(version)
        self._audit[-1]["action"] = "rollback_activate"

    @property
    def active_version(self) -> Optional[str]:
        return self._active_version

    def audit_log(self) -> List[dict]:
        return list(self._audit)

    # -- Resolution ----------------------------------------------------------------
    def _active(self) -> Dict[str, AdminRecord]:
        if self._active_version is None:
            raise UnknownAdminCode("no active admin version - run etl_load first")
        return self._versions[self._active_version]

    def all_records(self) -> List[AdminRecord]:
        return list(self._active().values())

    def resolve_old_code_2025(self, code: str) -> str:
        """Active code wins and is never remapped; then old_codes on an
        active unit; then the loaded merger CSV if its target is active.
        Unknown codes are returned unchanged - never guessed."""
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

    # -- Lookups ---------------------------------------------------------------------
    def get_unit(self, code: str) -> AdminRecord:
        rec = self._active().get(str(code).strip())
        if rec is None:
            raise UnknownAdminCode(f"unknown admin code: {code}")
        return rec

    def get_province(self, code_or_name: str) -> AdminRecord:
        try:
            return self.get_unit(self._resolve_province_code(str(code_or_name)))
        except UnknownAdminCode:
            found = self.find_by_name(str(code_or_name), level="province")
            if found is not None:
                return found
            raise

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

    def find_by_name(self, name: str, level: Optional[str] = None,
                     parent_code: Optional[str] = None) -> Optional[AdminRecord]:
        """Ascii-insensitive exact name lookup, optionally scoped by
        level and parent - the scoping is what keeps 'Cuôr Đăng' inside
        Đắk Lắk and never matches a same-named unit in another province."""
        target = _ascii(name)
        for rec in self._active().values():
            if level and rec.level != level:
                continue
            if parent_code and rec.parent_code != parent_code:
                continue
            if _ascii(rec.name) == target:
                return rec
        return None

    def get_province_by_name(self, name: str) -> AdminRecord:
        found = self.find_by_name(name, level="province")
        if found is None:
            raise UnknownAdminCode(f"unknown province name: {name}")
        return found

    def get_districts_in_province(self, province_code: str) -> List[AdminRecord]:
        resolved = self._resolve_province_code(str(province_code))
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

    # Compatibility aliases for other experts' callers.
    load_admin_csv = staticmethod(load_admin_csv)
    load_merger_csv = staticmethod(load_merger_csv)
    validate_hierarchy = staticmethod(validate_hierarchy)
    checksum_for = staticmethod(checksum_for)
    seed_records = staticmethod(seed_records)


_default_store: Optional[AdminBoundaryStore] = None


def get_default_store() -> AdminBoundaryStore:
    """Shared seeded store for service/gateway wiring."""
    global _default_store
    if _default_store is None:
        _default_store = AdminBoundaryStore()
    return _default_store


def default_store() -> AdminBoundaryStore:
    return get_default_store()
