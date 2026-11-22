"""Hồi quy cho fix_fuzzy_exact_match_ignores_district + golden_11.

Exact-match phải qua cùng veto tỉnh và huyện như nhánh fuzzy, và
contract phải truyền đúng street / house_number / confidence từ parsed
— nếu không Validator luôn cộng warning MISSING_HOUSE_OR_STREET và
gate hạ VERIFIED thành NEEDS_REVIEW cho địa chỉ đúng hoàn toàn.
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


def _parsed(raw, ward_name="", district_name="", street=None, house_number=None):
    return SimpleNamespace(
        raw_input=raw, ward_name=ward_name, district_name=district_name,
        street=street, house_number=house_number,
    )


def _anchor():
    return SimpleNamespace(anchored=True, province=SimpleNamespace(code="66", name="Đắk Lắk"))


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
                "Bến Nghé", "Quận 1", street="đường Lê Lợi", house_number="12"),
        anchor, index,
    )
    assert len(result) == 1
    assert result[0].address.ward_code == "76002"
    assert result[0].address.district_code == "760"
    assert result[0].address.province_code == "79"


def test_fuzzy_propagates_street_house_and_confidence(monkeypatch):
    """Contract từ fuzzy phải có street/house/confidence, nếu không
    Validator luôn warning MISSING_HOUSE_OR_STREET và gate hạ VERIFIED."""
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
                "Bến Nghé", "Quận 1", street="đường Lê Lợi", house_number="12"),
        anchor, index,
    )
    assert len(result) == 1
    contract = result[0].address
    assert contract.street == "đường Lê Lợi"
    assert contract.house_number == "12"
    assert contract.has_house_or_street is True
    assert contract.confidence == 1.0


def test_fuzzy_infers_street_house_from_raw_when_parsed_misses_them(monkeypatch):
    """Parsed bỏ sót street/house ở dạng "Số 12, đường Lê Lợi" thì
    fuzzy suy lại từ raw, contract vẫn có has_house_or_street=True."""
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
    contract = result[0].address
    assert contract.house_number == "12"
    assert contract.street == "đường Lê Lợi"
    assert contract.has_house_or_street is True


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
