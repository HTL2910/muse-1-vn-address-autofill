"""Structured parser — approach_04, canonical.
Fix: đoạn đã gán làm tỉnh/huyện/xã KHÔNG được gán lại làm đường.
Trước đây vòng rev gán street="Gia Lai" cho địa chỉ trần Đăk Yă,
khiến has_house_or_street=True và gate bare-only không kích hoạt.
Đường chỉ lấy từ đoạn có số nhà, đoạn có prefix đường, hoặc đoạn
đầu tiên chưa gán (theo thứ tự gốc, không đảo ngược)."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from .schemas import normalize_text
_HOUSE_RE = re.compile(r"^(\d+[A-Za-z]?)\s*(.*)$")
_PREFIX_RE = re.compile(r"^(tỉnh|thành phố|tp\.?|quận|huyện|thị trấn|tt\.?|xã|phường|thôn|xóm|đường|ngõ|hẻm)\s+", re.IGNORECASE)
def _strip_prefix(segment: str) -> tuple[str, str]:
    m=_PREFIX_RE.match(segment.strip())
    if not m: return "", segment.strip()
    return m.group(1).lower(), segment[m.end():].strip()
@dataclass
class ParsedAddress:
    raw_input: str; normalized: str=""; house_number: str=""; street: str=""; hamlet: str=""
    ward_name: str=""; district_name: str=""; province_name: str=""
    tokens: list[str]=field(default_factory=list); matched_tokens: list[str]=field(default_factory=list); unmatched_tokens: list[str]=field(default_factory=list)
    province_source: str=""; district_source: str=""; ward_source: str=""
    @property
    def admin_level_count(self) -> int: return sum(bool(x) for x in [self.province_name, self.district_name, self.ward_name])
def parse_address(raw_input: str) -> ParsedAddress:
    parsed=ParsedAddress(raw_input=raw_input, normalized=normalize_text(raw_input))
    parsed.tokens=parsed.normalized.split() if parsed.normalized else []
    segments=[s.strip() for s in (raw_input or "").split(",") if s.strip()]
    if not segments: return parsed
    unclassified: list[str]=[]
    for seg in segments:
        prefix, rest=_strip_prefix(seg)
        if prefix in ("tỉnh","thành phố","tp","tp."): parsed.province_name=rest; parsed.province_source="prefix"
        elif prefix in ("quận","huyện","thị trấn","tt","tt."): parsed.district_name=rest; parsed.district_source="prefix"
        elif prefix in ("xã","phường"): parsed.ward_name=rest; parsed.ward_source="prefix"
        elif prefix in ("thôn","xóm"): parsed.hamlet=rest
        elif prefix in ("đường","ngõ","hẻm"): parsed.street=rest
        else: unclassified.append(seg)
    consumed: set[str]=set()
    if unclassified:
        rev=list(reversed(unclassified))
        bare=[s for s in rev if not re.search(r"\d", s) and normalize_text(s) not in ("",)]
        if bare and not parsed.province_name and len(segments)>=2:
            parsed.province_name=_strip_prefix(bare[0])[1]; parsed.province_source="bare_segment"; consumed.add(bare[0]); bare=bare[1:]
        if bare and not parsed.district_name and len(segments)>=3:
            parsed.district_name=_strip_prefix(bare[0])[1]; parsed.district_source="bare_segment"; consumed.add(bare[0]); bare=bare[1:]
        if bare and not parsed.ward_name and len(segments)>=3:
            parsed.ward_name=_strip_prefix(bare[0])[1]; parsed.ward_source="bare_segment"; consumed.add(bare[0]); bare=bare[1:]
        # Đường/nhà: chỉ từ đoạn chưa gán làm cấp hành chính, theo
        # thứ tự gốc (đoạn đầu thường là số nhà/đường).
        for seg in unclassified:
            if seg in consumed: continue
            if normalize_text(seg) in (normalize_text(parsed.province_name), normalize_text(parsed.district_name), normalize_text(parsed.ward_name)) and parsed.province_name:
                continue
            m=_HOUSE_RE.match(seg.strip())
            if m and not parsed.house_number:
                parsed.house_number=m.group(1)
                if m.group(2): parsed.street=m.group(2)
                break
            if not parsed.street and not re.search(r"\d", seg):
                # Đoạn đầu chưa gán mà không có số: có thể là tên đường
                # (vd "Sói 799, đường Lam Sơn" đã có prefix nên không tới đây).
                # Chỉ gán nếu đây là đoạn đầu tiên và còn đoạn hành chính sau nó.
                if seg == unclassified[0] and len(segments) >= 3:
                    parsed.street=seg.strip()
                else:
                    parsed.unmatched_tokens.append(seg)
            else:
                parsed.unmatched_tokens.append(seg)
    for name in [parsed.province_name, parsed.district_name, parsed.ward_name, parsed.street, parsed.hamlet]:
        if name: parsed.matched_tokens.extend(normalize_text(name).split())
    return parsed
