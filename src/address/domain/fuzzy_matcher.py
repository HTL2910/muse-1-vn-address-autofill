"""Fuzzy matcher — veto qua parent_code, fallback tra tên parse.

Fix fix_fuzzy_exact_match_ignores_district (bản sửa lần 2):
Exact-match phải qua cùng veto tỉnh và huyện như nhánh thường.

Bài học từ lần test fail trước: KHÔNG kiểm tra `level == "ward"/"district"`.
Đường mentions gốc không kiểm tra level, chỉ kiểm tra parent_code; validator
cũng quy định hierarchy chỉ kiểm tra bằng parent_code trong Canonical Admin
DB, không kiểm tra bằng chuỗi. Nếu áp level-check lên mentions, các unit
có level khác chuỗi đó (vd. "phuong"/"quan", Enum, ...) sẽ bị veto oan,
fuzzy trả rỗng hoặc trả nhiều ứng viên, ranker tính margin = 0 nên địa
chỉ đúng hoàn toàn như golden_11 (79/760/76002) vẫn bị hạ NEEDS_REVIEW.

Veto duy nhất, dùng chung cho exact-match và fuzzy fallback:
  - ward phải có parent_code là một district tồn tại trong index;
  - district đó phải thuộc tỉnh đã neo (parent_code == scope.province_code);
  - nếu scope đã khóa huyện, ward.parent_code phải đúng huyện đó.
  - ward có parent_code=None bị veto: không chứng minh được thuộc tỉnh/
    huyện nào thì không được ép province_code của tỉnh đã neo lên nó.

Giữ nguyên hành vi gốc còn lại để không phá các ca đã pass:
  - giữ nguyên thứ tự: mentions -> ward inverted -> district parsed;
  - giữ khóa huyện bằng district_units[0] (không dùng _unique_code bỏ khóa
    khi có nhiều mention — bỏ khóa làm exact-match trả nhiều ứng viên
    cùng điểm 1.0, margin về 0);
  - chống trùng ứng viên theo ward_code: cùng một xã chỉ trả một lần,
    nếu không ranker cũng tính margin = 0.
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
    """Veto chung cho ward, dùng cho cả exact-match và fuzzy fallback.

    Chỉ bằng parent_code, không kiểm tra level. parent_code=None là veto,
    không phải là 'không rõ nên cho qua'.
    """
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


class FuzzyMatcher:
    def match(self, parsed: ParsedAddress, anchor: AnchorResult, index: AdminIndex) -> list[AddressCandidate]:
        if not anchor.anchored or anchor.province is None: return []
        scope=Scope(province_code=anchor.province.code)
        mentions=extract_admin_mentions(parsed.raw_input,index)
        ward_units=[u for u in mentions.get("ward",[])]
        district_units=[u for u in mentions.get("district",[]) if u.parent_code==scope.province_code]
        filtered=[]
        for ward in ward_units:
            # Sửa duy nhất ở đây so với bản gốc: bỏ nhánh
            # `elif ward.parent_code is None: filtered.append(ward)`.
            # Ward không có district cha thì không chứng minh được thuộc tỉnh
            # đã neo, phải veto thay vì ép vào tỉnh đó.
            if _ward_in_scope(ward, scope, index):
                filtered.append(ward)
        ward_units=_dedupe_by_code(filtered)
        if district_units and not scope.district_code: scope.district_code=district_units[0].code
        if not ward_units and parsed.ward_name:
            target=normalize_text(parsed.ward_name)
            for cand in index.inverted.get(target,[]):
                # Exact-match qua inverted: cùng veto tỉnh + huyện như nhánh
                # thường. _ward_in_scope dùng scope lúc này (đã có huyện từ
                # mentions, nếu có); huyện từ parsed sẽ được veto lại ở
                # vòng candidates bên dưới.
                if _ward_in_scope(cand, scope, index):
                    ward_units.append(cand)
            ward_units=_dedupe_by_code(ward_units)
        if not district_units and parsed.district_name:
            target=normalize_text(parsed.district_name)
            for cand in index.inverted.get(target,[]):
                # District là unit có parent_code == province_code (không
                # dùng level để phân biệt district/ward).
                if cand.parent_code==scope.province_code:
                    district_units.append(cand); scope.district_code=cand.code
        candidates: list[AddressCandidate]=[]
        for ward in ward_units:
            # Phòng thủ chiều sâu: veto lại với scope cuối cùng (gồm cả
            # huyện từ parsed.district_name) trước khi tạo contract.
            if not _ward_in_scope(ward, scope, index):
                continue
            district=index.units.get(ward.parent_code) if ward.parent_code else None
            if district is None or district.parent_code != scope.province_code:
                continue
            contract=AddressContract(raw_input=parsed.raw_input,province=anchor.province.name,district=district.name if district else None,ward=ward.name,province_code=anchor.province.code,district_code=district.code if district else None,ward_code=ward.code,source=ProviderSource.INTERNAL_ADMIN_DB)
            candidates.append(AddressCandidate(address=contract,provider=ProviderSource.INTERNAL_ADMIN_DB,provider_score=1.0,rank_score=1.0))
        if candidates: return candidates
        target=normalize_text(parsed.ward_name) if parsed.ward_name else ""
        if not target: return []
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
            contract=AddressContract(raw_input=parsed.raw_input,province=anchor.province.name,district=district.name if district else None,ward=ward.name,province_code=anchor.province.code,district_code=district.code if district else None,ward_code=ward.code,source=ProviderSource.INTERNAL_ADMIN_DB)
            candidates.append(AddressCandidate(address=contract,provider=ProviderSource.INTERNAL_ADMIN_DB,provider_score=score,rank_score=score))
        return candidates
