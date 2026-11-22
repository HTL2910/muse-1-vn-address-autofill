"""
InputGuard — hard gate chặn input thiếu thông tin (approach_10).

Contract (theo test suite của repo):
- Constructor nhận inject tên chuẩn: InputGuard(known_provinces=[...],
  known_districts=[...], known_wards=[...]); phần tử có thể là str, dict
  có khóa 'name' hoặc object có thuộc tính .name. Không inject vẫn chạy
  bằng fallback tiền tố hành chính có ranh giới từ.
- check(raw, parsed=None) -> GuardResult có .decision (GuardDecision),
  .passed, .reason_code, .levels. GuardResult so sánh bằng được với
  GuardDecision (== và .value) để caller cũ không vỡ.
- reason_code là ReasonStr: một str có thêm thuộc tính .value trả về
  chính nó. Lý do: PipelineResult.reject(stage, reason) trong schemas.py
  truy cập reason.value — truyền str thuần vào đó gây
  AttributeError: 'str' object has no attribute 'value' và làm sập toàn
  bộ đường reject của golden/pipeline-smoke. ReasonStr làm cho cả hai
  kiểu caller (dùng str, dùng .value) đều đúng mà không đổi ngữ nghĩa.
- Đếm cấp hành chính CHỈ khi có bằng chứng trong raw:
  * tên chuẩn khớp ở ranh giới từ (không khớp substring/một phần từ),
    khớp được cả dạng có dấu và không dấu,
  * tên nằm NGOÀI đoạn tên đường (đoạn chứa đường/phố/ngõ... bị loại),
  * hoặc tiền tố hành chính đứng đầu một đoạn ngăn bởi dấu phẩy.
- Trường parsed (parser đoán) chỉ được tính khi tên của nó xuất hiện thật
  trong raw ngoài đoạn tên đường — parser đoán xã cho input 'Xom' không
  tạo được bằng chứng.
- Quyết định: >= 2 cấp -> PASS; 1 cấp -> NEED_CLARIFICATION;
  0 cấp -> REJECTED. Input chỉ có số / rỗng -> REJECTED.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum


class GuardDecision(str, Enum):
    PASS = "PASS"
    NEED_CLARIFICATION = "NEED_CLARIFICATION"
    REJECTED = "REJECTED"


class ReasonStr(str):
    """str có .value — tương thích caller kỳ vọng enum lẫn caller dùng str."""

    @property
    def value(self) -> str:
        return str(self)


@dataclass
class GuardResult:
    decision: GuardDecision
    reason_code: ReasonStr = ReasonStr("OK")
    levels: tuple = ()
    matched: dict = field(default_factory=dict)
    raw_input: str = ""

    def __post_init__(self):
        if not isinstance(self.reason_code, ReasonStr):
            self.reason_code = ReasonStr(self.reason_code)

    @property
    def passed(self) -> bool:
        return self.decision == GuardDecision.PASS

    @property
    def reason(self) -> ReasonStr:
        return self.reason_code

    @property
    def value(self) -> str:
        return self.decision.value

    def __eq__(self, other):
        if isinstance(other, GuardDecision):
            return self.decision == other
        if isinstance(other, str):
            return self.decision.value == other
        if isinstance(other, GuardResult):
            return self.decision == other.decision
        return NotImplemented

    def __hash__(self):
        return hash(self.decision)

    def __bool__(self):
        return self.passed


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", (text or "")).strip().lower()


def _plain(text: str) -> str:
    """Bỏ dấu tiếng Việt để khớp không dấu ('cuor dang' ~ 'cuôr đăng')."""
    text = _norm(text).replace("đ", "d")
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _strip_prefix(name_norm: str) -> str:
    return re.sub(
        r"^(thanh pho|thành phố|tinh|tỉnh|quan|quận|huyen|huyện|thi xa|thị xã|"
        r"phuong|phường|xa|xã|thi tran|thị trấn)\s+", "", name_norm).strip()


def _extract_name(item) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("name") or item.get("label") or "")
    return str(getattr(item, "name", "") or "")


_STREET_HINT = re.compile(
    r"\b(đường|duong|phố|pho|ngõ|ngo|ngách|hẻm|hem|quốc lộ|quoc lo|"
    r"tỉnh lộ|tinh lo|đại lộ|dai lo|xa lộ|xa lo)\b")
_NUMBER_ONLY = re.compile(r"^[\d\s./-]+$")

_PREFIX_LEVELS = (
    ("province", re.compile(r"^(tỉnh|tinh)\s+.+")),
    ("district", re.compile(r"^(quận|quan|huyện|huyen|thị xã|thi xa)\s+.+")),
    ("ward", re.compile(r"^(phường|phuong|xã|xa|thị trấn|thi tran)\s+.+")),
)


def _found_in(name_variants: set, segment_variants: list) -> bool:
    """Khớp ranh giới từ: thử mọi biến thể (có dấu/không dấu) của tên
    trên mọi biến thể của đoạn."""
    for name in name_variants:
        if not name:
            continue
        pattern = r"(?<!\w)" + re.escape(name) + r"(?!\w)"
        for seg in segment_variants:
            if re.search(pattern, seg):
                return True
    return False


def _parsed_field(parsed, field_name: str):
    if parsed is None:
        return None, None
    if isinstance(parsed, dict):
        value = parsed.get(field_name)
    else:
        value = getattr(parsed, field_name, None)
    if value is None:
        return None, None
    if isinstance(value, str):
        return value, None
    inner = getattr(value, "value", None)
    span = getattr(value, "raw_span", None) or getattr(value, "evidence", None)
    return inner, span


class InputGuard:
    def __init__(self, known_provinces=None, known_districts=None,
                 known_wards=None, **_ignored):
        raw_known = {
            "province": [_extract_name(n) for n in (known_provinces or [])],
            "district": [_extract_name(n) for n in (known_districts or [])],
            "ward": [_extract_name(n) for n in (known_wards or [])],
        }
        # Mỗi tên chuẩn -> tập biến thể để khớp: dạng gốc, bỏ tiền tố,
        # và cả hai dạng sau khi bỏ dấu.
        self._variants: dict[str, list[set]] = {}
        for level, names in raw_known.items():
            variants = []
            for name in names:
                if not name:
                    continue
                forms = {_norm(name), _strip_prefix(_norm(name)),
                         _plain(name), _strip_prefix(_plain(name))}
                variants.append({f for f in forms if f})
            self._variants[level] = variants

    # -- phân đoạn ---------------------------------------------------------- #
    @staticmethod
    def _segment_variants(raw_norm: str, raw_plain: str) -> list:
        """Trả list cặp (norm, plain) cho từng đoạn, đã loại đoạn tên đường."""
        norm_segments = [s.strip() for s in re.split(r"[,;]", raw_norm) if s.strip()]
        plain_segments = [s.strip() for s in re.split(r"[,;]", raw_plain) if s.strip()]
        pairs = list(zip(norm_segments, plain_segments))
        return [pair for pair in pairs
                if not _STREET_HINT.search(pair[0])
                and not _STREET_HINT.search(pair[1])]

    # -- đếm cấp từ raw ------------------------------------------------------ #
    def _levels_from_raw(self, raw_norm: str, raw_plain: str) -> dict:
        found: dict[str, str] = {}
        pairs = self._segment_variants(raw_norm, raw_plain)
        segment_variants = [[n, p] for n, p in pairs]
        for level in ("province", "district", "ward"):
            for variants in self._variants[level]:
                flat = [seg for pair in segment_variants for seg in pair]
                if _found_in(variants, flat):
                    found[level] = sorted(variants)[0]
                    break
        for norm_seg, plain_seg in pairs:
            for level, pattern in _PREFIX_LEVELS:
                if level not in found and (pattern.match(norm_seg)
                                           or pattern.match(plain_seg)):
                    found[level] = norm_seg
                    break
            if "district" not in found and (
                    norm_seg.startswith("thành phố ")
                    or plain_seg.startswith("thanh pho ")):
                city = norm_seg[len("thành phố "):].strip()
                province_hit = any(
                    _found_in(variants, [city, _plain(city)])
                    for variants in self._variants["province"])
                if province_hit:
                    found.setdefault("province", norm_seg)
                else:
                    found["district"] = norm_seg
        return found

    # -- đếm cấp từ parsed, chỉ khi có bằng chứng raw ------------------------ #
    def _levels_from_parsed(self, parsed, raw_norm: str, raw_plain: str,
                            already: dict) -> dict:
        found = dict(already)
        pairs = self._segment_variants(raw_norm, raw_plain)
        flat = [seg for pair in pairs for seg in pair]
        for level in ("province", "district", "ward"):
            if level in found:
                continue
            value, span = _parsed_field(parsed, level)
            if not value:
                continue
            value_norm = _norm(str(value))
            variants = {value_norm, _strip_prefix(value_norm),
                        _plain(str(value)), _strip_prefix(_plain(str(value)))}
            evidence_ok = False
            if span and (_norm(str(span)) in raw_norm
                         or _plain(str(span)) in raw_plain):
                evidence_ok = True
            elif _found_in({v for v in variants if v}, flat):
                evidence_ok = True
            if evidence_ok:
                found[level] = str(value)
        return found

    # -- API chính ------------------------------------------------------------ #
    def check(self, raw_input, parsed=None, **kwargs) -> GuardResult:
        if parsed is None and "parsed_address" in kwargs:
            parsed = kwargs["parsed_address"]
        raw = raw_input if isinstance(raw_input, str) else str(raw_input or "")
        raw_norm = _norm(raw)
        raw_plain = _plain(raw)
        if not raw_norm:
            return GuardResult(GuardDecision.REJECTED,
                               ReasonStr("INSUFFICIENT_INFO"), (), {}, raw)
        if _NUMBER_ONLY.match(raw_norm):
            return GuardResult(GuardDecision.REJECTED,
                               ReasonStr("NUMBER_ONLY"), (), {}, raw)
        matched = self._levels_from_raw(raw_norm, raw_plain)
        if parsed is not None:
            matched = self._levels_from_parsed(parsed, raw_norm, raw_plain, matched)
        levels = tuple(
            level for level in ("province", "district", "ward") if level in matched)
        if len(levels) >= 2:
            return GuardResult(GuardDecision.PASS, ReasonStr("OK"),
                               levels, matched, raw)
        if len(levels) == 1:
            return GuardResult(GuardDecision.NEED_CLARIFICATION,
                               ReasonStr("MISSING_ADMIN_LEVELS"),
                               levels, matched, raw)
        tokens = [t for t in re.split(r"\s+", raw_norm) if t]
        reason = "TOO_SHORT" if len(tokens) <= 2 and len(raw_norm) < 12 \
            else "INSUFFICIENT_INFO"
        return GuardResult(GuardDecision.REJECTED, ReasonStr(reason),
                           (), matched, raw)

    def evaluate(self, raw_input, parsed=None, **kwargs) -> GuardResult:
        return self.check(raw_input, parsed=parsed, **kwargs)
