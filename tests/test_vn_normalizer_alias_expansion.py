"""Hồi quy cho fix_normalizer_alias_expansion.

1. Không còn trùng khóa alias (kiểm tra runtime + _build_alias_dict raise).
2. expanded_diacritic được gán đúng biến đã tính (alias + viết tắt),
   không phải fixed_diacritic.
3. Token đơn mơ hồ 'hn' / 'sg' / 'd' không expand khi đứng một mình;
   'd' chỉ expand khi có ngữ cảnh tên đường.
"""
from __future__ import annotations

import pytest

from src.address.domain.vn_normalizer import (
    ABBREVIATIONS,
    PLACE_ALIASES,
    _apply_place_aliases,
    _build_alias_dict,
    check_alias_duplicates,
    normalize,
)


def test_no_duplicate_alias_keys():
    keys = [alias for alias, _ in PLACE_ALIASES]
    assert len(keys) == len(set(keys))
    assert check_alias_duplicates(PLACE_ALIASES) == []
    # 'dak lak' chỉ có một canonical duy nhất, không bị ghi đè im lặng.
    assert PLACE_ALIASES["dak lak"] == "đắk lắk"
    assert PLACE_ALIASES.get("dak lak") == "đắk lắk"
    assert "dak lak" in PLACE_ALIASES


def test_build_alias_dict_raises_on_duplicate():
    with pytest.raises(ValueError):
        _build_alias_dict([("dak lak", "đắk lắk"), ("dak lak", "dak lak")])


def test_expanded_diacritic_uses_alias_result():
    result = normalize("dak lak")
    assert result.expanded_diacritic == "đắk lắk"
    assert result == "đắk lắk"
    assert result.unsigned == "dak lak"


def test_expanded_diacritic_expands_alias_and_abbreviation():
    # fixed: alias đã khôi phục dấu nhưng viết tắt chưa expand.
    # expanded: viết tắt cũng đã expand, khác fixed.
    result = normalize("tp hcm")
    assert result.fixed_diacritic == "tp hồ chí minh"
    assert result.expanded_diacritic == "thành phố hồ chí minh"
    assert result.expanded_diacritic != result.fixed_diacritic
    assert normalize("tphcm").expanded_diacritic == "thành phố hồ chí minh"
    assert normalize("cuor dang, dak lak").expanded_diacritic == "cuôr đăng, đắk lắk"


def test_apply_place_aliases_direct():
    assert _apply_place_aliases("dak lak") == "đắk lắk"
    assert _apply_place_aliases("cuor dang") == "cuôr đăng"
    assert _apply_place_aliases("ben nghe") == "bến nghé"


def test_single_token_hn_sg_d_not_expanded():
    assert normalize("hn").expanded_diacritic == "hn"
    assert normalize("sg").expanded_diacritic == "sg"
    assert normalize("d").expanded_diacritic == "d"
    assert normalize("bi").expanded_diacritic == "bi"
    assert normalize("xom").expanded_diacritic == "xom"
    # 'hn' / 'sg' / 'd' không được map vô điều kiện.
    assert "hn" not in ABBREVIATIONS
    assert "sg" not in ABBREVIATIONS
    assert "d" not in ABBREVIATIONS


def test_contextual_d_expands_before_street_name():
    assert normalize("d le loi").expanded_diacritic == "đường lê lợi"
    assert normalize("12 d le loi").expanded_diacritic == "12 đường lê lợi"
    # 'd' ở cuối, không có tên đường theo sau -> không expand.
    assert normalize("le loi d").expanded_diacritic == "lê lợi d"


def test_unambiguous_abbreviations_still_expand():
    assert normalize("q 1").expanded_diacritic == "quận 1"
    assert normalize("p ben nghe").expanded_diacritic == "phường bến nghé"
    assert normalize("x cuor dang").expanded_diacritic == "xã cuôr đăng"
    assert normalize("tp ho chi minh").expanded_diacritic == "thành phố hồ chí minh"


def test_normalize_returns_str_with_attributes():
    result = normalize("tp hcm")
    assert isinstance(result, str)
    assert result.original == "tp hcm"
    assert result.fixed_diacritic
    assert result.expanded_diacritic
    assert result.unsigned
    assert result.tokens
