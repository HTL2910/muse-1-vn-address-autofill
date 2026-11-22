"""Hồi quy cho fix_aho_corasick_missed_mention.

Lỗi cũ: sau khi đi theo fail-link, ký tự hiện tại bị bỏ qua nên
pattern bắt đầu ngay tại ký tự đó không bao giờ được tìm thấy.
"""
from __future__ import annotations

from src.address.domain.province_anchor import (
    AdminIndex,
    AdminUnit,
    AhoCorasick,
    anchor_province,
    extract_admin_mentions,
)


def _patterns_found(patterns, text):
    return sorted((m.pattern, m.start, m.end) for m in AhoCorasick(patterns).search(text))


def test_reprocess_after_fail_ab_ba():
    # Sau "ab", ký tự 'a' mở ra "ba" ở 1-2. Code cũ chỉ tìm được "ab".
    found = _patterns_found(["ab", "ba"], "aba")
    assert ("ab", 0, 2) in found
    assert ("ba", 1, 3) in found


def test_reprocess_after_fail_aab_ab():
    found = _patterns_found(["aab", "ab"], "aab")
    assert ("aab", 0, 3) in found
    assert ("ab", 1, 3) in found


def test_classic_ushers():
    # "she" và "he" kết thúc cùng vị trí, "hers" tiếp nối ngay sau đó.
    found = _patterns_found(["he", "she", "his", "hers"], "ushers")
    assert ("she", 1, 4) in found
    assert ("he", 2, 4) in found
    assert ("hers", 1, 5) in found
    assert ("his", 0, 0) not in found


def test_suffix_pattern_after_full_match():
    found = _patterns_found(["abc", "bc"], "abc")
    assert ("abc", 0, 3) in found
    assert ("bc", 1, 3) in found


def test_repeated_partial_match_then_new_pattern():
    # "ababa": ab ở 0-2 và 2-4, ba ở 1-3 và 3-5 — không được mất lần nào.
    found = _patterns_found(["ab", "ba"], "ababa")
    assert ("ab", 0, 2) in found
    assert ("ba", 1, 3) in found
    assert ("ab", 2, 4) in found
    assert ("ba", 3, 5) in found


def _index():
    return AdminIndex([
        AdminUnit("66", "Đắk Lắk", level="province"),
        AdminUnit("645", "Lắk", level="district", parent_code="66"),
        AdminUnit("663", "Cư M'gar", level="district", parent_code="66"),
        AdminUnit("66301", "Cuôr Đăng", level="ward", parent_code="663"),
        AdminUnit("79", "Hồ Chí Minh", level="province"),
        AdminUnit("760", "Quận 1", level="district", parent_code="79"),
        AdminUnit("76002", "Bến Nghé", level="ward", parent_code="760"),
    ])


def test_admin_mentions_after_partial_match():
    # "lak" (huyện Lắk) rồi ngay sau đó "dak lak" (tỉnh Đắk Lắk):
    # ký tự đầu của "dak lak" không được mất sau khi khớp "lak".
    mentions = extract_admin_mentions("huyện Lắk, tỉnh Đắk Lắk", _index())
    assert [u.code for u in mentions["district"]] == ["645"]
    assert [u.code for u in mentions["province"]] == ["66"]


def test_admin_mentions_full_address():
    mentions = extract_admin_mentions(
        "127 Nguyễn Trãi, xã Cuôr Đăng, huyện Cư M'gar, tỉnh Đắk Lắk", _index())
    assert [u.code for u in mentions["ward"]] == ["66301"]
    assert [u.code for u in mentions["district"]] == ["663"]
    assert [u.code for u in mentions["province"]] == ["66"]


def test_admin_mentions_hcm_full_address():
    mentions = extract_admin_mentions(
        "Số 12, đường Lê Lợi, Phường Bến Nghé, Quận 1, Thành phố Hồ Chí Minh", _index())
    assert [u.code for u in mentions["ward"]] == ["76002"]
    assert [u.code for u in mentions["district"]] == ["760"]
    assert [u.code for u in mentions["province"]] == ["79"]


def test_word_boundary_no_substring_match():
    # "dak lakx" không được khớp "dak lak".
    mentions = extract_admin_mentions("dak lakx", _index())
    assert mentions["province"] == []
    assert [u.code for u in extract_admin_mentions("dak lak", _index())["province"]] == ["66"]


def test_anchor_province():
    result = anchor_province(
        "xã Cuôr Đăng, huyện Cư M'gar, tỉnh Đắk Lắk", _index())
    assert result.anchored is True
    assert result.province_code == "66"
    assert result.province.name == "Đắk Lắk"


def test_anchor_province_no_mention_not_anchored():
    result = anchor_province("xã Cuôr Đăng", _index())
    assert result.anchored is False
    assert result.province is None


def test_anchor_province_declared_mismatch_not_anchored():
    result = anchor_province(
        "xã Cuôr Đăng, tỉnh Đắk Lắk", _index(), declared_province_code="79")
    assert result.anchored is False
    assert result.reason == "PROVINCE_MISMATCH"
