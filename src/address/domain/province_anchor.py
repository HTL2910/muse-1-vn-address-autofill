"""
Khóa neo tỉnh và lan truyền ràng buộc phân cấp — approach_05.

Nguyên tắc: nhận diện tỉnh TRƯỚC, khóa không gian tìm kiếm chỉ trong
tỉnh đó, rồi mới tìm huyện -> xã con của nó. Ứng viên khác tỉnh bị
loại 100% dù điểm fuzzy cao. Triệt tiêu:
  - "xã Cuôr Đăng, Đắk Lắk" -> Phường Phả Lại, Chí Linh, Hải Dương
  - khách ghi tỉnh này, máy điền tỉnh khác

Kỹ thuật: Trie + inverted index + Aho-Corasick để quét đồng thời mọi
tên hành chính trong một lần duyệt chuỗi; alias không dấu/có dấu,
tên cũ sau sáp nhập; fuzzy chỉ là dự phòng khi không khớp chính xác.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .schemas import normalize_text

# Tiền tố hành chính — bỏ đi khi so khớp để "Tỉnh Đắk Lắk" == "Đắk Lắk".
ADMIN_PREFIX_RE = re.compile(
    r"\b(tinh|thanh pho|tp|quan|huyen|xa|phuong|thi tran|tt)\b", re.IGNORECASE
)
# Từ chung không bao giờ được coi là địa danh đủ nghĩa.
GENERIC_TOKENS = {"xom", "bi", "soi", "nha", "duong", "so", "ngo", "ngach"}


def strip_admin_prefix(value: str) -> str:
    return ADMIN_PREFIX_RE.sub(" ", normalize_text(value)).strip()


def _strip_diacritics(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.replace("đ", "d").replace("Đ", "d")


@dataclass(frozen=True)
class AdminUnit:
    code: str  # tỉnh: 2 số, huyện: 3 số, xã: 5 số
    name: str  # tên chuẩn có dấu, vd "Đắk Lắk", "Cuôr Đăng"
    level: str  # province | district | ward
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


# ---------------------------------------------------------------------------
# Aho-Corasick: quét 1 lần, tìm mọi tên hành chính xuất hiện trong chuỗi
# ---------------------------------------------------------------------------
class _ACNode:
    __slots__ = ("children", "fail", "outputs")

    def __init__(self) -> None:
        self.children: dict[str, _ACNode] = {}
        self.fail: Optional[_ACNode] = None
        self.outputs: list[tuple[str, str]] = []  # (pattern, unit_code)


class AhoCorasick:
    def __init__(self, patterns: Iterable[tuple[str, str]]) -> None:
        self.root = _ACNode()
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
                while fail is not None and ch not in fail.children:
                    fail = fail.fail
                child.fail = fail.children[ch] if fail else self.root
                child.outputs.extend(child.fail.outputs if child.fail else [])
                queue.append(child)

    def search(self, text: str) -> list[tuple[str, str, int, int]]:
        """Trả về (pattern, unit_code, start, end) trên chuỗi đã chuẩn hóa."""
        results: list[tuple[str, str, int, int]] = []
        node = self.root
        for i, ch in enumerate(text):
            while node is not None and ch not in node.children:
                node = node.fail
            node = node.children[ch] if node else self.root
            if node is None:
                node = self.root
            for pattern, code in node.outputs:
                results.append((pattern, code, i - len(pattern) + 1, i + 1))
        return results


@dataclass
class AnchorResult:
    province: Optional[AdminUnit] = None
    confidence: float = 0.0
    method: str = "none"  # exact | alias | fuzzy | ambiguous | none
    matched_token: str = ""
    alternatives: list[AdminUnit] = field(default_factory=list)
    reason: str = ""

    @property
    def anchored(self) -> bool:
        return self.province is not None and self.confidence >= 0.6


class AdminIndex:
    """Trie (qua AC) + inverted index trên toàn bộ master data hành chính."""

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
                # Khóa dạng " dak lak " để AC chỉ khớp ranh giới từ.
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
        """Dự phòng khi không khớp chính xác; chỉ xét trong danh sách tỉnh."""
        scored: list[tuple[AdminUnit, float]] = []
        for prov in self.provinces:
            for key in prov.all_keys():
                score = difflib.SequenceMatcher(None, token, key).ratio()
                if score >= threshold:
                    scored.append((prov, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        # Khử trùng lặp, giữ điểm cao nhất cho mỗi tỉnh.
        seen: dict[str, float] = {}
        for unit, score in scored:
            seen[unit.code] = max(seen.get(unit.code, 0.0), score)
        return sorted(
            ((self.units[c], s) for c, s in seen.items()),
            key=lambda x: x[1],
            reverse=True,
        )


def anchor_province(raw_input: str, index: AdminIndex) -> AnchorResult:
    """
    Bước Anchor: bắt buộc chạy trước mọi bước Match/Geocode.
    Trả về tỉnh đã khóa, hoặc ambiguous/none để tầng Gate từ chối.
    """
    text = f" {normalize_text(raw_input)} "
    if len(normalize_text(raw_input)) < 2:
        return AnchorResult(reason="input_too_short")

    hits = index.automaton.search(text)
    province_hits: dict[str, tuple[AdminUnit, str, float]] = {}
    for pattern, code, _s, _e in hits:
        unit = index.units.get(code)
        if unit is None or unit.level != "province":
            continue
        # Ưu tiên khớp dài nhất (vd "dak lak" > "lak"), rồi khớp có dấu gốc.
        score = 0.95 if len(pattern.strip()) >= len(unit.normalized_name) else 0.85
        prev = province_hits.get(code)
        if prev is None or score > prev[2]:
            province_hits[code] = (unit, pattern.strip(), score)

    if len(province_hits) == 1:
        unit, token, score = next(iter(province_hits.values()))
        method = "exact" if token == unit.normalized_name else "alias"
        return AnchorResult(province=unit, confidence=score, method=method,
                            matched_token=token)
    if len(province_hits) > 1:
        # Khách ghi 2 tỉnh khác nhau -> mơ hồ thật, không được tự chọn 1.
        alts = sorted((v[0] for v in province_hits.values()), key=lambda u: u.name)
        return AnchorResult(method="ambiguous", alternatives=alts,
                            reason="multiple_provinces_mentioned")

    # Không khớp chính xác: thử fuzzy trên từng token dài >= 4 ký tự.
    tokens = [t for t in normalize_text(raw_input).split() if len(t) >= 4]
    for token in tokens:
        fuzzy = index.fuzzy_province(token)
        if fuzzy and fuzzy[0][1] >= 0.88:
            unit, score = fuzzy[0]
            # Nếu top-2 quá sát nhau thì vẫn là mơ hồ.
            if len(fuzzy) > 1 and (fuzzy[0][1] - fuzzy[1][1]) < 0.05:
                return AnchorResult(method="ambiguous",
                                    alternatives=[u for u, _ in fuzzy[:2]],
                                    reason="fuzzy_province_ambiguous")
            return AnchorResult(province=unit, confidence=score * 0.9,
                                method="fuzzy", matched_token=token)
    return AnchorResult(reason="province_not_found")


def extract_admin_mentions(raw_input: str, index: AdminIndex) -> dict[str, list[AdminUnit]]:
    """Liệt kê mọi huyện/xã được nhắc tới — dùng để phát hiện mâu thuẫn."""
    text = f" {normalize_text(raw_input)} "
    found: dict[str, list[AdminUnit]] = defaultdict(list)
    for _pattern, code, _s, _e in index.automaton.search(text):
        unit = index.units.get(code)
        if unit is not None and unit not in found[unit.level]:
            found[unit.level].append(unit)
    return dict(found)


def filter_candidates_by_anchor(
    candidates: list[AdminUnit], anchor: AnchorResult
) -> list[AdminUnit]:
    """
    Luật veto 100%: nếu đã khóa tỉnh, mọi ứng viên khác tỉnh bị loại,
    bất kể điểm fuzzy/provider cao đến đâu.
    """
    if not anchor.anchored or anchor.province is None:
        return candidates
    prov = anchor.province.code
    kept: list[AdminUnit] = []
    for unit in candidates:
        if unit.level == "province":
            if unit.code == prov:
                kept.append(unit)
        elif unit.level == "district":
            if unit.code.startswith(prov):
                kept.append(unit)
        elif unit.level == "ward":
            if unit.code.startswith(prov):
                kept.append(unit)
    return kept


def propagate_constraints(
    district: Optional[AdminUnit],
    ward: Optional[AdminUnit],
    anchor: AnchorResult,
) -> tuple[float, list[str]]:
    """
    Lan truyền ràng buộc phân cấp: xã phải thuộc huyện, huyện phải
    thuộc tỉnh đã khóa. Trả về (penalty 0-1, danh sách xung đột).
    Penalty 1.0 = loại bỏ ứng viên.
    """
    conflicts: list[str] = []
    penalty = 0.0
    if anchor.anchored and anchor.province is not None:
        prov = anchor.province.code
        if district is not None and not district.code.startswith(prov):
            conflicts.append("district_not_in_anchored_province")
            penalty = 1.0
        if ward is not None and not ward.code.startswith(prov):
            conflicts.append("ward_not_in_anchored_province")
            penalty = 1.0
    if district is not None and ward is not None:
        if not ward.code.startswith(district.code):
            conflicts.append("ward_not_in_district")
            penalty = max(penalty, 0.9)
    return penalty, conflicts


def load_admin_units_from_json(rows: list[dict]) -> list[AdminUnit]:
    """Adapter nạp master data chuẩn (CSV/JSON từ nguồn chính thức)."""
    units: list[AdminUnit] = []
    for row in rows:
        units.append(
            AdminUnit(
                code=str(row["code"]),
                name=str(row["name"]),
                level=str(row["level"]),
                parent_code=str(row["parent_code"]) if row.get("parent_code") else None,
                aliases=tuple(row.get("aliases") or ()),
                centroid_lat=row.get("centroid_lat"),
                centroid_lng=row.get("centroid_lng"),
            )
        )
    return units
