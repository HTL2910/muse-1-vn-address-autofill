"""Structured parser — approach_04, canonical cho fix_missing_pipeline_core.

Tách raw_input thành số nhà / đường / thôn-xóm / xã / huyện / tỉnh
mà không đoán: đoạn nào không phân loại được thì để unmatched, tầng
hạ lưu không được tự điền từ đoạn đó.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schemas import normalize_text

_HOUSE_RE = re.compile(r"^(\d+[A-Za-z]?)\s*(.*)$")
_PREFIX_RE = re.compile(
    r"^(tỉnh|thành phố|tp\.?|quận|huyện|thị trấn|tt\.?|xã|phường|thôn|xóm|đường|ngõ|hẻm)\s+",
    re.IGNORECASE,
)


def _strip_prefix(segment: str) -> tuple[str, str]:
    m = _PREFIX_RE.match(segment.strip())
    if not m:
        return "", segment.strip()
    return m.group(1).lower(), segment[m.end():].strip()


@dataclass
class ParsedAddress:
    raw_input: str
    normalized: str = ""
    house_number: str = ""
    street: str = ""
    hamlet: str = ""
    ward_name: str = ""
    district_name: str = ""
    province_name: str = ""
    tokens: list[str] = field(default_factory=list)
    matched_tokens: list[str] = field(default_factory=list)
    unmatched_tokens: list[str] = field(default_factory=list)

    @property
    def admin_level_count(self) -> int:
        return sum(bool(x) for x in [self.province_name, self.district_name, self.ward_name])


def parse_address(raw_input: str) -> ParsedAddress:
    parsed = ParsedAddress(raw_input=raw_input, normalized=normalize_text(raw_input))
    parsed.tokens = parsed.normalized.split() if parsed.normalized else []
    segments = [s.strip() for s in (raw_input or "").split(",") if s.strip()]
    if not segments:
        return parsed
    unclassified: list[str] = []
    for seg in segments:
        prefix, rest = _strip_prefix(seg)
        if prefix in ("tỉnh", "thành phố", "tp", "tp."):
            parsed.province_name = rest
        elif prefix in ("quận", "huyện", "thị trấn", "tt", "tt."):
            parsed.district_name = rest
        elif prefix in ("xã", "phường"):
            parsed.ward_name = rest
        elif prefix in ("thôn", "xóm"):
            parsed.hamlet = rest
        elif prefix in ("đường", "ngõ", "hẻm"):
            parsed.street = rest
        else:
            unclassified.append(seg)
    # Phân loại theo vị trí cho các đoạn không có tiền tố: đoạn cuối là
    # tỉnh, kế cuối là huyện, kế nữa là xã — chỉ khi chưa có tên đó.
    if unclassified:
        rev = list(reversed(unclassified))
        if len(rev) >= 1 and not parsed.province_name and len(segments) >= 2:
            # Đoạn cuối không có số nhà thì coi là tỉnh; đoạn có số nhà thì không.
            if not re.search(r"\d", rev[0]):
                parsed.province_name = _strip_prefix(rev[0])[1]
                rev = rev[1:]
        if rev and not parsed.district_name and len(segments) >= 3:
            if not re.search(r"\d", rev[0]):
                parsed.district_name = _strip_prefix(rev[0])[1]
                rev = rev[1:]
        if rev and not parsed.ward_name and len(segments) >= 3:
            if not re.search(r"\d", rev[0]):
                parsed.ward_name = _strip_prefix(rev[0])[1]
                rev = rev[1:]
        for seg in rev:
            m = _HOUSE_RE.match(seg.strip())
            if m and not parsed.house_number:
                parsed.house_number = m.group(1)
                if m.group(2):
                    parsed.street = m.group(2)
            elif not parsed.street:
                parsed.street = seg.strip()
            else:
                parsed.unmatched_tokens.append(seg)
    for name in [parsed.province_name, parsed.district_name, parsed.ward_name,
                 parsed.street, parsed.hamlet]:
        if name:
            parsed.matched_tokens.extend(normalize_text(name).split())
    return parsed
