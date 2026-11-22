"""Fuzzy matcher — sửa theo test fail: veto huyện/xã qua parent_code,
không qua prefix chuỗi. Fallback tra cứu theo tên đã parse trong
tỉnh đã neo, để ca hợp lệ không bị mất ứng viên khi AC bỏ lỡ.
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
    province_code: str
    district_code: Optional[str] = None


class FuzzyMatcher:
    def match(self, parsed: ParsedAddress, anchor: AnchorResult,
              index: AdminIndex) -> list[AddressCandidate]:
        if not anchor.anchored or anchor.province is None:
            return []
        scope = Scope(province_code=anchor.province.code)
        mentions = extract_admin_mentions(parsed.raw_input, index)
        ward_units = [u for u in mentions.get("ward", [])]
        district_units = [u for u in mentions.get("district", [])
                          if u.parent_code == scope.province_code]
        # Lọc ward: ward phải thuộc một huyện của tỉnh đã neo.
        filtered_wards: list[AdminUnit] = []
        for ward in ward_units:
            district = index.units.get(ward.parent_code or "")
            if district is not None and district.parent_code == scope.province_code:
                filtered_wards.append(ward)
            elif ward.parent_code is None:
                filtered_wards.append(ward)
        ward_units = filtered_wards
        if district_units and not scope.district_code:
            scope.district_code = district_units[0].code
        # Fallback: tra theo tên đã parse (ward/district) trong tỉnh đã neo.
        if not ward_units and parsed.ward_name:
            target = normalize_text(parsed.ward_name)
            for cand in index.inverted.get(target, []):
                if cand.level != "ward":
                    continue
                district = index.units.get(cand.parent_code or "")
                if district is not None and district.parent_code == scope.province_code:
                    ward_units.append(cand)
        if not district_units and parsed.district_name:
            target = normalize_text(parsed.district_name)
            for cand in index.inverted.get(target, []):
                if cand.level == "district" and cand.parent_code == scope.province_code:
                    district_units.append(cand)
                    scope.district_code = cand.code
        candidates: list[AddressCandidate] = []
        for ward in ward_units:
            if scope.district_code and ward.parent_code != scope.district_code:
                continue
            district = index.units.get(ward.parent_code) if ward.parent_code else None
            contract = AddressContract(
                raw_input=parsed.raw_input, province=anchor.province.name,
                district=district.name if district else None, ward=ward.name,
                province_code=anchor.province.code,
                district_code=district.code if district else None, ward_code=ward.code,
                source=ProviderSource.INTERNAL_ADMIN_DB)
            candidates.append(AddressCandidate(address=contract, provider=ProviderSource.INTERNAL_ADMIN_DB,
                                               provider_score=1.0, rank_score=1.0))
        if candidates:
            return candidates
        target = normalize_text(parsed.ward_name) if parsed.ward_name else ""
        if not target:
            return []
        best: list[tuple[float, AdminUnit]] = []
        for district in index.districts_of(scope.province_code):
            if scope.district_code and district.code != scope.district_code:
                continue
            for ward in index.wards_of(district.code):
                score = difflib.SequenceMatcher(None, target, ward.normalized_name).ratio()
                if score >= 0.85:
                    best.append((score, ward))
        best.sort(key=lambda x: x[0], reverse=True)
        for score, ward in best[:3]:
            district = index.units.get(ward.parent_code) if ward.parent_code else None
            contract = AddressContract(
                raw_input=parsed.raw_input, province=anchor.province.name,
                district=district.name if district else None, ward=ward.name,
                province_code=anchor.province.code,
                district_code=district.code if district else None, ward_code=ward.code,
                source=ProviderSource.INTERNAL_ADMIN_DB)
            candidates.append(AddressCandidate(address=contract, provider=ProviderSource.INTERNAL_ADMIN_DB,
                                               provider_score=score, rank_score=score))
        return candidates
