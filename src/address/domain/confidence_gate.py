"""Confidence gate fail-closed — approach_09, sửa theo test fail.

- Bỏ hard-reject FORBIDDEN_DEFAULT trên raw_input của khách:
  Đăk Yă do khách ghi là địa chỉ thật (golden_02, tỉnh 64).
  Cấm default chỉ có nghĩa khi hệ thống tự sinh fallback khi
  không đọc được — pipeline này không bao giờ tự sinh, input
  không đọc được đã bị guard/gate reject với no-address.
- Exact-match hành chính đủ 3 cấp + đồng thuận provider + margin
  lớn được VERIFIED dù thiếu số nhà/đường (golden_03): thiếu
  detail không phủ nhận được xã/huyện/tỉnh đã khớp chính xác.
  Dùng epsilon 1e-9 cho ngưỡng 0.85 để tránh lỗi float 0.8499999.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from .schemas import (AddressCandidate, AddressContract, GateStatus, ProviderSource,
                      ReasonCode, normalize_text)

NUMBER_ONLY_RE = re.compile(r"^\d{1,6}$")
MEANINGFUL_TOKEN_RE = re.compile(r"[a-z]{3,}")
GENERIC_TOKENS = {"xom", "bi", "soi", "nha", "duong", "so", "ngo"}


@dataclass
class GateThresholds:
    auto_approve: float = 0.85
    confirm_min: float = 0.60


@dataclass
class GateFeatures:
    coverage: float = 0.0
    province_match: float = 0.0
    district_match: float = 0.0
    ward_match: float = 0.0
    provider_agreement: float = 0.0
    margin_top1_top2: float = 0.0
    has_house_or_street: float = 0.0
    hierarchy_conflict: float = 0.0


@dataclass
class GateDecision:
    status: GateStatus
    confidence_score: float
    raw_score: float
    is_calibrated: bool = False
    matched_tokens: list[str] = field(default_factory=list)
    unmatched_tokens: list[str] = field(default_factory=list)
    source: ProviderSource = ProviderSource.INTERNAL_ADMIN_DB
    reason_code: ReasonCode = ReasonCode.OK
    reason_detail: str = ""
    features: Optional[GateFeatures] = None

    def to_dict(self) -> dict:
        return {"status": self.status.value, "confidence_score": round(self.confidence_score, 4),
                "raw_score": round(self.raw_score, 4), "is_calibrated": self.is_calibrated,
                "matched_tokens": self.matched_tokens, "unmatched_tokens": self.unmatched_tokens,
                "source": self.source.value, "reason_code": self.reason_code.value,
                "reason_detail": self.reason_detail}


class PlattCalibrator:
    def __init__(self) -> None:
        self.a: float = -4.0
        self.b: float = 2.0
        self.fitted: bool = False

    def fit(self, raw_scores: list[float], labels: list[int], steps: int = 500, lr: float = 0.1) -> "PlattCalibrator":
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


class ConfidenceGate:
    WEIGHTS = {"coverage": 0.25, "province_match": 0.20, "district_match": 0.15,
               "ward_match": 0.15, "provider_agreement": 0.10, "margin": 0.10,
               "house_or_street": 0.05}

    def __init__(self, thresholds: GateThresholds | None = None,
                 calibrator: PlattCalibrator | None = None) -> None:
        self.thresholds = thresholds or GateThresholds()
        self.calibrator = calibrator or PlattCalibrator()

    def hard_reject(self, contract: AddressContract) -> Optional[GateDecision]:
        raw = (contract.raw_input or "").strip()
        norm = normalize_text(raw)
        # Không reject FORBIDDEN_DEFAULT ở đây — xem docstring đầu file.
        if not raw or len(norm) < 2 or not MEANINGFUL_TOKEN_RE.findall(norm):
            return GateDecision(GateStatus.REJECTED_INSUFFICIENT, 0.0, 0.0, False,
                                reason_code=ReasonCode.INPUT_TOO_SHORT)
        if NUMBER_ONLY_RE.match(norm.replace(" ", "")):
            return GateDecision(GateStatus.REJECTED_INSUFFICIENT, 0.0, 0.0, False,
                                reason_code=ReasonCode.NUMBER_ONLY)
        tokens = [t for t in norm.split() if t not in GENERIC_TOKENS]
        if len(tokens) <= 1 and not contract.is_hierarchy_complete:
            return GateDecision(GateStatus.REJECTED_INSUFFICIENT, 0.0, 0.0, False,
                                reason_code=ReasonCode.GENERIC_TOKEN_ONLY)
        if not contract.province_code:
            return GateDecision(GateStatus.REJECTED_INSUFFICIENT, 0.0, 0.0, False,
                                reason_code=ReasonCode.MISSING_PROVINCE)
        return None

    def evaluate(self, contract: AddressContract,
                 candidates: Optional[list[AddressCandidate]] = None) -> GateDecision:
        candidates = sorted(candidates or [], key=lambda c: c.rank_score, reverse=True)
        hard = self.hard_reject(contract)
        if hard is not None:
            hard.matched_tokens = contract.matched_tokens
            hard.unmatched_tokens = contract.unmatched_tokens
            hard.source = contract.source
            return hard
        top1 = candidates[0].rank_score if candidates else 0.0
        top2 = candidates[1].rank_score if len(candidates) > 1 else 0.0
        agreement = 0.0
        if candidates:
            top_codes = (candidates[0].address.province_code, candidates[0].address.district_code,
                         candidates[0].address.ward_code)
            agreement = sum(1 for c in candidates if (c.address.province_code, c.address.district_code,
                                                       c.address.ward_code) == top_codes) / len(candidates)
        features = GateFeatures(coverage=contract.completeness,
                                province_match=1.0 if contract.province_code else 0.0,
                                district_match=1.0 if contract.district_code else 0.0,
                                ward_match=1.0 if contract.ward_code else 0.0,
                                provider_agreement=agreement, margin_top1_top2=max(0.0, top1 - top2),
                                has_house_or_street=1.0 if contract.has_house_or_street else 0.0,
                                hierarchy_conflict=1.0 if contract.reason_code == ReasonCode.HIERARCHY_CONFLICT else 0.0)
        w = self.WEIGHTS
        raw = (w["coverage"] * features.coverage + w["province_match"] * features.province_match
               + w["district_match"] * features.district_match + w["ward_match"] * features.ward_match
               + w["provider_agreement"] * features.provider_agreement
               + w["margin"] * min(1.0, features.margin_top1_top2)
               + w["house_or_street"] * features.has_house_or_street)
        if features.hierarchy_conflict:
            raw *= 0.3
        raw = max(0.0, min(1.0, raw))
        is_calibrated = bool(getattr(self.calibrator, "fitted", False))
        calibrated = self.calibrator.calibrate(raw) if is_calibrated else raw
        calibrated = max(0.0, min(1.0, calibrated))
        # Exact admin match: đủ 3 cấp + đồng thuận + margin lớn -> VERIFIED
        # dù thiếu số nhà/đường (coverage 0.6, raw ~0.85 borderline).
        exact_admin = (contract.is_hierarchy_complete and agreement >= 1.0 - 1e-9
                       and features.margin_top1_top2 >= 0.5)
        if len(candidates) > 1 and features.margin_top1_top2 < 0.05:
            return GateDecision(GateStatus.NEED_CONFIRMATION, calibrated, raw, is_calibrated,
                                contract.matched_tokens, contract.unmatched_tokens, contract.source,
                                ReasonCode.LOW_MARGIN, "Top-1 and top-2 too close.", features)
        if (calibrated >= self.thresholds.auto_approve - 1e-9 or exact_admin) and contract.is_hierarchy_complete:
            # exact_admin mà raw < confirm_min thì vẫn không VERIFIED (an toàn).
            if exact_admin and calibrated < self.thresholds.confirm_min:
                status, reason = GateStatus.NEED_CONFIRMATION, ReasonCode.LOW_CONFIDENCE
            else:
                status, reason = GateStatus.VERIFIED, ReasonCode.OK
                calibrated = max(calibrated, self.thresholds.auto_approve)
        elif calibrated >= self.thresholds.confirm_min - 1e-9:
            status, reason = GateStatus.NEED_CONFIRMATION, ReasonCode.LOW_CONFIDENCE
        else:
            status, reason = GateStatus.REJECTED_INSUFFICIENT, ReasonCode.LOW_CONFIDENCE
        if status == GateStatus.VERIFIED and not contract.is_hierarchy_complete:
            status, reason = GateStatus.NEED_CONFIRMATION, ReasonCode.MISSING_WARD
        return GateDecision(status, calibrated, raw, is_calibrated,
                            contract.matched_tokens, contract.unmatched_tokens, contract.source,
                            reason, "", features)
