"""
Minimum Input Guard — approach_10.

Hard gate ở tầng service và API, CHẠY TRƯỚC khi gọi autofill/geocoder.
Nguyên tắc fail-closed: đầu vào không đủ thông tin thì trả về
NEED_CLARIFICATION, không gọi Goong, không cho tạo vận đơn, không điền mò.

Chặn:
  - "73", "127"            : chỉ gồm số
  - "bi", "Bị", "Xom"      : 1-2 âm tiết mơ hồ, dưới ngưỡng ký tự có nghĩa
  - "Sói 799, đường Lam Sơn": chỉ có số nhà + đường, thiếu xã/huyện/tỉnh
  - chuỗi < 10 ký tự chữ, hoặc < 3 ký tự có nghĩa
Yêu cầu tối thiểu: ít nhất 2 cấp hành chính, hoặc tên xã/phường/huyện
rõ ràng kèm tỉnh. Thiếu thì UI yêu cầu bổ sung tỉnh/huyện/xã hoặc
chọn từ danh sách chuẩn (xem AddressDiffConfirm).

Fix fix_input_guard_substring_false_level:
Nhận diện cấp hành chính theo RANH GIỚI TỪ, không substring.
Bản cũ dùng `if name in norm` với tên tỉnh/huyện/xã chuẩn: tên đường,
tên quán, tên địa danh chứa chuỗi con trùng tên tỉnh/xã cũng bị tính
là một cấp hành chính, ví dụ "đường Đắk Lắk" bị tính là tỉnh Đắk Lắk,
khiến input chỉ có số nhà + đường vẫn đủ 2 cấp và được PASS, gọi
geocoder và tạo vận đơn — đúng lỗi gốc mà guard được sinh ra để chặn.

Sau fix:
  - Tên chuẩn chỉ được tính khi khớp thành dãy token liên tiếp hoàn chỉnh
    trong MỘT đoạn ngăn bởi dấu phẩy, sau khi đã loại bỏ cụm số nhà/đường
    ("đường ...", "ngõ ...", "hẻm ...", "số ...") khỏi đoạn đó.
  - "đường Đắk Lắk" bị loại cả cụm, nên không tính tỉnh; "xã Cuôr Đăng"
    thì tính xã, "Đắk Lắk" đứng riêng một đoạn thì tính tỉnh.
  - Không khớp chuỗi con trong một token: "dak lakx" không khớp "dak lak".
  - Nếu không inject danh sách tên chuẩn (known_* rỗng), giữ fallback
    nhận diện tiền tố hành chính bằng regex có \\b (đã là ranh giới từ),
    để các caller cũ không bị vỡ.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence

__all__ = ["GuardDecision", "GuardResult", "InputGuard", "guard_address_input"]

MIN_TOTAL_LETTERS = 10      # số ký tự chữ tối thiểu (không tính số, dấu câu, khoảng trắng)
MIN_MEANINGFUL_CHARS = 3    # số ký tự có nghĩa tối thiểu sau khi bỏ từ chung
MIN_ADMIN_LEVELS = 2

# Từ chung không tính là thông tin định danh: đường, số, ngõ, hẻm...
_STOPWORDS = {
    "duong", "so", "ngo", "hem", "ap", "khu", "pho", "toa", "tang",
    "số", "đường", "ngõ", "hẻm", "ấp",
}
# Token mơ hồ 1-2 âm tiết: khớp chính xác (sau bỏ dấu) thì chặn ngay,
# bất kể độ dài — "bi" không bao giờ đủ để xác định một xã.
_AMBIGUOUS_TOKENS = {"bi", "xom", "xa", "lang", "ban", "nha", "nhà"}

_ADMIN_PATTERNS = {
    "province": re.compile(r"\b(tinh|thanh pho|tp\.?)\b", re.I),
    "district": re.compile(r"\b(quan|huyen|thi xa|tx\.?)\b", re.I),
    "ward": re.compile(r"\b(xa|phuong|thi tran|tt\.?)\b", re.I),
}

# Tiền tố hành chính (dạng đã bỏ dấu, token). "thanh pho" / "thi tran"
# là tiền tố 2 token, xử lý trước tiền tố 1 token.
_ADMIN_PREFIX_TOKENS = {
    "tinh", "tp", "quan", "huyen", "xa", "phuong", "tt", "tx",
}
_ADMIN_PREFIX_TWO_TOKENS = {("thanh", "pho"), ("thi", "tran")}
# Token bắt đầu một cụm số nhà / đường / ngõ / hẻm: tên sau nó là tên
# đường, không phải tên hành chính, phải loại khỏi đoạn trước khi match.
_STREET_START_TOKENS = {"duong", "ngo", "hem"}
_HOUSE_START_TOKENS = {"so", "soi"}
_ADMIN_START_TOKENS = {
    "phuong", "xa", "quan", "huyen", "tinh", "thanh", "tp", "tt", "tx",
}


class GuardDecision(str, Enum):
    PASS = "PASS"                          # đủ thông tin, cho qua pipeline
    NEED_CLARIFICATION = "NEED_CLARIFICATION"  # thiếu cấp hành chính / quá ngắn / mơ hồ
    REJECTED = "REJECTED"                  # không thể là địa chỉ (chỉ số, chỉ ký tự rác)


@dataclass(frozen=True)
class GuardResult:
    decision: GuardDecision
    raw: str
    reason_code: str
    user_message: str  # tiếng Việt, hiển thị trực tiếp trên UI
    missing_fields: tuple[str, ...] = ()   # vd: ("province","district","ward")
    detected_levels: tuple[str, ...] = ()
    call_geocoder: bool = False  # luôn False khi không PASS
    allow_waybill: bool = False  # luôn False khi không PASS


def _strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text or "")
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return stripped.replace("Đ", "D").replace("đ", "d")


def _letters(text: str) -> str:
    return "".join(c for c in text if c.isalpha())


def _tokens(text: str) -> list[str]:
    norm = _strip_diacritics(text).lower()
    return [t for t in re.split(r"[^a-z0-9]+", norm) if t]


def _meaningful_tokens(text: str) -> list[str]:
    tokens = _tokens(text)
    return [t for t in tokens if t not in _STOPWORDS and not t.isdigit()]


def _strip_admin_prefix(tokens: list[str]) -> list[str]:
    """Bỏ tiền tố hành chính ở đầu dãy token: 'xa cuor dang' -> 'cuor dang',
    'thanh pho ho chi minh' -> 'ho chi minh', 'quan 1' -> '1'."""
    out = list(tokens)
    while out:
        if len(out) >= 2 and (out[0], out[1]) in _ADMIN_PREFIX_TWO_TOKENS:
            out = out[2:]
            continue
        if out[0] in _ADMIN_PREFIX_TOKENS:
            out = out[1:]
            continue
        break
    return out


def _remove_street_phrases(tokens: list[str]) -> list[str]:
    """Loại cụm số nhà / đường khỏi dãy token của MỘT đoạn.

    'duong dak lak' -> [] (tên đường, không phải tỉnh);
    'duong le loi phuong ben nghe' -> ['phuong', 'ben', 'nghe'] (dừng
    ngay khi gặp token bắt đầu cấp hành chính);
    'so 12' -> [] ; 'soi 799' -> [].
    """
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _STREET_START_TOKENS:
            i += 1
            while i < len(tokens) and tokens[i] not in _ADMIN_START_TOKENS:
                i += 1
            continue
        if tok in _HOUSE_START_TOKENS:
            i += 1
            while i < len(tokens) and (tokens[i].isdigit() or tokens[i] in ("nha",)):
                i += 1
            continue
        out.append(tok)
        i += 1
    return out


def _is_contiguous_subsequence(needle: tuple[str, ...], haystack: list[str]) -> bool:
    """needle phải khớp thành dãy token liên tiếp hoàn chỉnh trong haystack.
    Đây là ranh giới từ: 'dak lak' không khớp 'dak lakx', và cũng không
    khớp khi 'dak' ở cuối đoạn này và 'lak' ở đầu đoạn khác (vì chỉ tìm
    trong từng đoạn riêng)."""
    if not needle or len(needle) > len(haystack):
        return False
    n = len(needle)
    return any(tuple(haystack[i:i + n]) == needle for i in range(len(haystack) - n + 1))


def _known_token_tuples(names: Sequence[str]) -> set[tuple[str, ...]]:
    """Mỗi tên chuẩn cho 2 dạng token tuple: dạng đầy đủ và dạng đã bỏ
    tiền tố hành chính. Vd: 'Quận 1' -> ('quan','1') và ('1',);
    'Thành phố Hồ Chí Minh' -> ('thanh','pho','ho','chi','minh') và
    ('ho','chi','minh')."""
    out: set[tuple[str, ...]] = set()
    for name in names:
        tokens = _tokens(name)
        if not tokens:
            continue
        out.add(tuple(tokens))
        stripped = _strip_admin_prefix(tokens)
        if stripped:
            out.add(tuple(stripped))
    return out


class InputGuard:
    """Guard thuần domain. known_provinces / known_wards là tên chuẩn
    (có dấu, sẽ được chuẩn hóa nội bộ) từ Canonical Admin DB, inject từ
    infrastructure — không hard-code danh sách 63 tỉnh ở đây để tránh
    lệch version 2025."""

    def __init__(self, known_provinces: Optional[Sequence[str]] = None,
                 known_districts: Optional[Sequence[str]] = None,
                 known_wards: Optional[Sequence[str]] = None):
        self._known = {
            "province": _known_token_tuples(known_provinces or []),
            "district": _known_token_tuples(known_districts or []),
            "ward": _known_token_tuples(known_wards or []),
        }
        self._has_known_names = any(self._known.values())

    def _detect_levels_by_name(self, raw: str) -> set[str]:
        """Nhận diện theo ranh giới từ, theo từng đoạn ngăn bởi dấu phẩy."""
        norm = _strip_diacritics(raw).lower()
        found: set[str] = set()
        for segment in norm.split(","):
            segment = segment.strip()
            if not segment:
                continue
            # Loại cụm đường/số nhà trong đoạn trước khi match tên chuẩn.
            admin_tokens = _remove_street_phrases(_tokens(segment))
            if not admin_tokens:
                continue
            for level, tuples in self._known.items():
                if level in found:
                    continue
                for needle in tuples:
                    if _is_contiguous_subsequence(needle, admin_tokens):
                        found.add(level)
                        break
        return found

    def _detect_levels_by_prefix(self, raw: str) -> set[str]:
        """Fallback khi không có danh sách tên chuẩn: chỉ dựa vào tiền tố
        hành chính, regex đã dùng \\b nên cũng là ranh giới từ."""
        norm = _strip_diacritics(raw).lower()
        return {level for level, pat in _ADMIN_PATTERNS.items() if pat.search(norm)}

    def _detect_levels(self, raw: str) -> set[str]:
        if self._has_known_names:
            return self._detect_levels_by_name(raw)
        return self._detect_levels_by_prefix(raw)

    def check(self, raw: Optional[str]) -> GuardResult:
        text = (raw or "").strip()
        if not text:
            return GuardResult(GuardDecision.NEED_CLARIFICATION, raw or "",
                               "empty_input", "Vui lòng nhập địa chỉ giao hàng.",
                               missing_fields=("province", "district", "ward"))

        norm = _strip_diacritics(text).lower().strip()
        tokens = _meaningful_tokens(text)
        meaningful_chars = sum(len(t) for t in tokens)

        # 1. Chỉ gồm số: "73", "127" — không bao giờ là địa chỉ đầy đủ.
        if re.fullmatch(r"[\d\s,.\-#/]+", text):
            return GuardResult(GuardDecision.REJECTED, text, "numeric_only",
                               "Chỉ có số nhà thì chưa đủ để giao hàng. "
                               "Vui lòng nhập thêm đường, phường/xã, quận/huyện và tỉnh.",
                               missing_fields=("street", "ward", "district", "province"))
        # 2. Token mơ hồ 1-2 âm tiết: "bi", "Bị", "Xom".
        if norm in _AMBIGUOUS_TOKENS or (len(tokens) == 1 and len(tokens[0]) <= 3):
            return GuardResult(GuardDecision.NEED_CLARIFICATION, text, "ambiguous_token",
                               f"“{text}” quá chung chung, hệ thống không thể đoán xã/phường. "
                               "Vui lòng nhập đầy đủ phường/xã, quận/huyện và tỉnh.",
                               missing_fields=("ward", "district", "province"))
        # 3. Quá ngắn: < 10 ký tự chữ, hoặc < 3 ký tự có nghĩa.
        if len(_letters(text)) < MIN_TOTAL_LETTERS or meaningful_chars < MIN_MEANINGFUL_CHARS:
            return GuardResult(GuardDecision.NEED_CLARIFICATION, text, "too_short",
                               "Địa chỉ quá ngắn. Vui lòng nhập thêm đường, "
                               "phường/xã, quận/huyện và tỉnh.",
                               missing_fields=("ward", "district", "province"))
        # 4. Chỉ có số nhà + đường, thiếu cấp hành chính:
        #    "Sói 799, đường Lam Sơn" (Sói = gõ sai của Số) -> chặn.
        levels = self._detect_levels(text)
        has_street = bool(re.search(r"\b(duong|đường)\b", norm)) or bool(re.search(r"\d+", text))
        if has_street and len(levels) == 0:
            return GuardResult(GuardDecision.NEED_CLARIFICATION, text, "street_only_no_admin",
                               "Địa chỉ mới có số nhà và tên đường, còn thiếu "
                               "phường/xã, quận/huyện và tỉnh. Vui lòng bổ sung.",
                               missing_fields=("ward", "district", "province"),
                               detected_levels=tuple(sorted(levels)))
        # 5. Yêu cầu tối thiểu 2 cấp hành chính.
        if len(levels) < MIN_ADMIN_LEVELS:
            missing = tuple(l for l in ("province", "district", "ward") if l not in levels)
            return GuardResult(GuardDecision.NEED_CLARIFICATION, text, "insufficient_admin_levels",
                               "Địa chỉ chưa đủ cấp hành chính để giao hàng chính xác. "
                               "Vui lòng bổ sung tỉnh, quận/huyện hoặc phường/xã.",
                               missing_fields=missing,
                               detected_levels=tuple(sorted(levels)))
        return GuardResult(GuardDecision.PASS, text, "ok", "",
                           detected_levels=tuple(sorted(levels)),
                           call_geocoder=True, allow_waybill=True)


def guard_address_input(raw: Optional[str], **kwargs) -> GuardResult:
    """Hàm tiện ích cho API/service: gọi trước mọi bước autofill."""
    return InputGuard(**kwargs).check(raw)
