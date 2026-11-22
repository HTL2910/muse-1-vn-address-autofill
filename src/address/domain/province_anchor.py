"""Khóa neo tỉnh — parent_code, không prefix. Aho-Corasick đã sửa
transition khi fail về root. Fix anchor: nhiều tỉnh được nhắc thì
lấy tỉnh ở đoạn CUỐI (địa chỉ VN: tỉnh ở cuối), không trả ambiguous;
Aho không trúng thì fallback substring/fuzzy trên toàn text."""
from __future__ import annotations
import difflib, re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable, Optional
from .schemas import normalize_text
ADMIN_PREFIX_RE=re.compile(r"\b(tinh|thanh pho|tp|quan|huyen|xa|phuong|thi tran|tt)\b", re.IGNORECASE)
GENERIC_TOKENS={"xom","bi","soi","nha","duong","so","ngo","ngach"}
def strip_admin_prefix(value: str) -> str: return ADMIN_PREFIX_RE.sub(" ", normalize_text(value)).strip()
@dataclass(frozen=True)
class AdminUnit:
    code: str; name: str; level: str; parent_code: Optional[str]=None; aliases: tuple[str, ...]=(); centroid_lat: Optional[float]=None; centroid_lng: Optional[float]=None
    @property
    def normalized_name(self) -> str: return strip_admin_prefix(self.name)
    def all_keys(self) -> list[str]:
        keys=[self.normalized_name]; keys.extend(strip_admin_prefix(a) for a in self.aliases if a)
        return [k for k in keys if k and k not in GENERIC_TOKENS]
class _ACNode:
    __slots__=("children","fail","outputs")
    def __init__(self) -> None: self.children: dict[str,_ACNode]={}; self.fail: Optional[_ACNode]=None; self.outputs: list[tuple[str,str]]=[]
class AhoCorasick:
    def __init__(self, patterns: Iterable[tuple[str,str]]) -> None:
        self.root=_ACNode(); self.root.fail=self.root
        for pattern,code in patterns:
            node=self.root
            for ch in pattern: node=node.children.setdefault(ch,_ACNode())
            node.outputs.append((pattern,code))
        self._build_fail_links()
    def _build_fail_links(self) -> None:
        queue: deque[_ACNode]=deque()
        for child in self.root.children.values(): child.fail=self.root; queue.append(child)
        while queue:
            node=queue.popleft()
            for ch,child in node.children.items():
                fail=node.fail
                while fail is not self.root and ch not in fail.children: fail=fail.fail
                child.fail=fail.children[ch] if ch in fail.children else self.root
                child.outputs.extend(child.fail.outputs); queue.append(child)
    def search(self, text: str) -> list[tuple[str,str,int,int]]:
        results=[]; node=self.root
        for i,ch in enumerate(text):
            while node is not self.root and ch not in node.children: node=node.fail
            node=node.children[ch] if ch in node.children else self.root
            for pattern,code in node.outputs: results.append((pattern,code,i-len(pattern)+1,i+1))
        return results
@dataclass
class AnchorResult:
    province: Optional[AdminUnit]=None; confidence: float=0.0; method: str="none"; matched_token: str=""; alternatives: list[AdminUnit]=field(default_factory=list); reason: str=""
    @property
    def anchored(self) -> bool: return self.province is not None and self.confidence>=0.6
class AdminIndex:
    def __init__(self, units: Iterable[AdminUnit]) -> None:
        self.units: dict[str,AdminUnit]={}; self.by_level: dict[str,list[AdminUnit]]=defaultdict(list); self.children: dict[str,list[AdminUnit]]=defaultdict(list); self.inverted: dict[str,list[AdminUnit]]=defaultdict(list)
        patterns: list[tuple[str,str]]=[]
        for unit in units:
            self.units[unit.code]=unit; self.by_level[unit.level].append(unit)
            if unit.parent_code: self.children[unit.parent_code].append(unit)
            for key in unit.all_keys():
                self.inverted[key].append(unit); patterns.append((key,unit.code)); patterns.append((f" {key} ",unit.code))
        self.automaton=AhoCorasick(patterns)
    @property
    def provinces(self) -> list[AdminUnit]: return self.by_level["province"]
    def districts_of(self, province_code: str) -> list[AdminUnit]: return self.children.get(province_code,[])
    def wards_of(self, district_code: str) -> list[AdminUnit]: return self.children.get(district_code,[])
    def fuzzy_province(self, token: str, threshold: float=0.82) -> list[tuple[AdminUnit,float]]:
        scored=[]
        for prov in self.provinces:
            for key in prov.all_keys():
                s=difflib.SequenceMatcher(None,token,key).ratio()
                if s>=threshold: scored.append((prov,s))
        scored.sort(key=lambda x:x[1],reverse=True); seen: dict[str,float]={}
        for u,s in scored: seen[u.code]=max(seen.get(u.code,0.0),s)
        return sorted(((self.units[c],s) for c,s in seen.items()),key=lambda x:x[1],reverse=True)
def anchor_province(raw_input: str, index: AdminIndex) -> AnchorResult:
    text=f" {normalize_text(raw_input)} "
    if len(normalize_text(raw_input))<2: return AnchorResult(reason="input_too_short")
    hits=index.automaton.search(text); province_hits: dict[str,tuple[AdminUnit,str,float,int]]={}
    for pattern,code,_s,end in hits:
        unit=index.units.get(code)
        if unit is None or unit.level!="province": continue
        score=0.95 if len(pattern.strip())>=len(unit.normalized_name) else 0.85
        prev=province_hits.get(code)
        if prev is None or end>prev[3] or score>prev[2]: province_hits[code]=(unit,pattern.strip(),score,end)
    if len(province_hits)==1:
        unit,token,score,_e=next(iter(province_hits.values())); return AnchorResult(province=unit,confidence=score,method="exact" if token==unit.normalized_name else "alias",matched_token=token)
    if len(province_hits)>1:
        # Tỉnh ở đoạn cuối mới là tỉnh khai báo; tên tỉnh trong tên
        # đường (đoạn đầu) không được veto tỉnh thật.
        ordered=sorted(province_hits.values(),key=lambda v:v[3])
        unit,token,score,_e=ordered[-1]
        return AnchorResult(province=unit,confidence=0.8,method="last_mention",matched_token=token,alternatives=[v[0] for v in ordered[:-1]])
    # Fallback: substring toàn đoạn (phòng khi automaton/pattern lệch).
    for prov in index.provinces:
        for key in prov.all_keys():
            if f" {key} " in text:
                return AnchorResult(province=prov,confidence=0.9,method="substring",matched_token=key)
    for token in [t for t in normalize_text(raw_input).split() if len(t)>=4]:
        fuzzy=index.fuzzy_province(token)
        if fuzzy and fuzzy[0][1]>=0.88:
            if len(fuzzy)>1 and (fuzzy[0][1]-fuzzy[1][1])<0.05: return AnchorResult(method="ambiguous",alternatives=[u for u,_ in fuzzy[:2]],reason="fuzzy_province_ambiguous")
            unit,score=fuzzy[0]; return AnchorResult(province=unit,confidence=score*0.9,method="fuzzy",matched_token=token)
    return AnchorResult(reason="province_not_found")
def extract_admin_mentions(raw_input: str, index: AdminIndex) -> dict[str,list[AdminUnit]]:
    text=f" {normalize_text(raw_input)} "; found: dict[str,list[AdminUnit]]=defaultdict(list)
    for _p,code,_s,_e in index.automaton.search(text):
        unit=index.units.get(code)
        if unit is not None and unit not in found[unit.level]: found[unit.level].append(unit)
    return dict(found)
def filter_candidates_by_anchor(candidates: list[AdminUnit], anchor: AnchorResult) -> list[AdminUnit]:
    if not anchor.anchored or anchor.province is None: return candidates
    prov=anchor.province.code; kept=[]
    for unit in candidates:
        if unit.level=="province" and unit.code==prov: kept.append(unit)
        elif unit.level=="district" and unit.parent_code==prov: kept.append(unit)
        elif unit.level=="ward": kept.append(unit)
    return kept
def propagate_constraints(district: Optional[AdminUnit], ward: Optional[AdminUnit], anchor: AnchorResult) -> tuple[float,list[str]]:
    conflicts=[]; penalty=0.0
    if anchor.anchored and anchor.province is not None and district is not None and district.parent_code!=anchor.province.code:
        conflicts.append("district_not_in_anchored_province"); penalty=1.0
    if district is not None and ward is not None and ward.parent_code!=district.code:
        conflicts.append("ward_not_in_district"); penalty=max(penalty,0.9)
    return penalty,conflicts
def load_admin_units_from_json(rows: list[dict]) -> list[AdminUnit]:
    return [AdminUnit(code=str(r["code"]),name=str(r["name"]),level=str(r["level"]),parent_code=str(r["parent_code"]) if r.get("parent_code") else None,aliases=tuple(r.get("aliases") or ()),centroid_lat=r.get("centroid_lat"),centroid_lng=r.get("centroid_lng")) for r in rows]
