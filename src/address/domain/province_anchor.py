"""Province anchor — Aho-Corasick + neo tỉnh theo Canonical Admin DB.

Fix fix_aho_corasick_missed_mention: xử lý lại ký tự hiện tại sau fail.

Lỗi cũ (trong vòng lặp tìm kiếm): khi trạng thái hiện tại không có
chuyển đổi cho ký tự `ch`, code đi theo fail-link rồi bỏ luôn `ch` và
chuyển sang ký tự tiếp theo. `ch` chính là ký tự đầu tiên của một
pattern khác, nên mention đó bị mất. Ví dụ patterns ["ab", "ba"],
text "aba": sau khi khớp "ab" ở 0-1, ký tự 'a' ở vị trí 2 không có
chuyển đổi từ trạng thái "ab"; đi theo fail-link sang trạng thái "b"
thì 'a' mở ra "ba" ở 1-2 — code cũ bỏ 'a' nên chỉ tìm được "ab".
Tương tự với tên hành chính: sau một match một phần, ký tự đầu của
tên tỉnh/huyện/xã tiếp theo bị nuốt mất, anchor mất tỉnh.

Sau fix, vòng lặp tìm kiếm là dạng chuẩn của Aho-Corasick:
    while state != root and ch not in children[state]:
        state = fail[state]
    if ch in children[state]:
        state = children[state][ch]
    else:
        state = root
Ký tự `ch` được thử lại tại trạng thái mới sau fail — không bao giờ
bị bỏ qua. Output được thu thập qua dict-suffix-link để các pattern
là hậu tố của pattern khác (ví dụ "lak" trong "dak lak", "he" trong
"she") cũng được trả về.

API giữ nguyên cho các caller hiện có (fuzzy_matcher, validator,
ranker, address_service):
  - AdminUnit, AdminIndex, AnchorResult, extract_admin_mentions,
    anchor_province, ProvinceAnchor.
  - extract_admin_mentions(raw_input, index) ->
    {"province": [...], "district": [...], "ward": [...]}.
  - anchor_province(raw_input, index, declared_province_code=None) ->
    AnchorResult(anchored, province, ...).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, NamedTuple, Optional

from .schemas import normalize_text


# ---------------------------------------------------------------------------
# Aho-Corasick
# ---------------------------------------------------------------------------

class Match(NamedTuple):
    """Một pattern được tìm thấy: [start, end) trong text đã chuẩn hóa."""
    start: int
    end: int
    pattern: str
    payload: Any = None


class _Node:
    __slots__ = ("children", "fail", "output", "dict_suffix_link")

    def __init__(self) -> None:
        self.children: dict[str, int] = {}
        self.fail: int = 0
        # output: list[(pattern, payload)] kết thúc đúng tại node này.
        self.output: list[tuple[str, Any]] = []
        # dict_suffix_link: node gần nhất qua fail-link mà có output.
        self.dict_suffix_link: Optional[int] = None


class AhoCorasick:
    """Aho-Corasick chuẩn: fail-link + xử lý lại ký tự hiện tại sau fail."""

    def __init__(self, patterns: Optional[Iterable] = None) -> None:
        self.nodes: list[_Node] = [_Node()]  # node 0 là root
        self._built = False
        if patterns:
            for item in patterns:
                if isinstance(item, (tuple, list)) and len(item) == 2:
                    self.add_pattern(item[0], item[1])
                else:
                    self.add_pattern(item)

    def add_pattern(self, pattern: str, payload: Any = None) -> None:
        if not pattern:
            return
        state = 0
        for ch in pattern:
            nxt = self.nodes[state].children.get(ch)
            if nxt is None:
                nxt = len(self.nodes)
                self.nodes[state].children[ch] = nxt
                self.nodes.append(_Node())
            state = nxt
        self.nodes[state].output.append((pattern, payload if payload is not None else pattern))
        self._built = False

    # Alias cho caller dùng tên khác.
    add = add_pattern
    insert = add_pattern

    def _go(self, state: int, ch: str) -> int:
        """Chuyển đổi khi xây fail-link: đi theo fail cho tới khi có
        chuyển đổi cho `ch` hoặc về root."""
        while state != 0 and ch not in self.nodes[state].children:
            state = self.nodes[state].fail
        return self.nodes[state].children.get(ch, 0)

    def build(self) -> None:
        """Xây fail-link và dict-suffix-link bằng BFS."""
        if self._built:
            return
        queue: deque[int] = deque()
        for ch, child in self.nodes[0].children.items():
            self.nodes[child].fail = 0
            self.nodes[child].dict_suffix_link = None
            queue.append(child)
        while queue:
            state = queue.popleft()
            for ch, child in self.nodes[state].children.items():
                fail = self._go(self.nodes[state].fail, ch)
                self.nodes[child].fail = fail
                if self.nodes[fail].output:
                    self.nodes[child].dict_suffix_link = fail
                else:
                    self.nodes[child].dict_suffix_link = self.nodes[fail].dict_suffix_link
                queue.append(child)
        self._built = True

    # Alias cho caller dùng tên khác.
    build_failure_links = build

    def search(self, text: str) -> list[Match]:
        """Tìm mọi pattern trong text. Đây là nơi sửa lỗi missed-mention:
        sau khi đi theo fail-link, ký tự hiện tại được xử lý lại tại
        trạng thái mới — không bị bỏ qua."""
        self.build()
        results: list[Match] = []
        state = 0
        for i, ch in enumerate(text or ""):
            # Đi theo fail-link cho tới khi có chuyển đổi cho `ch`
            # hoặc về root, rồi thử lại CHÍNH `ch` đó (không bỏ qua).
            while state != 0 and ch not in self.nodes[state].children:
                state = self.nodes[state].fail
            if ch in self.nodes[state].children:
                state = self.nodes[state].children[ch]
            else:
                state = 0
            # Thu thập output của node hiện tại và của các hậu tố
            # qua dict-suffix-link (pattern là hậu tố của pattern khác).
            node: Optional[int] = state
            while node is not None:
                for pattern, payload in self.nodes[node].output:
                    results.append(Match(i - len(pattern) + 1, i + 1, pattern, payload))
                node = self.nodes[node].dict_suffix_link
        return results

    # Alias cho caller dùng tên khác.
    find_all = search
    search_all = search
    find = search


def build_aho_corasick(patterns: Iterable) -> AhoCorasick:
    """Hàm tiện ích: xây automaton từ một iterable pattern."""
    ac = AhoCorasick(patterns)
    ac.build()
    return ac


def aho_corasick_search(text: str, patterns: Iterable) -> list[Match]:
    """Hàm tiện ích: tìm mọi pattern trong text, không cần tự xây."""
    return AhoCorasick(patterns).search(text)


# ---------------------------------------------------------------------------
# Admin DB: AdminUnit / AdminIndex
# ---------------------------------------------------------------------------

@dataclass
class AdminUnit:
    code: str
    name: str
    normalized_name: str = ""
    level: str = ""  # "province" | "district" | "ward"
    parent_code: Optional[str] = None
    centroid_lat: Optional[float] = None
    centroid_lng: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.normalized_name and self.name:
            self.normalized_name = normalize_text(self.name)


class AdminIndex:
    """Index trên Canonical Admin DB: units, inverted, districts/wards."""

    def __init__(self, units=None) -> None:
        self.units: dict[str, AdminUnit] = {}
        if units is None:
            pass
        elif isinstance(units, dict):
            for code, unit in units.items():
                self._add(unit, code)
        else:
            for unit in units:
                self._add(unit)
        self.inverted: dict[str, list[AdminUnit]] = {}
        for unit in self.units.values():
            if unit.normalized_name:
                self.inverted.setdefault(unit.normalized_name, []).append(unit)
        self._automaton: Optional[AhoCorasick] = None

    def _add(self, unit, code: Optional[str] = None) -> None:
        if isinstance(unit, dict):
            unit = AdminUnit(
                code=str(unit.get("code") or code or ""),
                name=str(unit.get("name") or ""),
                normalized_name=str(unit.get("normalized_name") or ""),
                level=str(unit.get("level") or ""),
                parent_code=unit.get("parent_code"),
                centroid_lat=unit.get("centroid_lat"),
                centroid_lng=unit.get("centroid_lng"),
            )
        if getattr(unit, "code", None):
            self.units[str(unit.code)] = unit

    @classmethod
    def from_units(cls, units) -> "AdminIndex":
        return cls(units)

    def provinces(self) -> list[AdminUnit]:
        return [u for u in self.units.values() if u.level == "province"]

    def districts_of(self, province_code: str) -> list[AdminUnit]:
        return [u for u in self.units.values()
                if u.level == "district" and u.parent_code == province_code]

    def wards_of(self, district_code: str) -> list[AdminUnit]:
        return [u for u in self.units.values()
                if u.level == "ward" and u.parent_code == district_code]

    def build_automaton(self) -> AhoCorasick:
        if self._automaton is None:
            ac = AhoCorasick()
            for unit in self.units.values():
                if unit.normalized_name:
                    ac.add_pattern(unit.normalized_name, payload=unit)
            ac.build()
            self._automaton = ac
        return self._automaton

    @property
    def automaton(self) -> AhoCorasick:
        return self.build_automaton()


# ---------------------------------------------------------------------------
# extract_admin_mentions / anchor_province
# ---------------------------------------------------------------------------

def _iter_units(index) -> list[AdminUnit]:
    units = getattr(index, "units", None)
    if isinstance(units, dict):
        return list(units.values())
    if isinstance(units, (list, tuple, set)):
        return list(units)
    return []


def _automaton_for(index) -> AhoCorasick:
    builder = getattr(index, "build_automaton", None)
    if callable(builder):
        return builder()
    cached = getattr(index, "_automaton", None)
    if isinstance(cached, AhoCorasick):
        return cached
    ac = AhoCorasick()
    for unit in _iter_units(index):
        name = getattr(unit, "normalized_name", None) or normalize_text(getattr(unit, "name", "") or "")
        if name:
            ac.add_pattern(name, payload=unit)
    ac.build()
    try:
        index._automaton = ac  # type: ignore[attr-defined]
    except Exception:
        pass
    return ac


def _is_word_boundary(text: str, start: int, end: int) -> bool:
    """Match phải nằm trên ranh giới từ: ký tự trước/sau không phải
    chữ/số. "dak lakx" không được khớp "dak lak"."""
    if start > 0 and text[start - 1].isalnum():
        return False
    if end < len(text) and text[end].isalnum():
        return False
    return True


def extract_admin_mentions(raw_input: str, index) -> dict[str, list[AdminUnit]]:
    """Trích mọi tên tỉnh/huyện/xã xuất hiện trong raw_input.

    Dùng Aho-Corasick trên text đã chuẩn hóa (không dấu, lower) nên
    "Đắk Lắk" và "dak lak" đều khớp. Kết quả dedupe theo code, giữ
    thứ tự xuất hiện (match dài hơn trước nếu cùng vị trí bắt đầu).
    """
    result: dict[str, list[AdminUnit]] = {"province": [], "district": [], "ward": []}
    if not raw_input or index is None:
        return result
    text = normalize_text(raw_input)
    if not text:
        return result
    ac = _automaton_for(index)
    matches = ac.search(text)
    # Dài hơn trước khi cùng vị trí bắt đầu, để tên dài được ưu tiên.
    matches.sort(key=lambda m: (m.start, -(m.end - m.start)))
    seen: set[str] = set()
    for match in matches:
        if not _is_word_boundary(text, match.start, match.end):
            continue
        unit = match.payload
        if unit is None or not isinstance(unit, AdminUnit):
            continue
        level = unit.level
        if level not in result:
            continue
        if unit.code in seen:
            continue
        seen.add(unit.code)
        result[level].append(unit)
    return result


# Alias cho caller dùng tên khác.
extract_mentions = extract_admin_mentions


@dataclass
class AnchorResult:
    anchored: bool = False
    province: Optional[AdminUnit] = None
    matched_text: Optional[str] = None
    confidence: float = 0.0
    reason: Optional[str] = None
    declared_province_code: Optional[str] = None

    @property
    def province_code(self) -> Optional[str]:
        return self.province.code if self.province else None


def anchor_province(raw_input: str, index,
                    declared_province_code: Optional[str] = None) -> AnchorResult:
    """Neo tỉnh từ tên tỉnh khách viết. Fail-closed:
    không có tỉnh -> không neo; nhiều tỉnh khác nhau -> không neo
    (mơ hồ); tỉnh neo khác tỉnh khách khai -> không neo (mismatch)."""
    mentions = extract_admin_mentions(raw_input, index)
    provinces: list[AdminUnit] = []
    seen: set[str] = set()
    for unit in mentions.get("province", []):
        if unit.code not in seen:
            seen.add(unit.code)
            provinces.append(unit)
    if not provinces:
        return AnchorResult(anchored=False, reason="NO_PROVINCE_MENTION",
                            declared_province_code=declared_province_code)
    if len(provinces) > 1:
        return AnchorResult(anchored=False, reason="AMBIGUOUS_PROVINCE",
                            declared_province_code=declared_province_code)
    province = provinces[0]
    if declared_province_code is not None and str(declared_province_code) != str(province.code):
        return AnchorResult(anchored=False, reason="PROVINCE_MISMATCH",
                            declared_province_code=declared_province_code)
    return AnchorResult(anchored=True, province=province, matched_text=province.name,
                        confidence=1.0, declared_province_code=declared_province_code)


# Alias cho caller dùng tên khác.
anchor = anchor_province


class ProvinceAnchor:
    """Lớp bao cho anchor_province / extract_admin_mentions."""

    def __init__(self, index) -> None:
        self.index = index

    def anchor(self, raw_input: str, declared_province_code: Optional[str] = None) -> AnchorResult:
        return anchor_province(raw_input, self.index, declared_province_code)

    def extract_mentions(self, raw_input: str) -> dict[str, list[AdminUnit]]:
        return extract_admin_mentions(raw_input, self.index)

    def __call__(self, raw_input: str, declared_province_code: Optional[str] = None) -> AnchorResult:
        return self.anchor(raw_input, declared_province_code)
