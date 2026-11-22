"""
approach_04 — Parser địa chỉ có cấu trúc Rule-based kết hợp NER nhẹ.

Nguyên tắc fail-closed (bắt buộc, từ đề bài gốc):
- KHÔNG tự suy ra tỉnh khi trường tỉnh trống.
- KHÔNG điền mặc định bất kỳ địa chỉ nào khi không đọc được
  (chống tái diễn ca "Xã Đăk Yă, Mang Yang, Gia Lai").
- Các trường mâu thuẫn -> đánh dấu conflict, không tự chọn một trường.
- Input quá ngắn / quá mơ hồ ("73", "127", "bi", "Xom") -> confidence thấp,
  is_sufficient=False để Confidence Gate ở hạ lưu chặn, parser không đoán.

Tầng 1: regex có thứ tự + từ điển, ưu tiên từ chi tiết -> tổng quát.
Tầng 2: hook NER nhẹ (CRF / transformer / LLM nhỏ) qua Protocol `NerExtractor`,
chỉ dùng để TRÍCH XUẤT, không dùng để chốt địa chỉ. Mặc định không có model
thì tầng 2 bị tắt, hệ thống vẫn chạy thuần rule-based.

Module này là logic thuần (domain), không phụ thuộc provider / DB / framework.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Protocol, Sequence

# --------------------------------------------------------------------------- #
# Chuẩn hóa tiếng Việt
# --------------------------------------------------------------------------- #

_ABBREVIATIONS: dict[str, str] = {
    "tp.": "thành phố ", "tp ": "thành phố ", "t.p": "thành phố ",
    "tx.": "thị xã ", "tx ": "thị xã ",
    "q.": "quận ", "q ": "quận ",
    "h.": "huyện ", "h ": "huyện ",
    "p.": "phường ", "p ": "phường ",
    "x.": "xã ", "x ": "xã ",
    "tt.": "thị trấn ", "tt ": "thị trấn ",
    "đ.": "đường ", "đ ": "đường ",
    "dak": "đắk", "dac": "đắk", "daklak": "đắk lắk",
    "tphcm": "thành phố hồ chí minh", "tp hcm": "thành phố hồ chí minh",
    "hcm": "hồ chí minh", "hn": "hà nội", "dn": "đà nẵng",
}

# Từ khóa hành chính, sắp xếp dài -> ngắn để tránh khớp một phần.
_ADMIN_KEYWORDS = (
    ("thành phố trực thuộc trung ương", "province"),
    ("thành phố", "province_or_district"),
    ("thị trấn", "ward"),
    ("thị xã", "district"),
    ("phường", "ward"),
    ("huyện", "district"),
    ("quận", "district"),
    ("tỉnh", "province"),
    ("xã", "ward"),
    ("thôn", "hamlet"),
    ("xóm", "hamlet"),
    ("ấp", "hamlet"),
    ("bản", "hamlet"),
    ("khu phố", "hamlet"),
    ("tổ dân phố", "hamlet"),
)


def strip_accents(text: str) -> str:
    """Bỏ dấu tiếng Việt, giữ 'đ' -> 'd' để so khớp không dấu."""
    text = text.replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_text(raw: str) -> str:
    """Chuẩn hóa: lowercase, gộp khoảng trắng, mở rộng viết tắt phổ biến."""
    text = unicodedata.normalize("NFC", raw or "")
    text = text.strip().lower()
    text = re.sub(r"[,;|]+", ", ", text)
    text = re.sub(r"\s+", " ", text)
    for abbr, full in sorted(_ABBREVIATIONS.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(abbr, full)
    return re.sub(r"\s+", " ", text).strip(" ,")


# --------------------------------------------------------------------------- #
# Kết quả parse
# --------------------------------------------------------------------------- #

@dataclass
class FieldValue:
    value: str | None = None
    confidence: float = 0.0
    source: str = "none"  # rule | dictionary | ner | none
    raw_span: str | None = None


@dataclass
class ParseConflict:
    field_name: str
    candidates: list[str]
    reason: str


@dataclass
class ParsedAddress:
    raw: str
    normalized: str
    house_number: FieldValue = field(default_factory=FieldValue)
    street: FieldValue = field(default_factory=FieldValue)
    hamlet: FieldValue = field(default_factory=FieldValue)
    ward: FieldValue = field(default_factory=FieldValue)
    district: FieldValue = field(default_factory=FieldValue)
    province: FieldValue = field(default_factory=FieldValue)
    note: FieldValue = field(default_factory=FieldValue)
    conflicts: list[ParseConflict] = field(default_factory=list)
    unmatched_tokens: list[str] = field(default_factory=list)

    @property
    def overall_confidence(self) -> float:
        """Điểm tổng hợp có trọng số: cấp hành chính quan trọng hơn số nhà."""
        weights = {"province": 0.30, "district": 0.25, "ward": 0.25,
                   "street": 0.10, "house_number": 0.05, "hamlet": 0.05}
        fields = {"province": self.province, "district": self.district,
                  "ward": self.ward, "street": self.street,
                  "house_number": self.house_number, "hamlet": self.hamlet}
        return round(sum(weights[k] * fields[k].confidence for k in weights), 4)

    @property
    def is_sufficient(self) -> bool:
        """
        Đủ thông tin tối thiểu để đi tiếp pipeline hay không.
        Thiếu tỉnh HOẶC thiếu cả huyện lẫn xã -> không đủ. Có xung đột -> không đủ.
        Đây là chốt chặn các ca '73', '127', 'bi', 'Xom'.
        """
        if self.conflicts:
            return False
        if not self.province.value:
            return False
        if not self.ward.value and not self.district.value:
            return False
        return self.overall_confidence >= 0.45

    def to_dict(self) -> dict:
        def fv(f: FieldValue) -> dict:
            return {"value": f.value, "confidence": f.confidence, "source": f.source}
        return {
            "raw": self.raw, "normalized": self.normalized,
            "house_number": fv(self.house_number), "street": fv(self.street),
            "hamlet": fv(self.hamlet), "ward": fv(self.ward),
            "district": fv(self.district), "province": fv(self.province),
            "note": fv(self.note),
            "conflicts": [{"field": c.field_name, "candidates": c.candidates,
                           "reason": c.reason} for c in self.conflicts],
            "unmatched_tokens": self.unmatched_tokens,
            "overall_confidence": self.overall_confidence,
            "is_sufficient": self.is_sufficient,
        }


# --------------------------------------------------------------------------- #
# Tầng 2: NER nhẹ (tùy chọn, chỉ trích xuất)
# --------------------------------------------------------------------------- #

class NerExtractor(Protocol):
    """Implement bằng CRF / transformer nhẹ / LLM nhỏ ở infrastructure."""
    def extract(self, normalized_text: str) -> dict[str, str]:
        """Trả về {field_name: value} trích xuất được, không kèm quyết định chốt."""
        ...


# --------------------------------------------------------------------------- #
# Parser chính
# --------------------------------------------------------------------------- #

_SEGMENT_SPLIT = re.compile(r"\s*,\s*")
_HOUSE_STREET = re.compile(
    r"^(?:số\s*)?(?P<house>\d+[a-z]?(?:[/\-]\d+[a-z]?)?)?\s*(?:đường\s+)?(?P<street>.+)$"
)
_NUMBER_ONLY = re.compile(r"^\d{1,4}[a-z]?$")


class StructuredAddressParser:
    """
    Tham số:
    - known_provinces / known_districts / known_wards: tên chuẩn (đã lowercase,
      có dấu) từ Canonical Admin DB, do infrastructure nạp và truyền vào.
      Domain không tự chứa dữ liệu hành chính để tránh lệch sau sáp nhập 2025.
    - ner: extractor tầng 2 tùy chọn.
    """

    def __init__(
        self,
        known_provinces: Sequence[str] = (),
        known_districts: Sequence[str] = (),
        known_wards: Sequence[str] = (),
        ner: NerExtractor | None = None,
    ) -> None:
        self._provinces = {normalize_text(p): p for p in known_provinces}
        self._districts = {normalize_text(d): d for d in known_districts}
        self._wards = {normalize_text(w): w for w in known_wards}
        self._ner = ner

    # -- khớp từ điển không dấu / có dấu ------------------------------------ #
    @staticmethod
    def _match_dictionary(segment: str, dictionary: dict[str, str]) -> str | None:
        if segment in dictionary:
            return dictionary[segment]
        seg_plain = strip_accents(segment)
        for key, original in dictionary.items():
            if strip_accents(key) == seg_plain:
                return original
        return None

    def parse(self, raw: str) -> ParsedAddress:
        normalized = normalize_text(raw or "")
        result = ParsedAddress(raw=raw or "", normalized=normalized)
        if not normalized:
            return result

        segments = [s.strip() for s in _SEGMENT_SPLIT.split(normalized) if s.strip()]
        # Địa chỉ VN thường viết chi tiết -> tổng quát, nhưng cũng có người viết
        # ngược lại. Quét mọi segment cho mọi cấp, ghi nhận xung đột nếu 1 cấp
        # có >= 2 giá trị khác nhau.
        found: dict[str, list[tuple[str, str]]] = {
            "province": [], "district": [], "ward": [], "hamlet": [],
        }
        consumed: set[int] = set()

        for idx, seg in enumerate(segments):
            matched = self._classify_segment(seg, found)
            if matched:
                consumed.add(idx)

        self._assign(result, found)
        self._parse_detail_segments(result, segments, consumed)
        if self._ner is not None:
            self._apply_ner(result, normalized)
        self._detect_conflicts(result, found)
        result.unmatched_tokens = [
            seg for i, seg in enumerate(segments)
            if i not in consumed and seg not in (result.note.value or "")
        ]
        self._guard_insufficient(result, normalized)
        return result

    def _classify_segment(self, seg: str, found: dict) -> bool:
        """Phân loại 1 segment vào cấp hành chính. Trả True nếu đã nhận diện."""
        for keyword, level in _ADMIN_KEYWORDS:
            if seg.startswith(keyword + " "):
                name = seg[len(keyword):].strip()
                full = f"{keyword} {name}"
                if level == "province":
                    hit = self._match_dictionary(full, self._provinces) or full
                    found["province"].append((hit, seg)); return True
                if level == "district":
                    hit = self._match_dictionary(full, self._districts) or full
                    found["district"].append((hit, seg)); return True
                if level == "ward":
                    hit = self._match_dictionary(full, self._wards) or full
                    found["ward"].append((hit, seg)); return True
                if level == "hamlet":
                    found["hamlet"].append((full, seg)); return True
                if level == "province_or_district":
                    # "thành phố X": nếu X là tỉnh -> province, nếu là TP thuộc
                    # tỉnh (Biên Hòa, Thủ Dầu Một...) -> district. Không chắc
                    # thì ghi cả hai dạng ứng viên và để conflict xử lý.
                    if self._match_dictionary(full, self._provinces):
                        found["province"].append((full, seg))
                    else:
                        found["district"].append((full, seg))
                    return True
        # Segment không có tiền tố: thử khớp thẳng từ điển (vd "đắk lắk").
        for level, dictionary in (("province", self._provinces),
                                  ("district", self._districts),
                                  ("ward", self._wards)):
            hit = self._match_dictionary(seg, dictionary)
            if hit:
                found[level].append((hit, seg)); return True
        return False

    @staticmethod
    def _assign(result: ParsedAddress, found: dict) -> None:
        conf = {"dictionary": 0.9, "rule": 0.75}
        for name in ("province", "district", "ward", "hamlet"):
            if found[name]:
                value, span = found[name][0]
                fv = getattr(result, name)
                fv.value, fv.raw_span = value, span
                fv.confidence, fv.source = conf["dictionary"], "dictionary"

    def _parse_detail_segments(self, result: ParsedAddress,
                               segments: list[str], consumed: set[int]) -> None:
        """Segment chưa tiêu thụ đầu tiên thường là số nhà + đường / ghi chú."""
        for i, seg in enumerate(segments):
            if i in consumed:
                continue
            if _NUMBER_ONLY.match(seg):
                # "73", "127": chỉ là số, KHÔNG đủ làm số nhà đáng tin và
                # tuyệt đối không suy ra địa chỉ từ nó.
                result.note = FieldValue(seg, 0.2, "rule", seg)
                continue
            m = _HOUSE_STREET.match(seg)
            if m and (m.group("street") or "").strip():
                if m.group("house"):
                    result.house_number = FieldValue(m.group("house"), 0.7, "rule", seg)
                street = (m.group("street") or "").strip()
                if street and len(street.split()) >= 1 and not result.street.value:
                    result.street = FieldValue(street, 0.6, "rule", seg)
            consumed.add(i)
            break

    def _apply_ner(self, result: ParsedAddress, normalized: str) -> None:
        """NER chỉ ĐIỀN VÀO Ô TRỐNG với confidence thấp hơn rule/dictionary."""
        try:
            extracted = self._ner.extract(normalized)  # type: ignore[union-attr]
        except Exception:
            return  # NER lỗi không được làm sập pipeline, và không đoán thay.
        for name, value in (extracted or {}).items():
            fv = getattr(result, name, None)
            if isinstance(fv, FieldValue) and not fv.value and value:
                fv.value, fv.confidence, fv.source = str(value), 0.55, "ner"

    @staticmethod
    def _detect_conflicts(result: ParsedAddress, found: dict) -> None:
        for name in ("province", "district", "ward"):
            values = sorted({v for v, _ in found[name]})
            if len(values) > 1:
                result.conflicts.append(ParseConflict(
                    name, values,
                    "Nhiều giá trị khác nhau cho cùng một cấp hành chính; "
                    "cần khách/nhân viên xác nhận, không tự chọn."))

    @staticmethod
    def _guard_insufficient(result: ParsedAddress, normalized: str) -> None:
        """Hạ confidence khi input quá ngắn — chốt chặn ca 'bi', 'Xom', '73'."""
        tokens = [t for t in re.split(r"\s+", normalized) if t]
        meaningful = [t for t in tokens if len(strip_accents(t)) >= 2]
        if len(tokens) <= 1 or len(meaningful) == 0:
            for fv in (result.province, result.district, result.ward,
                       result.street, result.house_number):
                fv.confidence = min(fv.confidence, 0.2)
