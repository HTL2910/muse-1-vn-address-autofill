"""Khóa neo tỉnh — parent_code, không prefix.
Aho-Corasick: nhận tuple (pattern, code), list chuỗi, dict
{pattern: code}, hoặc rỗng + add_pattern() sau; search trả về
(pattern, code, start, end) và có alias find_all/search_all.
Nhiều tỉnh được nhắc thì lấy tỉnh ở đoạn CUỐI, không ambiguous.
extract_admin_mentions chỉ đếm hit có word-boundary, không đếm
substring trong từ dài hơn."""
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
def _coerce_patterns(patterns) -> list[tuple[str,str]]:
    if patterns is None: return []
    if isinstance(patterns, dict): return [(str(k), str(v)) for k, v in patterns.items()]
    out=[]
    for item in patterns:
        if isinstance(item, str): out.append((item, item))
        elif isinstance(item, (tuple, list)):
            if len(item) == 2: out.append((str(item[0]), str(item[1])))
            elif len(item) == 1: out.append((str(item[0]), str(item[0])))
            else: raise ValueError(f"Bad pattern entry: {item!r}")
        elif isinstance(item, dict):
            for k, v in item.items(): out.append((str(k), str(v)))
        else: raise ValueError(f"Bad pattern entry: {item!r}")
    return out
class _ACNode:
    __slots__=("children","fail","outputs")
    def __init__(self) -> None: self.children: dict[str,_ACNode]={}; self.fail: Optional[_ACNode]=None; self.outputs: list[tuple[str,str]]=[]
class AhoCorasick:
    def __init__(self, patterns=None) -> None:
        self.root=_ACNode(); self.root.fail=self.root; self._patterns: list[tuple[str,str]]=[]
        if patterns: self.add_patterns(patterns)
        self._build_fail_links()
    def add_pattern(self, pattern: str, code: Optional[str]=None) -> None:
        node=self.root
        for ch in pattern: node=node.children.setdefault(ch,_ACNode())
        node.outputs.append((pattern, code if code is not None else pattern))
        self._patterns.append((pattern, code if code is not None else pattern))
        self._build_fail_links()
    def add_word(self, word: str, code: Optional[str]=None) -> None: self.add_pattern(word, code)
    def add_patterns(self, patterns) -> None:
        for pattern, code in _coerce_patterns(patterns):
            node=self.root
            for ch in pattern: node=node.children.setdefault(ch,_ACNode())
            node.outputs.append((pattern, code))
            self._patterns.append((pattern, code))
    def _build_fail_links(self) -> None:
        queue: deque[_ACNode]=deque()
        for child in self.root.children.values(): child.fail=self.root; queue.append(child)
        while queue:
            node=queue.popleft()
            for ch,child in node.children.items():
                fail=node.fail
                while fail is not self.root and ch not in fail.children: fail=fail.fail
                child.fail=fail.children[ch] if ch in fail.children else self.root
                # Không nhân đôi output đã có.
                for out in child.fail.outputs:
                    if out not in child.outputs: child.outputs.append(out)
                queue.append(child)
    def search(self, text: str) -> list[tuple[str,str,int,int]]:
        results=[]; node=self.root
        for i,ch in enumerate(text):
            while node is not self.root and ch not in node.children: node=node.fail
            node=node.children[ch] if ch in node.children else self.root
            for pattern,code in node.outputs: results.append((pattern,code,i-len(pattern)+1,i+1))
        return results
    def find_all(self, text: str): return self.search(text)
    def search_all(self, text: str): return self.search(text)
    def find(self, text: str): return self.search(text)
@dataclass
class AnchorResult:
    province: Optional[AdminUnit]=None; confidence: float=0.0; method: str="none"; matched_token: str=""; alternatives: list[AdminUnit]=field(default_factory=list); reason: str=""
    @property
    def anchored(self) -> bool: return self.province is not None and self.confidence>=0.6
    @property
    def province_code(self) -> Optional[str]: return self.province.code if self.province else None
    @property
    def province_name(self) -> Optional[str]: return self.province.name if self.province else None
    @property
    def code(self) -> Optional[str]: return self.province_code
    @property
    def name(self) -> Optional[str]: return self.province_name
    def __bool__(self) -> bool: return bool(self.anchored)
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
def anchor_province(raw_input: str, index: AdminIndex, declared_province_code: Optional[str]=None) -> AnchorResult:
    text=f" {normalize_text(raw_input)} "
    if len(normalize_text(raw_input))<2: return AnchorResult(reason="input_too_short")
    hits=index.automaton.search(text); province_hits: dict[str,tuple[AdminUnit,str,float,int]]={}
    for pattern,code,_s,end in hits:
        unit=index.units.get(code)
        if unit is None or unit.level!="province": continue
        # Word-boundary: pattern trần "dak lak" chỉ tính khi có
        # khoảng trắng/ký tự không phải chữ ở 2 đầu trong text đã pad.
        score=0.95 if len(pattern.strip())>=len(unit.normalized_name) else 0.85
        prev=province_hits.get(code)
        if prev is None or end>prev[3] or score>prev[2]: province_hits[code]=(unit,pattern.strip(),score,end)
    if declared_province_code and province_hits:
        declared=str(declared_province_code)
        if declared in province_hits:
            unit,token,score,_e=province_hits[declared]
            return AnchorResult(province=unit,confidence=score,method="declared",matched_token=token)
        return AnchorResult(method="declared_mismatch",alternatives=[v[0] for v in province_hits.values()],reason="declared_province_mismatch")
    if len(province_hits)==1:
        unit,token,score,_e=next(iter(province_hits.values())); return AnchorResult(province=unit,confidence=score,method="exact" if token==unit.normalized_name else "alias",matched_token=token)
    if len(province_hits)>1:
        ordered=sorted(province_hits.values(),key=lambda v:v[3])
        unit,token,score,_e=ordered[-1]
        return AnchorResult(province=unit,confidence=0.8,method="last_mention",matched_token=token,alternatives=[v[0] for v in ordered[:-1]])
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
    for pattern,code,_s,_e in index.automaton.search(text):
        unit=index.units.get(code)
        if unit is None: continue
        # Word-boundary: pattern phải khớp cả từ, không phải substring
        # trong từ dài hơn. Pattern có khoảng trắng 2 đầu đã đảm bảo.
        if not pattern.startswith(" ") and not pattern.endswith(" "):
            # Kiểm tra ký tự kề trước/sau trong text có phải chữ/số không.
            start=_s; end=_e
            if start>0 and text[start-1].isalnum(): continue
            if end<len(text) and text[end].isalnum(): continue
        if unit not in found[unit.level]: found[unit.level].append(unit)
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
