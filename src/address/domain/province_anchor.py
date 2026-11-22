"""Province anchor — Aho-Corasick + neo tỉnh theo Canonical Admin DB.

Fix fix_aho_corasick_missed_mention: xử lý lại ký tự hiện tại sau fail.

Lỗi cũ (trong vòng lặp tìm kiếm): khi trạng thái hiện tại không có
chuyển đổi cho ký tự `ch`, code đi theo fail-link rồi bỏ luôn `ch` và
chuyển sang ký tự tiếp theo. `ch` chính là ký tự đầu tiên của một
pattern khác, nên mention đó bị mất. Ví dụ patterns ["ab", "ba"],
text "aba": sau khi khớp "ab" ở 0-1, ký tự 'a' ở vị trí 2 không có
chuyển đổi từ trạng thái "ab"; đi theo fail-link sang trạng thái "b"
thì 'a' mở ra "ba" ở 1-2 — code cũ bỏ 'a' nên chỉ tìm được "ab".

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

Hai lỗi hồi quy đã sửa thêm (lượt chạy trước tự gây ra):
  - Unit trong index thật có thể mang `normalized_name` được tính bằng
    hàm khác (giữ dấu, giữ dấu nháy như "cu m'gar"), nên pattern có dấu
    không bao giờ khớp text đã bỏ dấu của `normalize_text` — anchor
    trả về `matched_tokens=[]` và `MISSING_PROVINCE` cho cả địa chỉ
    đúng. Giờ `_as_admin_unit` LUÔN tính lại
    `normalized_name = normalize_text(name)`, bất kể giá trị có sẵn.
  - `level` trong index thật có thể là tiếng Việt (`tinh`/`huyen`/`xa`
    ...) hoặc số (`1`/`2`/`3`). Giờ `_normalize_level` map chúng về
    `province`/`district`/`ward` trước khi dùng.
  - `AnchorResult` được thêm các trường service có thể đọc
    (`matched_tokens`, `unmatched_tokens`, `reason_code`, `status`
    dạng `AddressStatus` để `.value` vẫn dùng được).
  - Match chồng lấn được giải quyết theo longest-match: tên nằm trong
    lòng tên dài hơn (ví dụ "lak" trong "dak lak") không được tính là
    một mention riêng.

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

import csv
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, NamedTuple, Optional

from .schemas import AddressStatus, ReasonCode, normalize_text


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

def _normalize_level(value) -> str:
    """Map tên cấp (tiếng Việt / số / tiếng Anh) về province/district/ward."""
    if value is None:
        return ""
    v = normalize_text(str(value).strip())
    if v in ("province", "tinh", "thanh pho", "tp", "1"):
        return "province"
    if v in ("district", "huyen", "quan", "thi xa", "tx", "2"):
        return "district"
    if v in ("ward", "xa", "phuong", "thi tran", "tt", "3"):
        return "ward"
    return v


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
        self.level = _normalize_level(self.level)


def _as_admin_unit(unit) -> Optional[AdminUnit]:
    """Chuẩn hóa mọi dạng unit (AdminUnit, dict CSV, object khác) thành
    AdminUnit của file này, chấp nhận các tên khóa/thuộc tính khác nhau.
    `normalized_name` LUÔN được tính lại từ `name` bằng `normalize_text`
    để pattern và text tìm kiếm dùng cùng một chuẩn (bỏ dấu, bỏ nháy)."""
    if unit is None:
        return None
    if isinstance(unit, AdminUnit):
        if unit.name:
            unit.normalized_name = normalize_text(unit.name)
        unit.level = _normalize_level(unit.level)
        return unit
    if isinstance(unit, dict):
        code = unit.get("code") or unit.get("id") or ""
        name = unit.get("name") or ""
        level = unit.get("level") or unit.get("type") or unit.get("admin_level") or ""
        parent = unit.get("parent_code") or unit.get("parent") or unit.get("parentCode")
        lat = unit.get("centroid_lat") or unit.get("lat")
        lng = unit.get("centroid_lng") or unit.get("lng")
    else:
        code = getattr(unit, "code", "") or getattr(unit, "id", "") or ""
        name = getattr(unit, "name", "") or ""
        level = getattr(unit, "level", "") or getattr(unit, "type", "") or ""
        parent = getattr(unit, "parent_code", None)
        if parent is None:
            parent = getattr(unit, "parent", None)
        lat = getattr(unit, "centroid_lat", None)
        lng = getattr(unit, "centroid_lng", None)
    if not code and not name:
        return None
    normalized = normalize_text(str(name)) if name else ""
    return AdminUnit(code=str(code), name=str(name), normalized_name=normalized,
                     level=_normalize_level(level),
                     parent_code=(str(parent) if parent not in (None, "") else None),
                     centroid_lat=lat, centroid_lng=lng)


class AdminIndex:
    """Index trên Canonical Admin DB: units, inverted, districts/wards."""

    def __init__(self, units=None, **kwargs) -> None:
        if units is None:
            units = kwargs.get("units") or kwargs.get("records") or kwargs.get("admin_units") or kwargs.get("data")
        self.units: dict[str, AdminUnit] = {}
        if units is None:
            pass
        elif hasattr(units, "units"):
            # Nhận vào một AdminIndex khác.
            units = getattr(units, "units")
            if isinstance(units, dict):
                units = list(units.values())
        if isinstance(units, dict):
            for code, unit in units.items():
                converted = _as_admin_unit(unit)
                if converted is None and code:
                    converted = AdminUnit(code=str(code), name=str(code))
                if converted is not None:
                    self.units[str(converted.code)] = converted
        elif units is not None:
            for unit in units:
                converted = _as_admin_unit(unit)
                if converted is not None and converted.code:
                    self.units[str(converted.code)] = converted
        self.inverted: dict[str, list[AdminUnit]] = {}
        for unit in self.units.values():
            if unit.normalized_name:
                self.inverted.setdefault(unit.normalized_name, []).append(unit)
        self._automaton: Optional[AhoCorasick] = None

    def __iter__(self):
        return iter(self.units.values())

    def __len__(self) -> int:
        return len(self.units)

    def __contains__(self, code) -> bool:
        return str(code) in self.units

    @classmethod
    def from_units(cls, units) -> "AdminIndex":
        return cls(units)

    @classmethod
    def from_records(cls, records) -> "AdminIndex":
        return cls(records)

    @classmethod
    def from_csv(cls, paths) -> "AdminIndex":
        """Nạp unit từ một hoặc nhiều file CSV (DictReader)."""
        if isinstance(paths, (str, bytes)):
            paths = [paths]
        records: list[dict] = []
        for path in paths or []:
            try:
                with open(path, newline="", encoding="utf-8-sig") as f:
                    records.extend(list(csv.DictReader(f)))
            except FileNotFoundError:
                continue
        return cls(records)

    # Alias cho caller dùng tên khác.
    load = from_csv
    load_csv = from_csv

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
        raw = list(units.values())
    elif isinstance(units, (list, tuple, set)):
        raw = list(units)
    else:
        raw = []
    out: list[AdminUnit] = []
    for unit in raw:
        converted = _as_admin_unit(unit)
        if converted is not None:
            out.append(converted)
    return out


def _automaton_for(index) -> AhoCorasick:
    builder = getattr(index, "build_automaton", None)
    if callable(builder):
        try:
            return builder()
        except Exception:
            pass
    cached = getattr(index, "_automaton", None)
    if isinstance(cached, AhoCorasick):
        return cached
    ac = AhoCorasick()
    for unit in _iter_units(index):
        if unit.normalized_name:
            ac.add_pattern(unit.normalized_name, payload=unit)
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
    "Đắk Lắk" và "dak lak" đều khớp. Match chồng lấn được giải quyết
    theo longest-match: một tên nằm trong lòng tên dài hơn (ví dụ "lak"
    trong "dak lak") không được tính là một mention riêng. Kết quả
    dedupe theo code, giữ thứ tự xuất hiện.
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
    kept: list[Match] = []
    max_end = -1
    for match in matches:
        if not _is_word_boundary(text, match.start, match.end):
            continue
        if match.start < max_end:
            continue  # Chồng lấn với match dài hơn đã giữ.
        kept.append(match)
        max_end = max(max_end, match.end)
    seen: set[str] = set()
    for match in kept:
        unit = match.payload
        if unit is None:
            continue
        converted = _as_admin_unit(unit)
        if converted is None:
            continue
        level = converted.level
        if level not in result:
            continue
        if converted.code in seen:
            continue
        seen.add(converted.code)
        result[level].append(converted)
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
    matched_tokens: list[str] = field(default_factory=list)
    unmatched_tokens: list[str] = field(default_factory=list)
    reason_code: Optional[ReasonCode] = None

    @property
    def province_code(self) -> Optional[str]:
        return self.province.code if self.province else None

    @property
    def status(self) -> AddressStatus:
        return AddressStatus.VERIFIED if self.anchored else AddressStatus.REJECTED


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
    all_tokens = normalize_text(raw_input or "").split()
    matched_tokens: list[str] = []
    for level_units in mentions.values():
        for unit in level_units:
            matched_tokens.extend(unit.normalized_name.split())
    unmatched_tokens = [t for t in all_tokens if t not in matched_tokens]
    if not provinces:
        return AnchorResult(anchored=False, reason="NO_PROVINCE_MENTION",
                            declared_province_code=declared_province_code,
                            matched_tokens=matched_tokens, unmatched_tokens=unmatched_tokens,
                            reason_code=ReasonCode.MISSING_PROVINCE)
    if len(provinces) > 1:
        return AnchorResult(anchored=False, reason="AMBIGUOUS_PROVINCE",
                            declared_province_code=declared_province_code,
                            matched_tokens=matched_tokens, unmatched_tokens=unmatched_tokens,
                            reason_code=ReasonCode.PROVINCE_MISMATCH)
    province = provinces[0]
    if declared_province_code is not None and str(declared_province_code) != str(province.code):
        return AnchorResult(anchored=False, reason="PROVINCE_MISMATCH",
                            declared_province_code=declared_province_code,
                            matched_tokens=matched_tokens, unmatched_tokens=unmatched_tokens,
                            reason_code=ReasonCode.PROVINCE_MISMATCH)
    return AnchorResult(anchored=True, province=province, matched_text=province.name,
                        confidence=1.0, declared_province_code=declared_province_code,
                        matched_tokens=matched_tokens, unmatched_tokens=unmatched_tokens,
                        reason_code=ReasonCode.OK)


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
