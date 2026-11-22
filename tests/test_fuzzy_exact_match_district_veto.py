"""Hồi quy cho fix_fuzzy_exact_match_ignores_district.

Exact-match phải qua cùng veto tỉnh và huyện như nhánh fuzzy:
- xã trùng tên ở huyện khác trong cùng tỉnh, khi huyện đã neo, phải bị veto;
- xã có parent_code=None phải bị veto, không được ép vào tỉnh đã neo;
- xã ở tỉnh khác phải bị veto;
- xã đúng huyện vẫn trả về với score 1.0, chỉ một ứng viên duy nhất;
- veto chỉ bằng parent_code, không bằng level: unit có level="phuong"/
  "quan" (không phải chuỗi "ward"/"district") mà parent chain đúng
  vẫn phải được trả về — đây là hồi quy cho lần fail golden_11.

Test dùng SimpleNamespace cho AdminUnit/AdminIndex/ParsedAddress/AnchorResult
để không phụ thuộc vào signature constructor của các dataclass đó; chỉ dùng
đúng các thuộc tính mà FuzzyMatcher.match đọc: code, name, normalized_name,
level, parent_code, units, inverted, districts_of, wards_of, raw_input,
ward_name, district_name, anchored, province.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.address.domain.fuzzy_matcher as fm
from src.address.domain.fuzzy_matcher import FuzzyMatcher
from src.address.domain.schemas import normalize_text


def _unit(code, name, level, parent_code):
    return SimpleNamespace(
        code=code, name=name, normalized_name=normalize_text(name),
        level=level, parent_code=parent_code,
    )


def _index(units, inverted, districts, wards_by_district):
    return SimpleNamespace(
        units={u.code: u for u in units},
        inverted=inverted,
        districts_of=lambda pc: [d for d in districts if d.parent_code == pc],
        wards_of=lambda dc: wards_by_district.get(dc, []),
    )


def _parsed(raw, ward_name="", district_name=""):
    return SimpleNamespace(raw_input=raw, ward_name=ward_name, district_name=district_name)


def _anchor():
    return SimpleNamespace(anchored=True, province=SimpleNamespace(code="66", name="Đắk Lắk"))


# Tỉnh 66 Đắk Lắk, huyện 664 Krông Búk, huyện 665 Krông Pắc.
D1 = _unit("664", "Krông Búk", "district", "66")
D2 = _unit("665", "Krông Pắc", "district", "66")
W_RIGHT = _unit("66401", "Cuôr Đăng", "ward", "664")
W_WRONG_DISTRICT = _unit("66501", "Cuôr Đăng", "ward", "665")


def test_exact_ward_in_other_district_is_vetoed(monkeypatch):
    """Huyện đã neo 664, mention exact chỉ ra xã 66501 -> không được trả 66501."""
    monkeypatch.setattr(
        fm, "extract_admin_mentions",
        lambda raw, index: {"ward": [W_WRONG_DISTRICT], "district": [D1]},
    )
    index = _index(
        [D1, D2, W_RIGHT, W_WRONG_DISTRICT],
        {normalize_text("Cuôr Đăng"): [W_WRONG_DISTRICT, W_RIGHT]},
        [D1, D2],
        {"664": [W_RIGHT], "665": [W_WRONG_DISTRICT]},
    )
    result = FuzzyMatcher().match(
        _parsed("xã Cuôr Đăng, Krông Búk, Đắk Lắk", "Cuôr Đăng", "Krông Búk"),
        _anchor(), index,
    )
    assert [c.address.ward_code for c in result] == ["66401"]


def test_exact_ward_with_no_parent_is_vetoed(monkeypatch):
    """Ward parent_code=None không chứng minh được thuộc tỉnh đã neo -> veto."""
    orphan = _unit("66499", "Xa La", "ward", None)
    monkeypatch.setattr(
        fm, "extract_admin_mentions",
        lambda raw, index: {"ward": [orphan], "district": [D1]},
    )
    index = _index(
        [D1, orphan],
        {normalize_text("Xa La"): [orphan]},
        [D1],
        {"664": []},
    )
    result = FuzzyMatcher().match(
        _parsed("Xa La, Krông Búk, Đắk Lắk", "Xa La", "Krông Búk"),
        _anchor(), index,
    )
    assert all(c.address.ward_code != "66499" for c in result)
    assert result == []


def test_exact_ward_in_other_province_is_vetoed(monkeypatch):
    """Xã thuộc huyện của tỉnh khác (district.parent_code != 66) -> veto."""
    other_district = _unit("671", "Huyện Khác", "district", "67")
    other_ward = _unit("67101", "Cuôr Đăng", "ward", "671")
    monkeypatch.setattr(
        fm, "extract_admin_mentions",
        lambda raw, index: {"ward": [other_ward], "district": []},
    )
    index = _index(
        [D1, other_district, other_ward],
        {normalize_text("Cuôr Đăng"): [other_ward]},
        [D1],
        {"664": []},
    )
    result = FuzzyMatcher().match(
        _parsed("xã Cuôr Đăng, Đắk Lắk", "Cuôr Đăng", ""),
        _anchor(), index,
    )
    assert result == []


def test_exact_match_in_correct_district_still_scores_one(monkeypatch):
    """Trường hợp đúng huyện/tỉnh không bị veto oan, vẫn score 1.0, một ứng viên."""
    monkeypatch.setattr(
        fm, "extract_admin_mentions",
        lambda raw, index: {"ward": [W_RIGHT], "district": [D1]},
    )
    index = _index(
        [D1, D2, W_RIGHT, W_WRONG_DISTRICT],
        {normalize_text("Cuôr Đăng"): [W_RIGHT]},
        [D1, D2],
        {"664": [W_RIGHT], "665": [W_WRONG_DISTRICT]},
    )
    result = FuzzyMatcher().match(
        _parsed("xã Cuôr Đăng, Krông Búk, Đắk Lắk", "Cuôr Đăng", "Krông Búk"),
        _anchor(), index,
    )
    assert len(result) == 1
    assert result[0].address.ward_code == "66401"
    assert result[0].address.district_code == "664"
    assert result[0].address.province_code == "66"
    assert result[0].provider_score == 1.0
    assert result[0].rank_score == 1.0


def test_veto_is_by_parent_code_not_by_level_string(monkeypatch):
    """Hồi quy golden_11: level='phuong'/'quan' vẫn phải match nếu parent chain đúng."""
    d1 = _unit("760", "Quận 1", "quan", "79")
    w1 = _unit("76002", "Bến Nghé", "phuong", "760")
    monkeypatch.setattr(
        fm, "extract_admin_mentions",
        lambda raw, index: {"ward": [w1], "district": [d1]},
    )
    index = _index(
        [d1, w1],
        {normalize_text("Bến Nghé"): [w1]},
        [d1],
        {"760": [w1]},
    )
    anchor = SimpleNamespace(anchored=True, province=SimpleNamespace(code="79", name="Hồ Chí Minh"))
    result = FuzzyMatcher().match(
        _parsed("Số 12, đường Lê Lợi, Phường Bến Nghé, Quận 1, Thành phố Hồ Chí Minh",
                "Bến Nghé", "Quận 1"),
        anchor, index,
    )
    assert len(result) == 1
    assert result[0].address.ward_code == "76002"
    assert result[0].address.district_code == "760"
    assert result[0].address.province_code == "79"


def test_duplicate_mentions_do_not_create_duplicate_candidates(monkeypatch):
    """Cùng một xã được mention 2 lần chỉ trả 1 ứng viên, nếu không margin về 0."""
    monkeypatch.setattr(
        fm, "extract_admin_mentions",
        lambda raw, index: {"ward": [W_RIGHT, W_RIGHT], "district": [D1]},
    )
    index = _index(
        [D1, D2, W_RIGHT, W_WRONG_DISTRICT],
        {normalize_text("Cuôr Đăng"): [W_RIGHT]},
        [D1, D2],
        {"664": [W_RIGHT], "665": [W_WRONG_DISTRICT]},
    )
    result = FuzzyMatcher().match(
        _parsed("xã Cuôr Đăng, Krông Búk, Đắk Lắk", "Cuôr Đăng", "Krông Búk"),
        _anchor(), index,
    )
    assert len(result) == 1
    assert result[0].address.ward_code == "66401"
