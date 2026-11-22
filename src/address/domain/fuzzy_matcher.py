"""Fuzzy matcher — veto qua parent_code, fallback tra tên parse.

Fix fix_fuzzy_exact_match_ignores_district:
Exact-match trước đây chỉ kiểm tra tỉnh, không kiểm tra huyện đã neo,
và còn giữ lại ward có parent_code=None rồi ép province_code của tỉnh
đã neo lên nó. Nhánh fuzzy fallback thì đã veto theo huyện, nên hai
nhánh mâu thuẫn nhau: cùng một xã trùng tên ở hai huyện trong một tỉnh,
nhánh exact có thể trả score 1.0 cho xã ở huyện sai.

Sau fix, exact-match và fuzzy fallback dùng chung một veto duy nhất
(_ward_in_scope / _district_in_scope):
  - ward phải có parent_code là một district tồn tại trong index;
  - district đó phải thuộc tỉnh đã neo (parent_code == scope.province_code);
  - nếu scope đã khóa huyện, ward.parent_code phải đúng huyện đó.
  - ward có parent_code=None bị veto: không chứng minh được thuộc tỉnh/
    huyện nào thì không được ép vào tỉnh đã neo, phải trả về rỗng để
    pipeline chuyển NEEDS_REVIEW thay vì chốt sai.

Thứ tự cũng được sửa: huyện được neo (từ mentions, nếu duy nhất, hoặc
từ parsed.district_name, nếu duy nhất) TRƯỚC khi tra exact ward qua
inverted index, để ward exact cũng chịu veto huyện đó. Không chọn
district_units[0] một cách tùy tiện khi có nhiều huyện được nhắc tới:
trường hợp mơ hồ thì không khóa huyện, để ranker/review xử lý.
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
    """Veto chung cho district: thuộc tỉnh đã neo, và đúng huyện đã khóa (nếu có)."""
    if district.level != "district":
        return False
    if district.parent_code != scope.province_code:
        return False
    if scope.district_code and district.code != scope.district_code:
        return False
    return True


def _ward_in_scope(ward: AdminUnit, scope: Scope, index: AdminIndex) -> bool:
    """Veto chung cho ward, dùng cho cả exact-match và fuzzy fallback.

    parent_code=None là veto, không phải là 'không rõ nên cho qua':
    không có district cha thì không thể chứng minh ward thuộc tỉnh đã neo.
    """
    if ward.level != "ward":
        return False
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


def _unique_code(units) -> Optional[str]:
    """Trả mã duy nhất nếu tất cả unit cùng một mã, ngược lại None (mơ hồ)."""
    codes = {u.code for u in units}
    return next(iter(codes)) if len(codes) == 1 else None


class FuzzyMatcher:
    def match(self, parsed: ParsedAddress, anchor: AnchorResult, index: AdminIndex) -> list[AddressCandidate]:
        if not anchor.anchored or anchor.province is None: return []
        scope=Scope(province_code=anchor.province.code)
        mentions=extract_admin_mentions(parsed.raw_input,index)

        # 1. Neo huyện TRƯỚC khi tra ward exact, để ward exact chịu cùng veto.
        # Chỉ khóa huyện khi duy nhất một huyện được nhắc tới / parse được.
        district_units=[u for u in mentions.get("district",[]) if _district_in_scope(u, scope)]
        if district_units:
            scope.district_code=_unique_code(district_units)
        elif parsed.district_name:
            target=normalize_text(parsed.district_name)
            parsed_districts=[c for c in index.inverted.get(target,[]) if _district_in_scope(c, scope)]
            if parsed_districts:
                district_units=parsed_districts
                scope.district_code=_unique_code(parsed_districts)

        # 2. Exact-match qua mentions: cùng veto tỉnh + huyện như nhánh thường.
        # Ward có parent_code=None hoặc district cha không thuộc tỉnh đã neo bị loại.
        ward_units=[u for u in mentions.get("ward",[]) if _ward_in_scope(u, scope, index)]

        # 3. Exact-match qua inverted index từ parsed.ward_name:
        # scope lúc này đã gồm huyện (nếu có), nên cùng veto được áp dụng.
        if not ward_units and parsed.ward_name:
            target=normalize_text(parsed.ward_name)
            for cand in index.inverted.get(target,[]):
                if _ward_in_scope(cand, scope, index):
                    ward_units.append(cand)

        candidates: list[AddressCandidate]=[]
        for ward in ward_units:
            # Phòng thủ chiều sâu: veto lại trước khi tạo contract, không bao giờ
            # tạo contract với district=None rồi ép province_code của tỉnh đã neo.
            if not _ward_in_scope(ward, scope, index):
                continue
            district=index.units.get(ward.parent_code) if ward.parent_code else None
            if district is None or district.parent_code != scope.province_code:
                continue
            contract=AddressContract(raw_input=parsed.raw_input,province=anchor.province.name,district=district.name if district else None,ward=ward.name,province_code=anchor.province.code,district_code=district.code if district else None,ward_code=ward.code,source=ProviderSource.INTERNAL_ADMIN_DB)
            candidates.append(AddressCandidate(address=contract,provider=ProviderSource.INTERNAL_ADMIN_DB,provider_score=1.0,rank_score=1.0))
        if candidates: return candidates

        # 4. Fuzzy fallback: dùng đúng cùng veto tỉnh + huyện như exact-match.
        target=normalize_text(parsed.ward_name) if parsed.ward_name else ""
        if not target: return []
        best=[]
        for district in index.districts_of(scope.province_code):
            if not _district_in_scope(district, scope):
                continue
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
