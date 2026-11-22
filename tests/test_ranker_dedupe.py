"""Hồi quy cho golden_11: ranker phải dedupe cùng một xã từ nhiều nguồn.

Cùng xã 76002 (Bến Nghé, Quận 1, TP HCM) được trả về 2-3 lần với điểm 1.0
từ parser / fuzzy / geocoder thì không được tính là 2 ứng viên cạnh tranh:
top1 và top2 cùng xã làm margin = 0 và gate hạ VERIFIED thành NEEDS_REVIEW.
Sau dedupe, chỉ còn 1 ứng viên, margin = điểm top1, và xã khác thật sự
vẫn giữ nguyên margin top1 - top2.
"""
from __future__ import annotations

from types import SimpleNamespace

from src.address.domain.ranker import Ranker
from src.address.domain.schemas import AddressCandidate, AddressContract, ProviderSource


def _candidate(ward_code, district_code="760", province_code="79",
               provider=ProviderSource.INTERNAL_ADMIN_DB, score=1.0):
    contract = AddressContract(
        raw_input="Số 12, đường Lê Lợi, Phường Bến Nghé, Quận 1, Thành phố Hồ Chí Minh",
        province="Hồ Chí Minh", district="Quận 1", ward="Bến Nghé",
        street="đường Lê Lợi", house_number="12",
        province_code=province_code, district_code=district_code, ward_code=ward_code,
        source=provider,
    )
    return AddressCandidate(address=contract, provider=provider,
                            provider_score=score, rank_score=score)


def _anchor():
    return SimpleNamespace(anchored=True, province=SimpleNamespace(code="79", name="Hồ Chí Minh"))


def test_same_ward_from_multiple_sources_collapses_to_one():
    cands = [
        _candidate("76002", provider=ProviderSource.INTERNAL_ADMIN_DB, score=1.0),
        _candidate("76002", provider=ProviderSource.GOONG, score=1.0),
        _candidate("76002", provider=ProviderSource.PANCAKE, score=0.95),
    ]
    result = Ranker().rank(cands, _anchor())
    assert len(result.ranked) == 1
    assert result.top1.address.ward_code == "76002"
    assert result.top2 is None
    assert result.margin == 1.0


def test_distinct_wards_keep_real_margin():
    cands = [
        _candidate("76002", score=1.0),
        _candidate("76003", score=0.9),
    ]
    result = Ranker().rank(cands, _anchor())
    assert len(result.ranked) == 2
    assert result.top1.address.ward_code == "76002"
    assert result.top2.address.ward_code == "76003"
    assert result.margin == 0.1


def test_province_mismatch_still_vetoed():
    cands = [
        _candidate("76002", score=1.0),
        _candidate("12345", province_code="66", district_code="664", score=1.0),
    ]
    result = Ranker().rank(cands, _anchor())
    assert [c.address.ward_code for c in result.ranked] == ["76002"]
    assert result.margin == 1.0
