"""Ranker — veto lệch tỉnh trước khi xếp hạng, dedupe theo ward_code.

Fix cho golden_11 (địa chỉ đúng hoàn toàn vẫn bị NEEDS_REVIEW):
Ranker gốc giữ nguyên mọi ứng viên, kể cả cùng một xã được trả về từ
nhiều nguồn (parser / fuzzy / geocoder) với cùng điểm 1.0. Khi đó
top1 và top2 là cùng một xã, margin = top1 - top2 = 0, gate hiểu là
mơ hồ và hạ thành NEEDS_REVIEW. Đồng thuận liên nguồn phải tăng độ
tin cậy, không được làm margin về 0.

Sau fix:
  - Veto lệch tỉnh như cũ (giữ nguyên).
  - Gộp ứng viên trùng (province_code, district_code, ward_code), giữ
    điểm cao nhất cho mỗi xã. Ứng viên không có ward_code thì không gộp
    (dùng id riêng) để không vô tình loại ứng viên chưa resolve.
  - Margin tính với xã KHÁC tiếp theo; chỉ có một xã duy nhất thì
    margin = điểm của nó (như gốc).
  - Margin được làm tròn 4 chữ số thập phân: 1.0 - 0.9 trong float là
    0.09999999999999998, không bằng 0.1 nếu so sánh == trực tiếp.
  - API không đổi: RankedResult(ranked, top1, top2, margin), Ranker.rank.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from .province_anchor import AnchorResult
from .schemas import AddressCandidate


@dataclass
class RankedResult:
    ranked: list[AddressCandidate]; top1: Optional[AddressCandidate]=None; top2: Optional[AddressCandidate]=None; margin: float=0.0


class Ranker:
    def rank(self, candidates: list[AddressCandidate], anchor: AnchorResult) -> RankedResult:
        by_ward: dict = {}
        for cand in candidates:
            if anchor.anchored and anchor.province is not None and cand.address.province_code and cand.address.province_code!=anchor.province.code: continue
            if cand.rank_score==0.0: cand.rank_score=cand.provider_score
            ward_code = cand.address.ward_code
            if ward_code:
                key = (cand.address.province_code, cand.address.district_code, ward_code)
            else:
                # Chưa resolve xã: không gộp, mỗi ứng viên giữ một slot riêng.
                key = ("__no_ward_code__", id(cand))
            existing = by_ward.get(key)
            if existing is None or cand.rank_score > existing.rank_score:
                by_ward[key] = cand
        kept = sorted(by_ward.values(), key=lambda c: c.rank_score, reverse=True)
        top1=kept[0] if kept else None; top2=kept[1] if len(kept)>1 else None
        margin=(top1.rank_score-top2.rank_score) if top1 and top2 else (top1.rank_score if top1 else 0.0)
        return RankedResult(ranked=kept,top1=top1,top2=top2,margin=round(max(0.0,margin), 4))
