"""Ranker / ensemble scoring — approach_08, canonical cho fix_missing_pipeline_core.

Loại ứng viên lệch tỉnh TRƯỚC khi xếp hạng. Chỉ xếp hạng khi có
margin top-1/top-2; margin nhỏ là tín hiệu cho gate, không tự chốt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .province_anchor import AnchorResult
from .schemas import AddressCandidate


@dataclass
class RankedResult:
    ranked: list[AddressCandidate]
    top1: Optional[AddressCandidate] = None
    top2: Optional[AddressCandidate] = None
    margin: float = 0.0


class Ranker:
    def rank(self, candidates: list[AddressCandidate], anchor: AnchorResult) -> RankedResult:
        kept: list[AddressCandidate] = []
        for cand in candidates:
            addr = cand.address
            if anchor.anchored and anchor.province is not None:
                if addr.province_code and addr.province_code != anchor.province.code:
                    continue
            # rank_score = provider_score đã cộng/trừ ở matcher/router;
            # nếu chưa có thì dùng provider_score.
            if cand.rank_score == 0.0:
                cand.rank_score = cand.provider_score
            kept.append(cand)
        kept.sort(key=lambda c: c.rank_score, reverse=True)
        top1 = kept[0] if kept else None
        top2 = kept[1] if len(kept) > 1 else None
        margin = (top1.rank_score - top2.rank_score) if top1 and top2 else (top1.rank_score if top1 else 0.0)
        return RankedResult(ranked=kept, top1=top1, top2=top2, margin=max(0.0, margin))
