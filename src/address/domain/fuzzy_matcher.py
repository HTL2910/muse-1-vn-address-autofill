"""Fuzzy matcher — veto qua parent_code, fallback tra tên parse.

Fix fix_fuzzy_exact_match_ignores_district + fix cho golden_11:
1. Exact-match phải qua cùng veto tỉnh và huyện như nhánh thường.
   Veto duy nhất, dùng chung cho exact-match và fuzzy fallback, chỉ
   bằng parent_code (không kiểm tra level — đường mentions gốc không
   kiểm tra level, validator cũng chỉ kiểm tra parent_code):
     - ward phải có parent_code là một district tồn tại trong index;
     - district đó phải thuộc tỉnh đã neo;
     - nếu scope đã khóa huyện, ward.parent_code phải đúng huyện đó.
     - ward có parent_code=None bị veto.
   Giữ nguyên thứ tự gốc và khóa huyện bằng district_units[0]; chống
   trùng ứng viên theo ward_code để margin không về 0.
2. Truyền đúng thông tin đã parse vào AddressContract — đây là nguyên
   nhân golden_11 fail dù mã đúng 79/760/76002: bản gốc tạo contract
   mà không truyền street / house_number / confidence, nên
   AddressContract.confidence mặc định 0.0 và has_house_or_street luôn
   False. Validator luôn cộng warning MISSING_HOUSE_OR_STREET + penalty
   0.15, gate hạ VERIFIED thành NEEDS_REVIEW cho mọi địa chỉ đúng.
   Sau fix, cả hai nhánh exact và fallback đều truyền
   street / house_number / hamlet từ ParsedAddress (nếu có) và
   confidence = score (1.0 cho exact, difflib score cho fallback).
3. Fallback không khớp mờ cho tên xã quá ngắn (<4 ký tự sau khi bỏ
   khoảng trắng): difflib trên chuỗi ngắn tạo ứng viên giả cho các
   input mơ hồ như 'bi' / 'Xom' — exact-match đã xử lý tên ngắn thật
   sự, fallback không được tự đoán thêm.
"""
from __future__ import annotations
import difflib
from dataclasses import dataclass
from typing import Optional
from .province_anchor import AdminIndex, AnchorResult, AdminUnit, extract_admin_mentions
from .schemas import AddressCandidate, AddressContract, ProviderSource, normalize_text
from .structured_parser import ParsedAddress


@dataclass
class Scope:
    province_code: str; district_code: Optional[str]=None


def _district_in_scope(district: AdminUnit, scope: Scope) -> bool:
    """Veto chung cho district, chỉ bằng parent_code, không kiểm tra level."""
    if district.parent_code != scope.province_code:
        return False
    if scope.district_code and district.code != scope.district_code:
        return False
    return True


def _ward_in_scope(ward: AdminUnit, scope: Scope, index: AdminIndex) -> bool:
    """Veto chung cho ward, dùng cho cả exact-match và fuzzy fallback."""
    if not ward.parent_code:
        return False
    district = index.units.get(ward.parent_code)
    if district is None:
        return False
    if district.parent_code != scope.province_code:
        return False
    if scope.district_code and ward.parent_code != scope.district_code:
        return False
    return True


def _dedupe_by_code(units):
    seen = set()
    out = []
    for u in units:
        if u.code not in seen:
            seen.add(u.code)
            out.append(u)
    return out


def _parsed_field(parsed, *names):
    if parsed is None:
        return None
    if isinstance(parsed, dict):
        for n in names:
            value = parsed.get(n)
            if value not in (None, ""):
                return value
        return None
    for n in names:
        value = getattr(parsed, n, None)
        if value not in (None, ""):
            return value
    return None


class FuzzyMatcher:
    def match(self, parsed: ParsedAddress, anchor: AnchorResult, index: AdminIndex) -> list[AddressCandidate]:
        if not anchor.anchored or anchor.province is None: return []
        scope=Scope(province_code=anchor.province.code)
        mentions=extract_admin_mentions(parsed.raw_input,index)
        ward_units=[u for u in mentions.get("ward",[])]
        district_units=[u for u in mentions.get("district",[]) if u.parent_code==scope.province_code]
        filtered=[]
        for ward in ward_units:
            if _ward_in_scope(ward, scope, index):
                filtered.append(ward)
        ward_units=_dedupe_by_code(filtered)
        if district_units and not scope.district_code: scope.district_code=district_units[0].code
        if not ward_units and parsed.ward_name:
            target=normalize_text(parsed.ward_name)
            for cand in index.inverted.get(target,[]):
                if _ward_in_scope(cand, scope, index):
                    ward_units.append(cand)
            ward_units=_dedupe_by_code(ward_units)
        if not district_units and parsed.district_name:
            target=normalize_text(parsed.district_name)
            for cand in index.inverted.get(target,[]):
                if cand.parent_code==scope.province_code:
                    district_units.append(cand); scope.district_code=cand.code
        street=_parsed_field(parsed, "street", "street_name")
        house_number=_parsed_field(parsed, "house_number", "house_no", "house")
        hamlet=_parsed_field(parsed, "hamlet")
        candidates: list[AddressCandidate]=[]
        for ward in ward_units:
            if not _ward_in_scope(ward, scope, index):
                continue
            district=index.units.get(ward.parent_code) if ward.parent_code else None
            if district is None or district.parent_code != scope.province_code:
                continue
            contract=AddressContract(raw_input=parsed.raw_input,province=anchor.province.name,district=district.name if district else None,ward=ward.name,province_code=anchor.province.code,district_code=district.code if district else None,ward_code=ward.code,street=street,house_number=house_number,hamlet=hamlet,confidence=1.0,source=ProviderSource.INTERNAL_ADMIN_DB)
            candidates.append(AddressCandidate(address=contract,provider=ProviderSource.INTERNAL_ADMIN_DB,provider_score=1.0,rank_score=1.0))
        if candidates: return candidates
        target=normalize_text(parsed.ward_name) if parsed.ward_name else ""
        if not target: return []
        # Tên xã quá ngắn thì không khớp mờ: difflib trên 2-3 ký tự tạo
        # ứng viên giả cho 'bi' / 'Xom'. Exact-match ở trên đã xử lý
        # tên ngắn thật sự (nếu có xã đó trong DB).
        if len(target.replace(" ", "")) < 4:
            return []
        best=[]
        for district in index.districts_of(scope.province_code):
            if scope.district_code and district.code!=scope.district_code: continue
            for ward in index.wards_of(district.code):
                if not _ward_in_scope(ward, scope, index):
                    continue
                s=difflib.SequenceMatcher(None,target,ward.normalized_name).ratio()
                if s>=0.85: best.append((s,ward))
        best.sort(key=lambda x:x[0],reverse=True)
        for score,ward in best[:3]:
            district=index.units.get(ward.parent_code) if ward.parent_code else None
            if district is None or district.parent_code != scope.province_code:
                continue
            contract=AddressContract(raw_input=parsed.raw_input,province=anchor.province.name,district=district.name if district else None,ward=ward.name,province_code=anchor.province.code,district_code=district.code if district else None,ward_code=ward.code,street=street,house_number=house_number,hamlet=hamlet,confidence=float(score),source=ProviderSource.INTERNAL_ADMIN_DB)
            candidates.append(AddressCandidate(address=contract,provider=ProviderSource.INTERNAL_ADMIN_DB,provider_score=score,rank_score=score))
        return candidates
