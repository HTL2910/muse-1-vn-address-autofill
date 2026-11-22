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


def _meaningful_tokens(text: str) -> list[str]:
    norm = _strip_diacritics(text).lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", norm) if t]
    return [t for t in tokens if t not in _STOPWORDS and not t.isdigit()]


class InputGuard:
    """Guard thuần domain. known_provinces / known_wards là tên chuẩn
    (không dấu) từ Canonical Admin DB, inject từ infrastructure — không
    hard-code danh sách 63 tỉnh ở đây để tránh lệch version 2025."""

    def __init__(self, known_provinces: Optional[Sequence[str]] = None,
                 known_districts: Optional[Sequence[str]] = None,
                 known_wards: Optional[Sequence[str]] = None):
        self.known_provinces = {_strip_diacritics(p).lower() for p in (known_provinces or [])}
        self.known_districts = {_strip_diacritics(p).lower() for p in (known_districts or [])}
        self.known_wards = {_strip_diacritics(p).lower() for p in (known_wards or [])}

    def _detect_levels(self, raw: str) -> set[str]:
        norm = _strip_diacritics(raw).lower()
        found = {level for level, pat in _ADMIN_PATTERNS.items() if pat.search(norm)}
        # Tên chuẩn xuất hiện tường minh cũng tính là một cấp, kể cả khi
        # khách không gõ tiền tố "tỉnh/quận/xã" (vd: "Cuôr Đăng, Đắk Lắk").
        for name in self.known_provinces:
            if name and name in norm:
                found.add("province")
        for name in self.known_districts:
            if name and name in norm:
                found.add("district")
        for name in self.known_wards:
            if name and name in norm:
                found.add("ward")
        return found

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
