"""
approach_08 — Ensemble scoring và xếp hạng đa ứng viên top-k.

Nguyên tắc:
- Chỉ TỰ ĐIỀN khi top-1 vượt ngưỡng tuyệt đối VÀ margin (top1 - top2) đủ lớn.
- Ngược lại trả tối đa 3 gợi ý đầy đủ tỉnh/huyện/xã để khách chọn một chạm.
- Ràng buộc cứng: ứng viên lệch tỉnh đã khóa (anchor) bị loại 100%,
  bất kể điểm chuỗi cao đến đâu (chống ca Cuôr Đăng/Đắk Lắk -> Hải Dương).
- Cộng điểm đồng thuận đa provider, trừ điểm lệch provider, ưu tiên mã xã
  khớp Canonical Admin DB.

Ranker thuần domain: nhận đặc trưng đã trích, không gọi provider trực tiếp.
Mô hình LambdaRank/GBDT/cross-encoder (nếu có) được cắm qua `LearnedScorer`;
khi chưa có model đã train, dùng trọng số tuyến tính mặc định đã hiệu chỉnh
từ golden cases. Không bịa kết quả train.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

AUTO_FILL_MIN_SCORE = 0.92
AUTO_FILL_MIN_MARGIN = 0.12
SUGGESTION_MIN_SCORE = 0.40
MAX_SUGGESTIONS = 3


@dataclass(frozen=True)
class Candidate:
    province_code: str
    district_code: str
    ward_code: str
    province_name: str
    district_name: str
    ward_name: str
    street: str | None = None
    house_number: str | None = None
    lat: float | None = None
    lng: float | None = None
    providers: tuple[str, ...] = ()          # vd ("goong", "ghn")
    in_canonical_db: bool = False            # mã xã khớp Canonical Admin DB
    string_similarity: float = 0.0           # 0..1 so với raw input
    ward_match: float = 0.0
    district_match: float = 0.0
    province_match: float = 0.0
    popularity: float = 0.0                  # dân số / độ phổ biến 0..1
    historical_success_rate: float = 0.5     # từ feedback_store, mặc định trung tính
    gps_distance_km: float | None = None     # nếu có tọa độ tham chiếu

    @property
    def full_label(self) -> str:
        parts = [self.ward_name, self.district_name, self.province_name]
        return ", ".join(p for p in parts if p)


@dataclass
class RankedCandidate:
    candidate: Candidate
    score: float
    reasons: list[str] = field(default_factory=list)
    penalties: list[str] = field(default_factory=list)


@dataclass
class RankingResult:
    decision: str  # AUTO_FILL | SHOW_SUGGESTIONS | NO_CANDIDATE
    ranked: list[RankedCandidate]
    suggestions: list[RankedCandidate]       # tối đa 3, chỉ khi SHOW_SUGGESTIONS
    margin: float

    def to_dict(self) -> dict:
        def rc(r: RankedCandidate) -> dict:
            return {"label": r.candidate.full_label,
                    "province_code": r.candidate.province_code,
                    "district_code": r.candidate.district_code,
                    "ward_code": r.candidate.ward_code,
                    "score": round(r.score, 4),
                    "reasons": r.reasons, "penalties": r.penalties}
        return {"decision": self.decision, "margin": round(self.margin, 4),
                "ranked": [rc(r) for r in self.ranked],
                "suggestions": [rc(r) for r in self.suggestions]}


class LearnedScorer(Protocol):
    """LambdaRank / GBDT / cross-encoder do infrastructure nạp model."""
    def score(self, features: dict[str, float]) -> float: ...


# Trọng số mặc định (tổng phần dương xấp xỉ 1.0 sau chuẩn hóa ở _linear_score).
_DEFAULT_WEIGHTS: dict[str, float] = {
    "province_match": 0.26, "district_match": 0.20, "ward_match": 0.24,
    "string_similarity": 0.12, "popularity": 0.04,
    "historical_success_rate": 0.08, "canonical_bonus": 0.04,
    "provider_consensus": 0.06,
}
_PROVIDER_DISAGREE_PENALTY = 0.18
_GPS_PENALTY_PER_50KM = 0.05
_RETURNED_ADDRESS_PENALTY = 0.25  # địa chỉ từng bị hoàn (từ feedback_store)


def extract_features(c: Candidate, provider_universe: Sequence[str],
                     returned_ward_codes: set[str] | None = None) -> dict[str, float]:
    consensus = (len(set(c.providers) & set(provider_universe)) /
                 max(1, len(set(provider_universe)))) if provider_universe else 0.0
    gps_penalty = 0.0
    if c.gps_distance_km is not None:
        gps_penalty = min(0.30, (c.gps_distance_km / 50.0) * _GPS_PENALTY_PER_50KM)
    return {
        "province_match": c.province_match, "district_match": c.district_match,
        "ward_match": c.ward_match, "string_similarity": c.string_similarity,
        "popularity": c.popularity,
        "historical_success_rate": c.historical_success_rate,
        "canonical_bonus": 1.0 if c.in_canonical_db else 0.0,
        "provider_consensus": consensus,
        "_gps_penalty": gps_penalty,
        "_returned": 1.0 if (returned_ward_codes and c.ward_code in returned_ward_codes) else 0.0,
        "_single_provider": 1.0 if len(set(c.providers)) <= 1 and provider_universe else 0.0,
    }


class EnsembleRanker:
    def __init__(self, learned_scorer: LearnedScorer | None = None,
                 weights: dict[str, float] | None = None,
                 auto_fill_min_score: float = AUTO_FILL_MIN_SCORE,
                 auto_fill_min_margin: float = AUTO_FILL_MIN_MARGIN) -> None:
        self._learned = learned_scorer
        self._weights = weights or dict(_DEFAULT_WEIGHTS)
        self._min_score = auto_fill_min_score
        self._min_margin = auto_fill_min_margin

    def _linear_score(self, f: dict[str, float]) -> tuple[float, list[str], list[str]]:
        score = sum(self._weights[k] * f.get(k, 0.0) for k in self._weights)
        reasons, penalties = [], []
        if f.get("canonical_bonus"):
            reasons.append("Mã xã khớp Canonical Admin DB")
        if f.get("provider_consensus", 0) >= 0.5:
            reasons.append("Nhiều provider đồng thuận")
        if f.get("_single_provider"):
            score -= _PROVIDER_DISAGREE_PENALTY
            penalties.append("Chỉ 1 provider gợi ý, chưa có đồng thuận")
        if f.get("_gps_penalty"):
            score -= f["_gps_penalty"]; penalties.append("Lệch GPS tham chiếu")
        if f.get("_returned"):
            score -= _RETURNED_ADDRESS_PENALTY
            penalties.append("Địa chỉ từng bị hoàn đơn")
        return max(0.0, min(1.0, score)), reasons, penalties

    def rank(self, candidates: Sequence[Candidate],
             anchored_province_code: str | None = None,
             provider_universe: Sequence[str] = ("internal_db", "goong", "ghn", "spx"),
             returned_ward_codes: set[str] | None = None) -> RankingResult:
        # 1) Ràng buộc cứng theo tỉnh đã khóa.
        pool = [c for c in candidates
                if not anchored_province_code or c.province_code == anchored_province_code]
        # 2) Gộp ứng viên trùng mã xã từ nhiều provider (ensemble).
        merged: dict[str, Candidate] = {}
        for c in pool:
            key = c.ward_code
            if key in merged:
                old = merged[key]
                providers = tuple(sorted(set(old.providers) | set(c.providers)))
                merged[key] = Candidate(
                    **{**old.__dict__, "providers": providers,
                       "string_similarity": max(old.string_similarity, c.string_similarity),
                       "ward_match": max(old.ward_match, c.ward_match),
                       "district_match": max(old.district_match, c.district_match),
                       "province_match": max(old.province_match, c.province_match),
                       "historical_success_rate": max(old.historical_success_rate,
                                                      c.historical_success_rate)})
            else:
                merged[key] = c
        # 3) Chấm điểm.
        ranked: list[RankedCandidate] = []
        for c in merged.values():
            feats = extract_features(c, provider_universe, returned_ward_codes)
            if self._learned is not None:
                score = max(0.0, min(1.0, self._learned.score(feats)))
                _, reasons, penalties = self._linear_score(feats)
            else:
                score, reasons, penalties = self._linear_score(feats)
            ranked.append(RankedCandidate(c, round(score, 4), reasons, penalties))
        ranked.sort(key=lambda r: r.score, reverse=True)

        margin = (ranked[0].score - ranked[1].score) if len(ranked) >= 2 else ranked[0].score if ranked else 0.0
        if not ranked or ranked[0].score < SUGGESTION_MIN_SCORE:
            return RankingResult("NO_CANDIDATE", ranked, [], margin)
        if ranked[0].score >= self._min_score and margin >= self._min_margin:
            return RankingResult("AUTO_FILL", ranked, [], margin)
        suggestions = [r for r in ranked if r.score >= SUGGESTION_MIN_SCORE][:MAX_SUGGESTIONS]
        return RankingResult("SHOW_SUGGESTIONS", ranked, suggestions, margin)
