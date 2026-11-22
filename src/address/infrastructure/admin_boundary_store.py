"""
Canonical Admin DB — approach_02.

Nguồn sự thật duy nhất cho Tỉnh/Huyện/Xã sau sáp nhập 2025.
Nguyên tắc:
  - Không có địa chỉ mặc định. Không bao giờ trả về Xã Đăk Yă khi không đọc được.
  - Mọi tra cứu đi qua DB này. Goong / Pancake / GHN / SPX chỉ là provider gợi ý.
  - Ward thuộc District, District thuộc Province — enforced bằng FK + CHECK ở DB,
    không chỉ ở code.
  - Versioned: mỗi đợt ETL tạo version mới, chỉ activate khi validate xong,
    rollback được về version trước.
  - Cache Redis theo tỉnh/huyện/xã, mục tiêu < 10ms cho read nóng.

Pipeline: Normalize -> Parse -> Anchor tỉnh -> Match trong phạm vi đã khóa
-> Multi-provider Geocode -> Rank -> Validate phân cấp -> Confidence Gate
-> Commit hoặc NEEDS_REVIEW hoặc REJECTED.
File này chỉ lo tầng infrastructure: DDL, ETL, store, cache, version/rollback.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Iterable, Optional, Protocol, Sequence

__all__ = [
    "AdminLevel",
    "AdminRecord",
    "AdminVersion",
    "HierarchyViolation",
    "UnknownAdminCode",
    "AdminBoundaryStore",
    "build_ddl",
    "normalize_admin_name",
    "strip_diacritics",
    "resolve_old_code_2025",
    "MERGER_2025_OLD_TO_NEW",
    "load_admin_csv",
]


# ---------------------------------------------------------------------------
# Domain types (thuần dữ liệu, không phụ thuộc provider)
# ---------------------------------------------------------------------------

class AdminLevel(str, Enum):
    PROVINCE = "province"
    DISTRICT = "district"
    WARD = "ward"


class HierarchyViolation(ValueError):
    """Ward không thuộc District, hoặc District không thuộc Province."""


class UnknownAdminCode(ValueError):
    """Mã hành chính không tồn tại trong version đang active."""


@dataclass(frozen=True)
class AdminRecord:
    code: str                    # mã hành chính GSO, vd: 66 = Đắk Lắk (sau 2025)
    name: str                    # tên có dấu, vd: "Cuôr Đăng"
    name_unsigned: str           # tên không dấu, vd: "Cuor Dang"
    level: AdminLevel
    parent_code: Optional[str]   # ward -> district, district -> province, province -> None
    province_code: str
    district_code: Optional[str] = None
    aliases: tuple[str, ...] = ()
    old_codes: tuple[str, ...] = ()   # mã cũ trước sáp nhập 2025
    old_names: tuple[str, ...] = ()
    centroid_lat: Optional[float] = None
    centroid_lng: Optional[float] = None
    carrier_codes: dict[str, str] = field(default_factory=dict)  # {"ghn":..,"ghtk":..,"spx":..,"goong":..}
    version: int = 0
    effective_from: Optional[date] = None
    source: str = ""


@dataclass(frozen=True)
class AdminVersion:
    version: int
    effective_from: date
    source: str
    record_count: int
    checksum: str
    is_active: bool
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# Sáp nhập 2025 — bảng mapping CŨ -> MỚI (rút gọn, nguồn GSO; ETL đầy đủ
# nạp từ file CSV chính thức, không hard-code toàn bộ danh mục ở đây).
# Ví dụ: Phú Yên (54) sáp nhập vào Đắk Lắk (66); Bình Định (52) vào Gia Lai (64).
MERGER_2025_OLD_TO_NEW: dict[str, str] = {
    "54": "66",  # Phú Yên -> Đắk Lắk
    "52": "64",  # Bình Định -> Gia Lai
    "68": "62",  # Lâm Đồng giữ mã, sáp nhập Đắk Nông + Bình Thuận (tham chiếu ETL)
    "35": "37",  # Hà Nam -> Ninh Bình (ví dụ tra cứu, ETL là nguồn chuẩn)
}


def resolve_old_code_2025(old_code: str) -> str:
    """Trả mã mới sau sáp nhập 2025, nếu không sáp nhập thì giữ nguyên."""
    return MERGER_2025_OLD_TO_NEW.get(old_code.strip(), old_code.strip())


def strip_diacritics(text: str) -> str:
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    # Đ, đ không phân tách được bằng NFD
    return stripped.replace("Đ", "D").replace("đ", "d")


def normalize_admin_name(text: str) -> str:
    """Chuẩn hóa để khớp: lower, bỏ dấu, gộp khoảng trắng, bỏ tiền tố hành chính."""
    t = strip_diacritics(text or "").lower().strip()
    t = re.sub(r"^(tinh|thanh pho|quan|huyen|xa|phuong|thi tran|tp\.?|tx\.?)\s+", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


# ---------------------------------------------------------------------------
# DDL — PostgreSQL + PostGIS, FK + CHECK là tuyến phòng thủ cuối cùng
# ---------------------------------------------------------------------------

def build_ddl() -> str:
    return """
-- Canonical Admin DB — versioned, PostGIS, fail-closed
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS admin_versions (
  version        INT PRIMARY KEY,
  effective_from DATE NOT NULL,
  source         TEXT NOT NULL,
  record_count   INT NOT NULL CHECK (record_count > 0),
  checksum       TEXT NOT NULL,
  is_active      BOOLEAN NOT NULL DEFAULT FALSE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Chỉ một version active tại một thời điểm
CREATE UNIQUE INDEX IF NOT EXISTS uq_admin_versions_one_active
  ON admin_versions (is_active) WHERE is_active;

CREATE TABLE IF NOT EXISTS provinces (
  code          TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  name_unsigned TEXT NOT NULL,
  version       INT NOT NULL REFERENCES admin_versions(version),
  effective_from DATE NOT NULL,
  source        TEXT NOT NULL,
  centroid      GEOMETRY(Point, 4326)
);
CREATE TABLE IF NOT EXISTS districts (
  code          TEXT PRIMARY KEY,
  province_code TEXT NOT NULL REFERENCES provinces(code) ON DELETE RESTRICT,
  name          TEXT NOT NULL,
  name_unsigned TEXT NOT NULL,
  version       INT NOT NULL REFERENCES admin_versions(version),
  effective_from DATE NOT NULL,
  source        TEXT NOT NULL,
  centroid      GEOMETRY(Point, 4326),
  CHECK (province_code <> code)
);
CREATE TABLE IF NOT EXISTS wards (
  code          TEXT PRIMARY KEY,
  district_code TEXT NOT NULL REFERENCES districts(code) ON DELETE RESTRICT,
  province_code TEXT NOT NULL REFERENCES provinces(code) ON DELETE RESTRICT,
  name          TEXT NOT NULL,
  name_unsigned TEXT NOT NULL,
  version       INT NOT NULL REFERENCES admin_versions(version),
  effective_from DATE NOT NULL,
  source        TEXT NOT NULL,
  centroid      GEOMETRY(Point, 4326),
  -- Cấm tổ hợp chéo: ward.province_code phải khớp district.province_code.
  -- Enforced bằng trigger validate_ward_hierarchy() bên dưới, vì CHECK
  -- liên bảng không làm được trong PostgreSQL.
  CHECK (district_code <> code AND province_code <> code)
);
CREATE INDEX IF NOT EXISTS idx_wards_district ON wards (district_code);
CREATE INDEX IF NOT EXISTS idx_wards_province ON wards (province_code);
CREATE INDEX IF NOT EXISTS idx_districts_province ON districts (province_code);
CREATE INDEX IF NOT EXISTS idx_wards_name_trgm ON wards USING gin (name_unsigned gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_districts_name_trgm ON districts USING gin (name_unsigned gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_provinces_name_trgm ON provinces USING gin (name_unsigned gin_trgm_ops);

CREATE TABLE IF NOT EXISTS admin_aliases (
  id            BIGSERIAL PRIMARY KEY,
  admin_code    TEXT NOT NULL,
  admin_level   TEXT NOT NULL CHECK (admin_level IN ('province','district','ward')),
  alias         TEXT NOT NULL,
  alias_unsigned TEXT NOT NULL,
  alias_type    TEXT NOT NULL CHECK (alias_type IN ('old_name','local','abbrev','unsigned','carrier')),
  version       INT NOT NULL REFERENCES admin_versions(version),
  UNIQUE (admin_code, alias_unsigned, alias_type)
);
CREATE INDEX IF NOT EXISTS idx_alias_unsigned ON admin_aliases (alias_unsigned);

CREATE TABLE IF NOT EXISTS carrier_codes (
  admin_code TEXT NOT NULL,
  provider   TEXT NOT NULL CHECK (provider IN ('ghn','ghtk','spx','goong','postal')),
  ext_code   TEXT NOT NULL,
  version    INT NOT NULL REFERENCES admin_versions(version),
  PRIMARY KEY (admin_code, provider)
);

CREATE OR REPLACE FUNCTION validate_ward_hierarchy() RETURNS trigger AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM districts d
    WHERE d.code = NEW.district_code AND d.province_code = NEW.province_code
  ) THEN
    RAISE EXCEPTION 'hierarchy violation: ward % district % not in province %',
      NEW.code, NEW.district_code, NEW.province_code;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_ward_hierarchy ON wards;
CREATE TRIGGER trg_ward_hierarchy
  BEFORE INSERT OR UPDATE ON wards
  FOR EACH ROW EXECUTE FUNCTION validate_ward_hierarchy();

CREATE TABLE IF NOT EXISTS admin_audit (
  id BIGSERIAL PRIMARY KEY, version INT NOT NULL, action TEXT NOT NULL,
  detail JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- KHÔNG có bảng default_address, KHÔNG có row fallback Đăk Yă.
-- Mọi lookup miss đều trả về None / raise, service phải chuyển NEEDS_REVIEW.
"""


# ---------------------------------------------------------------------------
# ETL — staging -> validate -> activate (transaction), rollback = re-activate
# ---------------------------------------------------------------------------

def load_admin_csv(path: str, level: AdminLevel) -> list[AdminRecord]:
    """Đọc CSV chính thức (GSO/GHN/SPX/bưu chính). Cột tối thiểu:
    code,name,parent_code,province_code,district_code,centroid_lat,centroid_lng,old_codes,old_names,aliases
    """
    records: list[AdminRecord] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("code") or "").strip()
            if not code:
                continue
            name = (row.get("name") or "").strip()
            records.append(AdminRecord(
                code=code, name=name, name_unsigned=normalize_admin_name(name),
                level=level, parent_code=(row.get("parent_code") or None),
                province_code=(row.get("province_code") or code).strip(),
                district_code=(row.get("district_code") or None),
                aliases=tuple(a.strip() for a in (row.get("aliases") or "").split(";") if a.strip()),
                old_codes=tuple(c.strip() for c in (row.get("old_codes") or "").split(";") if c.strip()),
                old_names=tuple(n.strip() for n in (row.get("old_names") or "").split(";") if n.strip()),
                centroid_lat=float(row["centroid_lat"]) if row.get("centroid_lat") else None,
                centroid_lng=float(row["centroid_lng"]) if row.get("centroid_lng") else None,
                source=row.get("source") or "gso",
            ))
    return records


class Cache(Protocol):
    def get(self, key: str) -> Optional[str]: ...
    def set(self, key: str, value: str, ex: int = 300) -> None: ...
    def delete(self, *keys: str) -> None: ...


class DB(Protocol):
    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any: ...
    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]: ...
    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict]: ...


class AdminBoundaryStore:
    """Store duy nhất được phép trả lời: tỉnh/huyện/xã này có tồn tại không,
    và nó thuộc cấp cha nào — trong version đang active."""

    CACHE_TTL_SECONDS = 3600  # read nóng; invalidate khi activate version mới

    def __init__(self, db: DB, cache: Optional[Cache] = None, version: Optional[int] = None):
        self.db = db
        self.cache = cache
        self._version = version

    # -- version ---------------------------------------------------------
    def active_version(self) -> int:
        if self._version is not None:
            return self._version
        row = self.db.fetchone(
            "SELECT version FROM admin_versions WHERE is_active ORDER BY version DESC LIMIT 1")
        if not row:
            raise UnknownAdminCode("no active admin version — refuse to guess")
        self._version = int(row["version"])
        return self._version

    def activate_version(self, version: int) -> None:
        """Chỉ activate sau khi ETL validate xong; invalidate toàn bộ cache."""
        self.db.execute("UPDATE admin_versions SET is_active=FALSE WHERE is_active")
        self.db.execute("UPDATE admin_versions SET is_active=TRUE WHERE version=%s", (version,))
        self.db.execute(
            "INSERT INTO admin_audit(version, action, detail) VALUES (%s,'activate',%s)",
            (version, json.dumps({"by": "etl"})))
        if self.cache:
            # Cache key luôn gắn version nên activate mới tự miss; xóa để chắc chắn.
            self.cache.delete("admin:active_version")
        self._version = version

    def rollback_to_version(self, version: int) -> None:
        """Rollback = activate lại version cũ đã validate. Không xóa dữ liệu."""
        self.activate_version(version)

    # -- cache helper ----------------------------------------------------
    def _cache_get(self, key: str) -> Optional[Any]:
        if not self.cache:
            return None
        raw = self.cache.get(f"admin:v{self.active_version()}:{key}")
        return json.loads(raw) if raw else None

    def _cache_set(self, key: str, value: Any) -> None:
        if self.cache:
            self.cache.set(f"admin:v{self.active_version()}:{key}",
                           json.dumps(value, ensure_ascii=False), ex=self.CACHE_TTL_SECONDS)

    # -- lookups (mọi tra cứu đi qua đây) --------------------------------
    def get_province(self, code: str) -> Optional[dict]:
        code = resolve_old_code_2025(code)
        if hit := self._cache_get(f"province:{code}"):
            return hit
        row = self.db.fetchone(
            "SELECT code,name,name_unsigned FROM provinces WHERE code=%s AND version=%s",
            (code, self.active_version()))
        if row:
            self._cache_set(f"province:{code}", row)
        return row

    def get_districts_in_province(self, province_code: str) -> list[dict]:
        province_code = resolve_old_code_2025(province_code)
        if hit := self._cache_get(f"districts:{province_code}"):
            return hit
        rows = self.db.fetchall(
            "SELECT code,name,name_unsigned FROM districts "
            "WHERE province_code=%s AND version=%s ORDER BY name",
            (province_code, self.active_version()))
        self._cache_set(f"districts:{province_code}", rows)
        return rows

    def get_wards_in_district(self, district_code: str) -> list[dict]:
        if hit := self._cache_get(f"wards:{district_code}"):
            return hit
        rows = self.db.fetchall(
            "SELECT code,name,name_unsigned,province_code FROM wards "
            "WHERE district_code=%s AND version=%s ORDER BY name",
            (district_code, self.active_version()))
        self._cache_set(f"wards:{district_code}", rows)
        return rows

    def get_wards_in_province(self, province_code: str) -> list[dict]:
        """Dùng cho fuzzy matcher: chỉ quét trong tỉnh đã neo, không quét toàn quốc."""
        province_code = resolve_old_code_2025(province_code)
        if hit := self._cache_get(f"wards_province:{province_code}"):
            return hit
        rows = self.db.fetchall(
            "SELECT code,name,name_unsigned,district_code FROM wards "
            "WHERE province_code=%s AND version=%s ORDER BY name",
            (province_code, self.active_version()))
        self._cache_set(f"wards_province:{province_code}", rows)
        return rows

    def find_by_alias(self, unsigned_name: str, level: AdminLevel) -> list[dict]:
        table = {"province": "provinces", "district": "districts", "ward": "wards"}[level.value]
        return self.db.fetchall(
            f"SELECT code,name,name_unsigned FROM {table} "
            "WHERE name_unsigned=%s AND version=%s",
            (normalize_admin_name(unsigned_name), self.active_version()))

    def validate_hierarchy(self, province_code: str, district_code: Optional[str],
                           ward_code: Optional[str]) -> bool:
        """True chỉ khi ward thuộc district và district thuộc province.
        Sai -> HierarchyViolation, caller phải NEEDS_REVIEW, không tự sửa tỉnh."""
        if district_code:
            row = self.db.fetchone(
                "SELECT 1 FROM districts WHERE code=%s AND province_code=%s AND version=%s",
                (district_code, resolve_old_code_2025(province_code), self.active_version()))
            if not row:
                raise HierarchyViolation(f"district {district_code} not in province {province_code}")
        if ward_code:
            if not district_code:
                raise HierarchyViolation("ward without district cannot be validated")
            row = self.db.fetchone(
                "SELECT 1 FROM wards WHERE code=%s AND district_code=%s AND version=%s",
                (ward_code, district_code, self.active_version()))
            if not row:
                raise HierarchyViolation(f"ward {ward_code} not in district {district_code}")
        return True

    def checksum(self, records: Iterable[AdminRecord]) -> str:
        payload = "\n".join(sorted(f"{r.level.value}|{r.code}|{r.name}|{r.parent_code or ''}"
                                   for r in records))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
