"""Khóa neo tỉnh — sửa theo test fail: propagate_constraints dùng
parent_code, không dùng prefix chuỗi (mã thật không có tính prefix).
"""
from __future__ import annotations

import difflib
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .schemas import normalize_text

ADMIN_PREFIX_RE = re.compile(r"\b(tinh|thanh pho|tp|quan|huyen|xa|phuong|thi tran|tt)\b", re.IGNORECASE)
GENERIC_TOKENS = {"xom", "bi", "soi", "nha", "duong", "so", "ngo", "ngach"}


def strip_admin_prefix(value: str) -> str:
    return ADMIN_PREFIX_RE.sub(" ", normalize_text(value)).strip()


@dataclass(frozen=True)
class AdminUnit:
    code: str
    name: str
    level: str
    parent_code: Optional[str] = None
    aliases: tuple[str, ...] = ()
    centroid_lat: Optional[float] = None
    centroid_lng: Optional[float] = None

    @property
    def normalized_name(self) -> str:
        return strip_admin_prefix(self.name)

    def all_keys(self) -> list[str]:
        keys = [self.normalized_name]
        keys.extend(strip_admin_prefix(a) for a in self.aliases if a)
        return [k for k in keys if k and k not in GENERIC_TOKENS]


class _ACNode:
    __slots__ = ("children", "fail", "outputs")

    def __init__(self) -> None:
        self.children: dict[str, _ACNode] = {}
        self.fail: Optional[_ACNode] = None
        self.outputs: list[tuple[str, str]] = []


class AhoCorasick:
    def __init__(self, patterns: Iterable[tuple[str, str]]) -> None:
        self.root = _ACNode()
        self.root.fail = self.root
        for pattern, code in patterns:
            node = self.root
            for ch in pattern:
                node = node.children.setdefault(ch, _ACNode())
            node.outputs.append((pattern, code))
        self._build_fail_links()

    def _build_fail_links(self) -> None:
        queue: deque[_ACNode] = deque()
        for child in self.root.children.values():
            child.fail = self.root
            queue.append(child)
        while queue:
            node = queue.popleft()
            for ch, child in node.children.items():
                fail = node.fail
                while fail is not self.root and ch not in fail.children:
                    fail = fail.fail
                child.fail = fail.children[ch] if ch in fail.children else self.root
                child.outputs.extend(child.fail.outputs)
                queue.append(child)

    def search(self, text: str) -> list[tuple[str, str, int, int]]:
        results: list[tuple[str, str, int, int]] = []
        node = self.root
        for i, ch in enumerate(text):
            while node is not self.root and ch not in node.children:
                node = node.fail
            if ch in node.children:
                node = node.children[ch]
            else:
                node = self.root
            for pattern, code in node.outputs:
                results.append((pattern, code, i - len(pattern) + 1, i + 1))
        return results


@dataclass
class AnchorResult:
    province: Optional[AdminUnit] = None
    confidence: float = 0.0
    method: str = "none"
    matched_token: str = ""
    alternatives: list[AdminUnit] = field(default_factory=list)
    reason: str = ""

    @property
    def anchored(self) -> bool:
        return self.province is not None and self.confidence >= 0.6


class AdminIndex:
    def __init__(self, units: Iterable[AdminUnit]) -> None:
        self.units: dict[str, AdminUnit] = {}
        self.by_level: dict[str, list[AdminUnit]] = defaultdict(list)
        self.children: dict[str, list[AdminUnit]] = defaultdict(list)
        self.inverted: dict[str, list[AdminUnit]] = defaultdict(list)
        patterns: list[tuple[str, str]] = []
        for unit in units:
            self.units[unit.code] = unit
            self.by_level[unit.level].append(unit)
            if unit.parent_code:
                self.children[unit.parent_code].append(unit)
            for key in unit.all_keys():
                self.inverted[key].append(unit)
                patterns.append((key, unit.code))
                patterns.append((f" {key} ", unit.code))
        self.automaton = AhoCorasick(patterns)

    @property
    def provinces(self) -> list[AdminUnit]:
        return self.by_level["province"]

    def districts_of(self, province_code: str) -> list[AdminUnit]:
        return self.children.get(province_code, [])

    def wards_of(self, district_code: str) -> list[AdminUnit]:
        return self.children.get(district_code, [])

    def fuzzy_province(self, token: str, threshold: float = 0.82) -> list[tuple[AdminUnit, float]]:
        scored: list[tuple[AdminUnit, float]] = []
        for prov in self.provinces:
            for key in prov.all_keys():
                score = difflib.SequenceMatcher(None, token, key).ratio()
                if score >= threshold:
                    scored.append((prov, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        seen: dict[str, float] = {}
        for unit, score in scored:
            seen[unit.code] = max(seen.get(unit.code, 0.0), score)
        return sorted(((self.units[c], s) for c, s in seen.items()),
                      key=lambda x: x[1], reverse=True)


def anchor_province(raw_input: str, index: AdminIndex) -> AnchorResult:
    text = f" {normalize_text(raw_input)} "
    if len(normalize_text(raw_input)) < 2:
        return AnchorResult(reason="input_too_short")
    hits = index.automaton.search(text)
    province_hits: dict[str, tuple[AdminUnit, str, float]] = {}
    for pattern, code, _s, _e in hits:
        unit = index.units.get(code)
        if unit is None or unit.level != "province":
            continue
        score = 0.95 if len(pattern.strip()) >= len(unit.normalized_name) else 0.85
        prev = province_hits.get(code)
        if prev is None or score > prev[2]:
            province_hits[code] = (unit, pattern.strip(), score)
    if len(province_hits) == 1:
        unit, token, score = next(iter(province_hits.values()))
        method = "exact" if token == unit.normalized_name else "alias"
        return AnchorResult(province=unit, confidence=score, method=method, matched_token=token)
    if len(province_hits) > 1:
        return AnchorResult(method="ambiguous",
                            alternatives=[v[0] for v in province_hits.values()],
                            reason="multiple_provinces_mentioned")
    tokens = [t for t in normalize_text(raw_input).split() if len(t) >= 4]
    for token in tokens:
        fuzzy = index.fuzzy_province(token)
        if fuzzy and fuzzy[0][1] >= 0.88:
            if len(fuzzy) > 1 and (fuzzy[0][1] - fuzzy[1][1]) < 0.05:
                return AnchorResult(method="ambiguous", alternatives=[u for u, _ in fuzzy[:2]],
                                    reason="fuzzy_province_ambiguous")
            unit, score = fuzzy[0]
            return AnchorResult(province=unit, confidence=score * 0.9, method="fuzzy", matched_token=token)
    return AnchorResult(reason="province_not_found")


def extract_admin_mentions(raw_input: str, index: AdminIndex) -> dict[str, list[AdminUnit]]:
    text = f" {normalize_text(raw_input)} "
    found: dict[str, list[AdminUnit]] = defaultdict(list)
    for _pattern, code, _s, _e in index.automaton.search(text):
        unit = index.units.get(code)
        if unit is not None and unit not in found[unit.level]:
            found[unit.level].append(unit)
    return dict(found)


def filter_candidates_by_anchor(candidates: list[AdminUnit], anchor: AnchorResult) -> list[AdminUnit]:
    if not anchor.anchored or anchor.province is None:
        return candidates
    prov = anchor.province.code
    kept: list[AdminUnit] = []
    for unit in candidates:
        if unit.level == "province" and unit.code == prov:
            kept.append(unit)
        elif unit.level == "district" and unit.parent_code == prov:
            kept.append(unit)
        elif unit.level == "ward":
            # Xã thuộc huyện thuộc tỉnh đã neo: kiểm tra qua parent district.
            district = None
            # caller có thể tra parent_code; ở đây giữ ward nếu district cha thuộc tỉnh
            # được kiểm tra ở tầng matcher/validator bằng parent_code.
            kept.append(unit)
    return kept


def propagate_constraints(district: Optional[AdminUnit], ward: Optional[AdminUnit],
                           anchor: AnchorResult) -> tuple[float, list[str]]:
    conflicts: list[str] = []
    penalty = 0.0
    if anchor.anchored and anchor.province is not None:
        prov = anchor.province.code
        if district is not None and district.parent_code != prov:
            conflicts.append("district_not_in_anchored_province")
            penalty = 1.0
        if ward is not None and district is not None and ward.parent_code != district.code:
            conflicts.append("ward_not_in_district")
            penalty = max(penalty, 0.9)
        elif ward is not None and district is None:
            # Ward không có district đi kèm: không kết luận vội, để matcher tra parent.
            pass
    if district is not None and ward is not None and ward.parent_code != district.code:
        conflicts.append("ward_not_in_district")
        penalty = max(penalty, 0.9)
    return penalty, conflicts


def load_admin_units_from_json(rows: list[dict]) -> list[AdminUnit]:
    units: list[AdminUnit] = []
    for row in rows:
        units.append(AdminUnit(code=str(row["code"]), name=str(row["name"]),
                               level=str(row["level"]),
                               parent_code=str(row["parent_code"]) if row.get("parent_code") else None,
                               aliases=tuple(row.get("aliases") or ()),
                               centroid_lat=row.get("centroid_lat"),
                               centroid_lng=row.get("centroid_lng")))
    return units
