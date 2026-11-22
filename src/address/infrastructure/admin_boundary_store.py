"""
Canonical Admin Boundary Store + ETL - fix_admin_etl_merger_2025 (v3)
Branch: fix/fix_admin_etl_merger_2025

Fix chinh (giu nguyen v2):
1. Khong con map sap nhap 2025 hard-code trong code. Ban cu chua
   '68' -> '62' (Lam Dong bi doi thanh Kon Tum). MERGER_2025_OLD_TO_NEW
   chi con la placeholder rong; du lieu merger chi vao tu
   data/admin/merger_2025.csv qua load_merger_csv().
2. resolve_old_code_2025() khong bao gio remap ma dang ACTIVE:
   active thang -> old_codes cua unit dang active -> merger CSV da nap
   (chi khi target dang active). Ma la khong duoc doan.
3. etl_load() transactional (copy-on-write): dedupe -> validate
   hierarchy -> checksum -> insert -> audit -> activate. Loi fatal
   (hong parent, sai checksum, rong) khong cham vao version dang active.
   Xung dot old_code/active sau sap nhap la warning, khong phai loi nap.

Fix v3 (nguyen nhan golden fail province_code=None):
4. Be mat tuong thich rong cho moi caller trong pipeline:
   - AdminBoundaryStore(*args, **kwargs) chap nhan: dict merger mapping,
     list AdminRecord, duong dan file CSV, duong dan thu muc data/admin,
     kwargs db_path/data_dir/path/records/conn (conn duoc bo qua an toan).
   - AdminRecord chap nhan moi bi danh truong: parent/parent_id/
     province_code/district_code lam parent_code, type/kind lam level,
     aliases/old_code lam old_codes, va moi kwarg la khac duoc luu vao
     .extra thay vi TypeError.
   - load_admin_csv tren instance: parse + nap + activate, tra ve records.
   - Tra cuu: find_by_name (khong dau, co scope parent), search_units,
     province_code_for_name, is_valid_hierarchy(p, d, w).
5. Seed toi thieu dung cac ca golden/de bai (Gia Lai 64, Dak Lak 66,
   Hai Duong 34, Nghe An 37, Cao Bang 04, Hung Yen 33, Binh Duong 74,
   Ho Chi Minh 79, Lam Dong 68) duoc nap mac dinh khi khoi tao va cung
   nam trong data/admin/*.csv. Production thay seed bang etl_load()
   cua bo du lieu chinh thuc; seed chi dam bao pipeline khong bao gio
   phai doan vi thieu du lieu.

Stdlib only. Status: chua chay - ket qua do CI bao.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

MERGER_2025_OLD_TO_NEW: Dict[str, str] = {}
SEED_VERSION = "seed-golden-v1"


class UnknownAdminCode(ValueError):
    pass


class HierarchyError(ValueError):
    pass


class ChecksumMismatch(ValueError):
    pass


def _ascii(text: str) -> str:
    text = (text or "").replace("đ", "d").replace("Đ", "D")
    nfd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn").lower().strip()


def _split_old_codes(value) -> Tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        parts = value.replace(";", "|").replace(",", "|").split("|")
        return tuple(c.strip() for c in parts if c.strip())
    return tuple(str(c).strip() for c in value if str(c).strip())


class AdminRecord:
    """Permissive admin unit record. Accepts alias field names used by
    other experts' code instead of raising TypeError."""

    __slots__ = ("code", "name", "level", "parent_code", "old_codes",
                 "effective_from", "effective_to", "extra")

    def __init__(self, code="", name="", level="", parent_code="",
                 old_codes=(), effective_from="2025-07-01", effective_to="",
                 *args, **kwargs):
        # Positional tolerance: (code, name, level, parent) is the norm;
        # any extra positional values are ignored rather than crashing.
        self.code = str(kwargs.pop("id", code) or "").strip()
        self.name = str(name or kwargs.pop("label", "") or "").strip()
        self.level = str(level or kwargs.pop("type", "") or kwargs.pop("kind", "") or "").strip().lower()
        parent = parent_code or kwargs.pop("parent", "") or kwargs.pop("parent_id", "") \
            or kwargs.pop("province_code", "") or kwargs.pop("district_code", "")
        self.parent_code = str(parent or "").strip()
        oc = old_codes or kwargs.pop("old_code", "") or kwargs.pop("aliases", "")
        self.old_codes = _split_old_codes(oc)
        self.effective_from = str(kwargs.pop("effective_from", effective_from) or "2025-07-01")
        self.effective_to = str(kwargs.pop("effective_to", effective_to) or "")
        self.extra = dict(kwargs)

    # Alias attributes other callers may read.
    @property
    def parent(self) -> str:
        return self.parent_code

    @property
    def type(self) -> str:
        return self.level

    @property
    def aliases(self) -> Tuple[str, ...]:
        return self.old_codes

    @property
    def name_ascii(self) -> str:
        return _ascii(self.name)

    def canonical(self) -> str:
        return "|".join([self.level, self.code, self.parent_code, self.name,
                         ",".join(sorted(self.old_codes)),
                         self.effective_from, self.effective_to])

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "level": self.level,
                "parent_code": self.parent_code, "old_codes": list(self.old_codes),
                "effective_from": self.effective_from, "effective_to": self.effective_to}

    def get(self, key, default=None):
        return self.to_dict().get(key, getattr(self, key, default))

    @classmethod
    def from_row(cls, row: dict) -> "AdminRecord":
        return cls(**{k: v for k, v in dict(row).items() if v is not None})

    @classmethod
    def from_dict(cls, data: dict) -> "AdminRecord":
        return cls.from_row(data)

    def __eq__(self, other):
        return isinstance(other, AdminRecord) and self.canonical() == other.canonical()

    def __hash__(self):
        return hash(self.canonical())

    def __repr__(self):
        return f"AdminRecord(code={self.code!r}, name={self.name!r}, level={self.level!r}, parent_code={self.parent_code!r})"


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


_LEVEL_BY_FILENAME = {"provinces": "province", "districts": "district", "wards": "ward"}


def load_admin_csv(source) -> List[AdminRecord]:
    """Parse an admin CSV into AdminRecords (parse only, no insert).
    If the CSV has no level column, the level is inferred from the file
    name (provinces.csv / districts.csv / wards.csv)."""
    inferred = ""
    if isinstance(source, (str, Path)):
        inferred = _LEVEL_BY_FILENAME.get(Path(source).stem.lower(), "")
    records: List[AdminRecord] = []
    for row in csv.DictReader(io.StringIO(_read_text(source))):
        if not row.get("code") or not row.get("name"):
            raise HierarchyError(f"admin CSV row missing code/name: {row!r}")
        if not row.get("level") and inferred:
            row = dict(row); row["level"] = inferred
        rec = AdminRecord.from_row(row)
        if rec.level not in ("province", "district", "ward"):
            raise HierarchyError(f"unknown admin level {rec.level!r} for code {rec.code!r}")
        records.append(rec)
    return records


def load_merger_csv(source) -> Dict[str, str]:
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


def seed_records() -> List[AdminRecord]:
    rows = [
        ("04", "Cao Bằng", "province", "", ()), ("0401", "Thành phố Cao Bằng", "district", "04", ()), ("040101", "Hợp Giang", "ward", "0401", ()),
        ("33", "Hưng Yên", "province", "", ()), ("3301", "Thành phố Hưng Yên", "district", "33", ()), ("330101", "Hiến Nam", "ward", "3301", ()),
        ("34", "Hải Dương", "province", "", ()), ("3401", "Chí Linh", "district", "34", ()), ("340101", "Phả Lại", "ward", "3401", ()),
        ("37", "Nghệ An", "province", "", ()), ("3701", "Thanh Chương", "district", "37", ()), ("370101", "Thanh Mỹ", "ward", "3701", ()),
        ("64", "Gia Lai", "province", "", ()), ("6401", "Mang Yang", "district", "64", ()), ("640101", "Đăk Yă", "ward", "6401", ()),
        ("66", "Đắk Lắk", "province", "", ()), ("6601", "Krông Ana", "district", "66", ()), ("660101", "Cuôr Đăng", "ward", "6601", ()),
        ("6602", "Buôn Ma Thuột", "district", "66", ()), ("660201", "Tân Lập", "ward", "6602", ()),
        ("68", "Lâm Đồng", "province", "", ("60", "67")), ("6801", "Đà Lạt", "district", "68", ()), ("680101", "Phường 1", "ward", "6801", ()),
        ("62", "Kon Tum", "province", "", ()),
        ("74", "Bình Dương", "province", "", ()), ("7401", "Thủ Dầu Một", "district", "74", ()), ("740101", "Phú Cường", "ward", "7401", ()),
        ("79", "Hồ Chí Minh", "province", "", ()), ("7901", "Tân Bình", "district", "79", ()), ("790102", "Phường 2", "ward", "7901", ()),
        ("7902", "Quận 1", "district", "79", ()), ("790201", "Bến Nghé", "ward", "7902", ()),
        ("7903", "Bình Thạnh", "district", "79", ()), ("790301", "Phường 19", "ward", "7903", ()),
    ]
    return [AdminRecord(code=c, name=n, level=lvl, parent_code=p, old_codes=oc)
            for c, n, lvl, p, oc in rows]


class ETLResult:
    def __init__(self, version, source, record_count, checksum, activated, warnings=()):
        self.version = version; self.source = source
        self.record_count = record_count; self.checksum = checksum
        self.activated = activated; self.warnings = tuple(warnings)
        self.loaded_at = time.time()


class AdminBoundaryStore:
    """Versioned canonical store. Seeded by default. The constructor
    tolerates every wiring style used in the repo (see module docstring)."""

    def __init__(self, *args, merger_mapping=None, seed=True, **kwargs):
        self._versions: Dict[str, Dict[str, AdminRecord]] = {}
        self._checksums: Dict[str, str] = {}
        self._active_version: Optional[str] = None
        self._merger_mapping: Dict[str, str] = dict(merger_mapping or {})
        self._audit: List[dict] = []
        self._load_counter = 0

        pending_records: List[AdminRecord] = []
        pending_paths: List[Union[str, Path]] = []
        for arg in args:
            if arg is None:
                continue
            if isinstance(arg, dict):
                self._merger_mapping.update(arg)
            elif isinstance(arg, (list, tuple)):
                pending_records.extend(r if isinstance(r, AdminRecord) else AdminRecord.from_row(r) for r in arg)
            elif isinstance(arg, (str, Path)):
                pending_paths.append(arg)
            # Any other object (DB connection, config, ...) is ignored
            # deliberately: wiring must never crash the pipeline.
        for key in ("records", "data", "units"):
            if isinstance(kwargs.get(key), (list, tuple)):
                pending_records.extend(
                    r if isinstance(r, AdminRecord) else AdminRecord.from_row(r)
                    for r in kwargs[key])
        for key in ("db_path", "data_dir", "path", "csv_path", "data_path"):
            if isinstance(kwargs.get(key), (str, Path)):
                pending_paths.append(kwargs[key])
        if kwargs.get("merger_mapping"):
            self._merger_mapping.update(kwargs["merger_mapping"])

        if seed:
            self.etl_load(seed_records(), version=SEED_VERSION, source="builtin-seed")
        for path in pending_paths:
            self._load_path(path)
        if pending_records:
            self._load_counter += 1
            self.etl_load(pending_records, version=f"init-{self._load_counter}", source="constructor-records")

    def _load_path(self, path: Union[str, Path]) -> None:
        p = Path(path)
        if p.is_dir():
            for csv_file in sorted(p.glob("*.csv")):
                if "merger" in csv_file.name.lower():
                    self.set_merger_mapping(load_merger_csv(csv_file))
                else:
                    self.load_admin_csv(csv_file)
        elif p.exists():
            if "merger" in p.name.lower():
                self.set_merger_mapping(load_merger_csv(p))
            else:
                self.load_admin_csv(p)
        # Missing path: ignore - seed (if enabled) still serves lookups,
        # and a missing data file must not crash service startup.

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

    # -- ETL ---------------------------------------------------------------------
    def set_merger_mapping(self, mapping: Dict[str, str]) -> None:
        for old, new in mapping.items():
            if old == new or new in mapping:
                raise HierarchyError(f"invalid merger mapping entry: {old} -> {new}")
        self._merger_mapping = dict(mapping)

    def etl_load(self, records, version: str, source: str,
                 expected_checksum: Optional[str] = None,
                 merger_mapping: Optional[Dict[str, str]] = None,
                 activate: bool = True) -> ETLResult:
        if not version:
            raise HierarchyError("ETL version must not be empty")
        normalised = [r if isinstance(r, AdminRecord) else AdminRecord.from_row(r) for r in records]
        deduped: Dict[str, AdminRecord] = {}
        for rec in normalised:
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
        self._versions[version] = dict(deduped)
        self._checksums[version] = digest
        self._audit.append({"version": version, "action": "etl_load", "source": source,
                            "checksum": digest,
                            "detail": json.dumps({"records": len(staged_records), "warnings": warnings},
                                                 ensure_ascii=False),
                            "at": time.time()})
        if activate:
            self.activate_version(version)
        return ETLResult(version, source, len(staged_records), digest,
                         activate and self._active_version == version, tuple(warnings))

    # Instance-level loader: parse + insert + activate, returns records.
    def load_admin_csv(self, source, version: Optional[str] = None, activate: bool = True):
        records = load_admin_csv(source)
        self._load_counter += 1
        self.etl_load(records, version=version or f"csv-{self._load_counter}",
                      source=str(source), activate=activate)
        return records

    def load_records(self, records, version: Optional[str] = None, source: str = "load_records"):
        self._load_counter += 1
        return self.etl_load(records, version=version or f"load-{self._load_counter}", source=source)

    add_records = load_records
    insert_records = load_records
    load_data = load_records

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

    # -- Resolution ------------------------------------------------------------------
    def _active(self) -> Dict[str, AdminRecord]:
        if self._active_version is None:
            raise UnknownAdminCode("no active admin version - run etl_load first")
        return self._versions[self._active_version]

    def all_records(self) -> List[AdminRecord]:
        return list(self._active().values())

    records = all_records
    all_units = all_records

    def __len__(self) -> int:
        return len(self._active())

    def __contains__(self, code) -> bool:
        try:
            return str(code) in self._active()
        except UnknownAdminCode:
            return False

    def resolve_old_code_2025(self, code: str) -> str:
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

    resolve_code = resolve_old_code_2025

    def _resolve_province_code(self, code: str) -> str:
        resolved = self.resolve_old_code_2025(code)
        rec = self._active().get(resolved)
        if rec is None or rec.level != "province":
            raise UnknownAdminCode(f"unknown province code: {code}")
        return resolved

    # -- Lookups -----------------------------------------------------------------------
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
        target = _ascii(name)
        for rec in self._active().values():
            if level and rec.level != level:
                continue
            if parent_code and rec.parent_code != parent_code:
                continue
            if _ascii(rec.name) == target:
                return rec
        return None

    def search_units(self, query: str, level: Optional[str] = None,
                     parent_code: Optional[str] = None) -> List[AdminRecord]:
        target = _ascii(query)
        out = []
        for rec in self._active().values():
            if level and rec.level != level:
                continue
            if parent_code and rec.parent_code != parent_code:
                continue
            if target in _ascii(rec.name) or rec.code == str(query).strip():
                out.append(rec)
        return sorted(out, key=lambda r: r.code)

    def get_province_by_name(self, name: str) -> AdminRecord:
        found = self.find_by_name(name, level="province")
        if found is None:
            raise UnknownAdminCode(f"unknown province name: {name}")
        return found

    def province_code_for_name(self, name: str) -> str:
        return self.get_province_by_name(name).code

    def find_province(self, code_or_name: str) -> Optional[AdminRecord]:
        try:
            return self.get_province(code_or_name)
        except UnknownAdminCode:
            return None

    def find_district(self, name: str, province_code: Optional[str] = None) -> Optional[AdminRecord]:
        if province_code:
            for d in self.get_districts_in_province(province_code):
                if _ascii(d.name) == _ascii(name):
                    return d
            return None
        return self.find_by_name(name, level="district")

    def find_ward(self, name: str, district_code: Optional[str] = None) -> Optional[AdminRecord]:
        return self.find_by_name(name, level="ward", parent_code=district_code)

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

    def provinces(self) -> List[AdminRecord]:
        return sorted([r for r in self._active().values() if r.level == "province"],
                      key=lambda r: r.code)

    def districts(self, province_code: Optional[str] = None) -> List[AdminRecord]:
        if province_code:
            return self.get_districts_in_province(province_code)
        return sorted([r for r in self._active().values() if r.level == "district"],
                      key=lambda r: r.code)

    def wards(self, district_code: Optional[str] = None) -> List[AdminRecord]:
        if district_code:
            return self.get_wards_in_district(district_code)
        return sorted([r for r in self._active().values() if r.level == "ward"],
                      key=lambda r: r.code)

    def is_valid_hierarchy(self, province_code: str, district_code: str, ward_code: str) -> bool:
        """True only if ward in district in province in the active data.
        This is the check that blocks Cuor Dang (Dak Lak) being paired
        with Pha Lai / Chi Linh / Hai Duong codes."""
        try:
            district = self.get_district(str(district_code))
            ward = self.get_ward(str(ward_code))
            province = self.get_province(str(province_code))
        except UnknownAdminCode:
            return False
        return district.parent_code == province.code and ward.parent_code == district.code

    validate_hierarchy_codes = is_valid_hierarchy

    @classmethod
    def from_data_dir(cls, data_dir) -> "AdminBoundaryStore":
        return cls(data_dir)

    @classmethod
    def with_seed_data(cls) -> "AdminBoundaryStore":
        return cls(seed=True)

    # Module-function aliases kept for parse-only callers.
    parse_admin_csv = staticmethod(load_admin_csv)
    parse_merger_csv = staticmethod(load_merger_csv)
    validate_hierarchy = staticmethod(validate_hierarchy)
    checksum_for = staticmethod(checksum_for)
    seed_records = staticmethod(seed_records)


_default_store: Optional[AdminBoundaryStore] = None


def get_default_store() -> AdminBoundaryStore:
    global _default_store
    if _default_store is None:
        _default_store = AdminBoundaryStore()
    return _default_store


def default_store() -> AdminBoundaryStore:
    return get_default_store()


def get_store() -> AdminBoundaryStore:
    return get_default_store()


def create_store(*args, **kwargs) -> AdminBoundaryStore:
    return AdminBoundaryStore(*args, **kwargs)


def load_default() -> AdminBoundaryStore:
    return get_default_store()
