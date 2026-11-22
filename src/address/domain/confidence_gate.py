"""
Confidence Gate fail-closed — approach_09, nhánh expert/systems-arch-m1.

- Xóa hoàn toàn default "Xã Đăk Yă, Mang Yang, Gia Lai" và mọi fallback.
- Điểm 0-1 được hiệu chuẩn bằng Platt scaling (mặc định) hoặc
  isotonic regression trên dữ liệu lịch sử có nhãn giao thành công/hoàn.
- Ngưỡng mặc định: >= 0.85 và đủ tỉnh+huyện+xã -> VERIFIED;
  0.60-0.85 -> NEED_CONFIRMATION; < 0.60 -> REJECTED_INSUFFICIENT.
- Input "73", "127", "Xom", "bi"/"Bị", "Sói 799, đường Lam Sơn"
  thiếu phân cấp sẽ bị chặn ở hard-rule, không bao giờ auto-approve.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .province_anchor import GENERIC_TOKENS
from .schemas import (
    AddressCandidate,
    AddressContract,
    GateStatus,
    ProviderSource,
    ReasonCode,
    is_forbidden_default,
    normalize_text,
)

NUMBER_ONLY_RE = re.compile(r"^\d{1,6}$")
# Token có nghĩa: chữ cái, dài >= 3, không phải từ chung.
MEANINGFUL_TOKEN_RE = re.compile(r"[a-z]{3,}", re.IGNORECASE)


@dataclass
class GateThresholds:
    auto_approve: float = 0.85
    confirm_min: float = 0.60


@dataclass
class GateFeatures:
    input_length: int = 0
    meaningful_token_count: int = 0
    coverage: float = 0.0  # completeness của AddressContract
    province_match: float = 0.0
    district_match: float = 0.0
    ward_match: float = 0.0
    provider_agreement: float = 0.0  # tỉ lệ provider đồng thuận top-1
    margin_top1_top2: float = 0.0  # chênh lệch điểm top-1 và top-2
    has_house_or_street: float = 0.0
    hierarchy_conflict: float = 0.0  # 1.0 = xung đột phân cấp


@dataclass
class GateDecision:
    status: GateStatus
    confidence_score: float  # đã hiệu chuẩn, 0-1
    raw_score: float
    matched_tokens: list[str] = field(default_factory=list)
    unmatched_tokens: list[str] = field(default_factory=list)
    source: ProviderSource = ProviderSource.INTERNAL_ADMIN_DB
    reason_code: ReasonCode = ReasonCode.OK
    reason_detail: str = ""
    features: Optional[GateFeatures] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "confidence_score": round(self.confidence_score, 4),
            "raw_score": round(self.raw_score, 4),
            "matched_tokens": self.matched_tokens,
            "unmatched_tokens": self.unmatched_tokens,
            "source": self.source.value,
            "reason_code": self.reason_code.value,
            "reason_detail": self.reason_detail,
        }


# ---------------------------------------------------------------------------
# Hiệu chuẩn: Platt scaling + Isotonic (PAVA) — không phụ thuộc sklearn
# ---------------------------------------------------------------------------
class PlattCalibrator:
    """P(A|raw) = 1 / (1 + exp(A*raw + B)), fit bằng gradient descent."""

    def __init__(self) -> None:
        self.a: float = -4.0
        self.b: float = 2.0
        self.fitted: bool = False

    def fit(self, raw_scores: list[float], labels: list[int],
            steps: int = 500, lr: float = 0.1) -> "PlattCalibrator":
        if len(raw_scores) != len(labels) or len(raw_scores) < 10:
            raise ValueError("Need >=10 labelled samples to fit Platt scaling")
        a, b = self.a, self.b
        for _ in range(steps):
            ga = gb = 0.0
            for x, y in zip(raw_scores, labels):
                z = a * x + b
                p = 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))
                err = p - y
                ga += err * x
                gb += err
            a -= lr * ga / len(raw_scores)
            b -= lr * gb / len(raw_scores)
        self.a, self.b, self.fitted = a, b, True
        return self

    def calibrate(self, raw: float) -> float:
        z = self.a * raw + self.b
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        e = math.exp(z)
        return e / (1.0 + e)


class IsotonicCalibrator:
    """Hiệu chuẩn không tham số bằng Pool Adjacent Violators Algorithm."""

    def __init__(self) -> None:
        self.breakpoints: list[tuple[float, float]] = []  # (raw, calibrated)

    def fit(self, raw_scores: list[float], labels: list[int]) -> "IsotonicCalibrator":
        pairs = sorted(zip(raw_scores, labels))
        # PAVA trên các block: giá trị = trung bình nhãn trong block.
        blocks: list[list[float]] = []  # [sum_y, count, x_min, x_max]
        for x, y in pairs:
            blocks.append([float(y), 1.0, x, x])
            while len(blocks) >= 2:
                prev, cur = blocks[-2], blocks[-1]
                if prev[0] / prev[1] <= cur[0] / cur[1]:
                    break
                merged = [prev[0] + cur[0], prev[1] + cur[1], prev[2], cur[3]]
                blocks[-2:] = [merged]
        self.breakpoints = [(b[3], b[0] / b[1]) for b in blocks]
        return self

    def calibrate(self, raw: float) -> float:
        if not self.breakpoints:
            return raw
        for x_max, value in self.breakpoints:
            if raw <= x_max:
                return value
        return self.breakpoints[-1][1]


# ---------------------------------------------------------------------------
# Confidence Gate
# ---------------------------------------------------------------------------
class ConfidenceGate:
    # Trọng số raw score — tổng = 1.0, có thể tune từ dữ liệu lịch sử.
    WEIGHTS = {
        "coverage": 0.25,
        "province_match": 0.20,
        "district_match": 0.15,
        "ward_match": 0.15,
        "provider_agreement": 0.10,
        "margin": 0.10,
        "house_or_street": 0.05,
    }

    def __init__(
        self,
        thresholds: GateThresholds | None = None,
        calibrator: PlattCalibrator | IsotonicCalibrator | None = None,
    ) -> None:
        self.thresholds = thresholds or GateThresholds()
        self.calibrator = calibrator or PlattCalibrator()

    # -- Hard-rule: chặn trước khi tính điểm -------------------------------
    def hard_reject(self, contract: AddressContract) -> Optional[GateDecision]:
        raw = (contract.raw_input or "").strip()
        norm = normalize_text(raw)
        tokens = [t for t in norm.split() if t not in GENERIC_TOKENS]
        meaningful = MEANINGFUL_TOKEN_RE.findall(norm)

        if is_forbidden_default(raw):
            return self._reject(ReasonCode.FORBIDDEN_DEFAULT,
                                "Forbidden default address must never be used.")
        if not raw or len(norm) < 2 or len(meaningful) == 0:
            return self._reject(ReasonCode.INPUT_TOO_SHORT,
                                "Input too short or has no meaningful token.")
        if NUMBER_ONLY_RE.match(norm.replace(" ", "")):
            # "73" -> Hợp Giang, "127" -> Hiến Nam: chỉ có số, cấm đoán.
            return self._reject(ReasonCode.NUMBER_ONLY,
                                "Number-only input cannot identify an address.")
        if len(tokens) <= 1 and not contract.is_hierarchy_complete:
            # "Xom", "bi"/"Bị": 1 từ chung, không có phân cấp.
            return self._reject(ReasonCode.GENERIC_TOKEN_ONLY,
                                "Single generic token cannot identify an address.")
        if not contract.province_code:
            return self._reject(ReasonCode.MISSING_PROVINCE,
                                "Province could not be anchored from input.")
        return None

    def _reject(self, reason: ReasonCode, detail: str) -> GateDecision:
        return GateDecision(
            status=GateStatus.REJECTED_INSUFFICIENT,
            confidence_score=0.0,
            raw_score=0.0,
            reason_code=reason,
            reason_detail=detail,
        )

    # -- Tính điểm ---------------------------------------------------------
    def extract_features(
        self,
        contract: AddressContract,
        candidates: list[AddressCandidate],
    ) -> GateFeatures:
        raw = contract.raw_input or ""
        norm = normalize_text(raw)
        meaningful = MEANINGFUL_TOKEN_RE.findall(norm)
        top1 = candidates[0].rank_score if candidates else 0.0
        top2 = candidates[1].rank_score if len(candidates) > 1 else 0.0
        # Đồng thuận: tỉ lệ provider có cùng bộ 3 mã với top-1.
        agreement = 0.0
        if candidates:
            top_codes = (
                candidates[0].address.province_code,
                candidates[0].address.district_code,
                candidates[0].address.ward_code,
            )
            same = sum(
                1
                for c in candidates
                if (
                    c.address.province_code,
                    c.address.district_code,
                    c.address.ward_code,
                )
                == top_codes
            )
            agreement = same / len(candidates)
        return GateFeatures(
            input_length=len(raw),
            meaningful_token_count=len(meaningful),
            coverage=contract.completeness,
            province_match=1.0 if contract.province_code else 0.0,
            district_match=1.0 if contract.district_code else 0.0,
            ward_match=1.0 if contract.ward_code else 0.0,
            provider_agreement=agreement,
            margin_top1_top2=max(0.0, top1 - top2),
            has_house_or_street=1.0 if contract.has_house_or_street else 0.0,
            hierarchy_conflict=(
                1.0 if contract.reason_code == ReasonCode.HIERARCHY_CONFLICT else 0.0
            ),
        )

    def raw_score(self, f: GateFeatures) -> float:
        w = self.WEIGHTS
        score = (
            w["coverage"] * f.coverage
            + w["province_match"] * f.province_match
            + w["district_match"] * f.district_match
            + w["ward_match"] * f.ward_match
            + w["provider_agreement"] * f.provider_agreement
            + w["margin"] * min(1.0, f.margin_top1_top2)
            + w["house_or_street"] * f.has_house_or_street
        )
        if f.hierarchy_conflict:
            score *= 0.3  # xung đột phân cấp: hạ điểm mạnh
        return max(0.0, min(1.0, score))

    def evaluate(
        self,
        contract: AddressContract,
        candidates: Optional[list[AddressCandidate]] = None,
    ) -> GateDecision:
        candidates = sorted(
            candidates or [], key=lambda c: c.rank_score, reverse=True
        )
        hard = self.hard_reject(contract)
        if hard is not None:
            hard.matched_tokens = contract.matched_tokens
            hard.unmatched_tokens = contract.unmatched_tokens
            hard.source = contract.source
            return hard

        features = self.extract_features(contract, candidates)
        raw = self.raw_score(features)
        calibrated = self.calibrator.calibrate(raw) if getattr(
            self.calibrator, "fitted", True
        ) else raw
        calibrated = max(0.0, min(1.0, calibrated))

        # Margin quá nhỏ giữa top-1/top-2 -> không được auto-approve.
        if features.margin_top1_top2 < 0.05 and len(candidates) > 1:
            return GateDecision(
                status=GateStatus.NEED_CONFIRMATION,
                confidence_score=calibrated,
                raw_score=raw,
                matched_tokens=contract.matched_tokens,
                unmatched_tokens=contract.unmatched_tokens,
                source=contract.source,
                reason_code=ReasonCode.LOW_MARGIN,
                reason_detail="Top-1 and top-2 candidates are too close.",
                features=features,
            )

        if calibrated >= self.thresholds.auto_approve and contract.is_hierarchy_complete:
            status, reason = GateStatus.VERIFIED, ReasonCode.OK
        elif calibrated >= self.thresholds.confirm_min:
            status, reason = GateStatus.NEED_CONFIRMATION, ReasonCode.LOW_CONFIDENCE
        else:
            status, reason = GateStatus.REJECTED_INSUFFICIENT, ReasonCode.LOW_CONFIDENCE

        # VERIFIED nhưng thiếu xã/huyện thì hạ xuống NEED_CONFIRMATION.
        if status == GateStatus.VERIFIED and not contract.is_hierarchy_complete:
            status, reason = GateStatus.NEED_CONFIRMATION, ReasonCode.MISSING_WARD

        return GateDecision(
            status=status,
            confidence_score=calibrated,
            raw_score=raw,
            matched_tokens=contract.matched_tokens,
            unmatched_tokens=contract.unmatched_tokens,
            source=contract.source,
            reason_code=reason,
            reason_detail="",
            features=features,
        )


def fit_calibrator_from_history(
    history: Iterable[tuple[float, int]],
    method: str = "platt",
) -> PlattCalibrator | IsotonicCalibrator:
    """history: iterable (raw_score, label) với label 1=giao thành công, 0=hoàn."""
    rows = list(history)
    raw = [r for r, _ in rows]
    labels = [int(y) for _, y in rows]
    if method == "isotonic":
        return IsotonicCalibrator().fit(raw, labels)
    return PlattCalibrator().fit(raw, labels)
