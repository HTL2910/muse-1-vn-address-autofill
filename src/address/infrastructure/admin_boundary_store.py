"""Canonical Admin DB — nguồn sự thật duy nhất.
Fix: không còn map hard-code 68->62 (Lâm Đồng giữ 68, Kon Tum là 62);
chỉ map old_code qua merger_2025.csv khi old_code không còn active.
ETL thực sự insert vào store, không chỉ DDL/lookup.
"""
from __future__ import annotations
import csv, hashlib, json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
try:
    from ..domain.province_anchor import AdminIndex, AdminUnit
except Exception:  # pragma: no cover
    AdminIndex=None; AdminUnit=None  # type: ignore
@dataclass
class AdminRecord:
    code: str; name: str; level: str; parent_code: Optional[str]=None; aliases: tuple[str,...]=()
    centroid_lat: Optional[float]=None; centroid_lng: Optional[float]=None
@dataclass
class AdminBoundaryStore:
    units: dict[str, AdminRecord]=field(default_factory=dict)
    versions: dict[str, list[str]]=field(default_factory=dict)
    active_version: Optional[str]=None
    merger_old_to_new: dict[str, str]=field(default_factory=dict)
    def load_merger_csv(self, path) -> None:
        p=Path(path)
        if not p.is_file(): return
        with p.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                old=str(row.get("old_code") or row.get("code_old") or "").strip()
                new=str(row.get("new_code") or row.get("code_new") or "").strip()
                if old and new and old!=new: self.merger_old_to_new[old]=new
    def resolve_old_code_2025(self, code: str) -> str:
        # Chỉ map khi code cũ không còn active; không bao giờ remap mã active.
        if code in self.units: return code
        return self.merger_old_to_new.get(str(code), str(code))
    def load_admin_csv(self, path) -> list[AdminRecord]:
        p=Path(path); out=[]
        if not p.is_file(): return out
        with p.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                code=str(row.get("code") or "").strip()
                if not code: continue
                parent=str(row.get("parent_code") or row.get("province_code") or row.get("district_code") or "").strip() or None
                # provinces.csv/districts.csv/wards.csv dùng cột parent khác nhau:
                level=str(row.get("level") or "").strip()
                if not level:
                    level="province" if len(code)==2 else ("district" if len(code)==3 else "ward")
                out.append(AdminRecord(code=code,name=str(row.get("name") or ""),level=level,parent_code=parent,aliases=tuple(str(row.get("aliases") or "").split("|")) if row.get("aliases") else ()))
        return out
    def etl_load(self, records, version: str, source: str="csv") -> str:
        # Staging -> validate hierarchy (parent phải tồn tại, trừ tỉnh) -> insert -> activate.
        staged=list(records)
        codes={r.code for r in staged}
        for r in staged:
            if r.parent_code and r.parent_code not in codes and r.parent_code not in self.units:
                raise ValueError(f"Hierarchy broken: {r.code} parent {r.parent_code} missing")
        for r in staged: self.units[r.code]=r
        self.versions[version]=[r.code for r in staged]
        self.active_version=version
        return version
    def activate_version(self, version: str) -> None:
        if version not in self.versions: raise ValueError(f"Unknown version {version}")
        self.active_version=version
    def rollback_to_version(self, version: str) -> None: self.activate_version(version)
    def get_province(self, code: str):
        code=self.resolve_old_code_2025(code); rec=self.units.get(code)
        return rec if rec and rec.level=="province" else None
    def get_districts_in_province(self, province_code: str):
        province_code=self.resolve_old_code_2025(province_code)
        return [r for r in self.units.values() if r.level=="district" and r.parent_code==province_code]
    def get_wards_in_province(self, province_code: str):
        province_code=self.resolve_old_code_2025(province_code)
        districts={r.code for r in self.get_districts_in_province(province_code)}
        return [r for r in self.units.values() if r.level=="ward" and r.parent_code in districts]
    def get_wards_in_district(self, district_code: str):
        return [r for r in self.units.values() if r.level=="ward" and r.parent_code==district_code]
    def to_index(self):
        if AdminIndex is None: return None
        units=[AdminUnit(code=r.code,name=r.name,level=r.level,parent_code=r.parent_code,aliases=r.aliases,centroid_lat=r.centroid_lat,centroid_lng=r.centroid_lng) for r in self.units.values()]
        return AdminIndex(units)
    def checksum(self, version: str) -> str:
        data=json.dumps(sorted(self.versions.get(version,[])),ensure_ascii=False)
        return hashlib.sha256(data.encode()).hexdigest()
def build_ddl() -> str:
    return "CREATE TABLE IF NOT EXISTS provinces(code TEXT PRIMARY KEY, name TEXT); CREATE TABLE IF NOT EXISTS districts(code TEXT PRIMARY KEY, name TEXT, parent_code TEXT); CREATE TABLE IF NOT EXISTS wards(code TEXT PRIMARY KEY, name TEXT, parent_code TEXT);"
def load_index_from_data_dir(data_dir="data/admin"):
    store=AdminBoundaryStore(); base=Path(data_dir)
    store.load_merger_csv(base/"merger_2025.csv")
    records=[]
    for name in ("admin_units.csv","provinces.csv","districts.csv","wards.csv","admin_units_seed.csv"):
        records.extend(store.load_admin_csv(base/name))
    if records: store.etl_load(records,"v1")
    return store.to_index()
