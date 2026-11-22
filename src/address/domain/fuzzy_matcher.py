"""
Fuzzy matching đa tín hiệu trong phạm vi đã khóa — approach_06.

Quy tắc sắt:
  1. CHỈ chạy trong tỉnh (và huyện, nếu đã neo) do bước Anchor tỉnh trả về.
     Không bao giờ tìm toàn quốc — đó là gốc lỗi Cuôr Đăng (Đắk Lắk)
     bị khớp sang Phả Lại (Hải Dương).
  2. Token tường minh của khách là nguồn sự thật tối cao. Provider không
     được ghi đè. Nếu khách ghi rõ tỉnh mà ứng viên khác tỉnh -> loại ngay.
  3. Tên hiếm thắng tên phổ biến: cộng điểm theo IDF của token.
  4. Thuần domain: không import Goong/Pancake/DB. Embedding là Protocol
     tùy chọn; không có embedding vẫn chạy bằng lexical.

Tín hiệu: Levenshtein, Jaro-Winkler, Trigram (xấp xỉ pg_trgm),
token-sort Jaccard, BM25 trên inverted index, embedding PhoBERT/bi-encoder.
BK-tree để tìm ứng viên trong ngưỡng edit-distance mà không quét hết
10.000+ phường/xã trong tỉnh.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional, Protocol, Sequence

__all__ = [
    "Scope", "Candidate", "MatchResult", "EmbeddingProvider",
    "FuzzyMatcher", "BKTree", "levenshtein", "jaro_winkler",
    "trigram_similarity", "token_sort_jaccard", "strip_diacritics",
    "normalize_text", "build_bm25_index",
]

# ---------------------------------------------------------------------------
# Chuẩn hóa tiếng Việt
# ---------------------------------------------------------------------------

_ABBREV = {
    "tp": "thanh pho", "tx": "thi xa", "q": "quan", "h": "huyen",
    "p": "phuong", "x": "xa", "tt": "thi tran", "duong": "duong",
    "so": "so", "số": "so",
}
# Lỗi gõ / alias địa phương thường gặp — học thêm từ feedback loop (approach_18).
_ALIASES = {
    "cuor dang": "cuor dang", "cuo dang": "cuor dang", "cuor dang": "cuor dang",
    "dak ya": "dak ya", "dak lak": "dak lak", "daklak": "dak lak",
    "pha lai": "pha lai",
}


def strip_diacritics(text: str) -> str:
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return stripped.replace("Đ", "D").replace("đ", "d")


def normalize_text(text: str) -> str:
    """lower + bỏ dấu + gộp khoảng trắng + mở rộng viết tắt + alias."""
    t = strip_diacritics(text or "").lower().strip()
    t = re.sub(r"[.,;:]+", " ", t)
    tokens = [_ABBREV.get(tok, tok) for tok in t.split()]
    t = " ".join(tokens)
    return _ALIASES.get(t, t)


def _tokens(text: str) -> list[str]:
    return [t for t in normalize_text(text).split() if t]


# ---------------------------------------------------------------------------
# Các độ đo tương đồng (thuần, có test được, không phụ thuộc lib ngoài)
# ---------------------------------------------------------------------------

def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _jaro(s1: str, s2: str) -> float:
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if not len1 or not len2:
        return 0.0
    match_dist = max(len1, len2) // 2 - 1
    m1 = [False] * len1
    m2 = [False] * len2
    matches = 0
    for i, c1 in enumerate(s1):
        lo, hi = max(0, i - match_dist), min(len2, i + match_dist + 1)
        for j in range(lo, hi):
            if not m2[j] and s2[j] == c1:
                m1[i] = m2[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    t = sum(1 for i in range(len1) if m1[i] for j in range(len2)
            if m2[j] and s1[i] != s2[j]) // 2 if False else 0
    # đếm transposition đúng cách
    k, transpositions = 0, 0
    for i in range(len1):
        if m1[i]:
            while not m2[k]:
                k += 1
            if s1[i] != s2[k]:
                transpositions += 1
            k += 1
    return (matches / len1 + matches / len2 + (matches - transpositions / 2) / matches) / 3


def jaro_winkler(a: str, b: str, prefix_scale: float = 0.1) -> float:
    j = _jaro(normalize_text(a), normalize_text(b))
    prefix = 0
    for ca, cb in zip(normalize_text(a), normalize_text(b)):
        if ca == cb and prefix < 4:
            prefix += 1
        else:
            break
    return j + prefix * prefix_scale * (1 - j)


def _trigrams(text: str) -> set[str]:
    s = f"  {normalize_text(text)}  "
    return {s[i:i + 3] for i in range(len(s) - 2)} if len(s) >= 3 else {s}


def trigram_similarity(a: str, b: str) -> float:
    """Xấp xỉ pg_trgm similarity() — dùng ở domain để test, DB dùng pg_trgm thật."""
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def token_sort_jaccard(a: str, b: str) -> float:
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ---------------------------------------------------------------------------
# BK-tree + BM25
# ---------------------------------------------------------------------------

class BKTree:
    """Tìm ứng viên trong ngưỡng edit-distance mà không quét toàn bộ danh sách."""

    def __init__(self, distance_fn=levenshtein):
        self.distance_fn = distance_fn
        self.root: Optional[tuple[str, dict[int, tuple]]] = None

    def add(self, term: str) -> None:
        term = normalize_text(term)
        if self.root is None:
            self.root = (term, {})
            return
        node_term, children = self.root
        # đi xuống đệ quy (dạng đơn giản, đủ cho ~10k tên trong 1 tỉnh)
        cur_term, cur_children = node_term, children
        while True:
            d = self.distance_fn(term, cur_term)
            if d == 0:
                return
            nxt = cur_children.get(d)
            if nxt is None:
                cur_children[d] = (term, {})
                return
            cur_term, cur_children = nxt

    def search(self, term: str, max_dist: int) -> list[tuple[str, int]]:
        term = normalize_text(term)
        out: list[tuple[str, int]] = []
        if self.root is None:
            return out
        stack = [self.root]
        while stack:
            node_term, children = stack.pop()
            d = self.distance_fn(term, node_term)
            if d <= max_dist:
                out.append((node_term, d))
            for dist, child in children.items():
                if abs(dist - d) <= max_dist:
                    stack.append(child)
        return sorted(out, key=lambda x: x[1])


def build_bm25_index(docs: Sequence[str]) -> dict[str, float]:
    """IDF cho BM25: token hiếm (cuor, dang) có trọng số cao hơn token
    phổ biến (phuong, xa, thanh). Trả về dict token -> idf."""
    n = max(1, len(docs))
    df: Counter[str] = Counter()
    for doc in docs:
        for tok in set(_tokens(doc)):
            df[tok] += 1
    return {tok: math.log(1 + (n - cnt + 0.5) / (cnt + 0.5)) for tok, cnt in df.items()}


def bm25_score(query: str, doc: str, idf: dict[str, float],
               avg_len: float, k1: float = 1.2, b: float = 0.75) -> float:
    qt, dt = _tokens(query), _tokens(doc)
    if not dt:
        return 0.0
    freq = Counter(dt)
    score = 0.0
    for tok in qt:
        if tok not in freq:
            continue
        denom = freq[tok] + k1 * (1 - b + b * len(dt) / max(1.0, avg_len))
        score += idf.get(tok, 0.0) * freq[tok] * (k1 + 1) / denom
    return score


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Scope:
    """Phạm vi đã khóa. province_code bắt buộc — không có là không match."""
    province_code: str
    district_code: Optional[str] = None


@dataclass(frozen=True)
class Candidate:
    code: str
    name: str
    province_code: str
    district_code: Optional[str] = None
    # tên đã chuẩn hóa của ứng viên, để BK-tree/BM25 dùng lại
    name_unsigned: str = ""


@dataclass
class MatchResult:
    candidate: Candidate
    score: float  # 0..1 đã hiệu chuẩn thô, dùng chung với Confidence Gate
    signals: dict[str, float] = field(default_factory=dict)
    vetoed: bool = False
    veto_reason: str = ""


class FuzzyMatcher:
    # Trọng số mặc định — tune bằng golden eval (approach_18), không tune tay mù.
    WEIGHTS = {"jaro_winkler": 0.25, "trigram": 0.20, "jaccard": 0.20,
               "bm25_norm": 0.20, "embedding": 0.15}

    def __init__(self, candidates: Sequence[Candidate],
                 embedding: Optional[EmbeddingProvider] = None):
        self.candidates = list(candidates)
        self.embedding = embedding
        names = [c.name_unsigned or normalize_text(c.name) for c in self.candidates]
        self.idf = build_bm25_index(names)
        self.avg_len = sum(len(_tokens(n)) for n in names) / max(1, len(names))
        self._by_unsigned = {normalize_text(c.name): c for c in self.candidates}
        self.bk = BKTree()
        for c in self.candidates:
            self.bk.add(c.name_unsigned or c.name)
        self._emb_cache: dict[str, list[float]] = {}

    def _embedding_sim(self, query: str, cand_name: str) -> float:
        if not self.embedding:
            return 0.0
        if query not in self._emb_cache:
            self._emb_cache[query] = self.embedding.embed([query])[0]
        if cand_name not in self._emb_cache:
            self._emb_cache[cand_name] = self.embedding.embed([cand_name])[0]
        return max(0.0, _cosine(self._emb_cache[query], self._emb_cache[cand_name]))

    def _score(self, query: str, cand: Candidate) -> MatchResult:
        cand_name = cand.name_unsigned or normalize_text(cand.name)
        q = normalize_text(query)
        # IDF boost: token hiếm trong query khớp được thì cộng mạnh.
        # "cuor dang" hiếm -> thắng "pha lai" phổ biến dù edit-distance tương đương.
        rare_bonus = sum(self.idf.get(t, 0.0) for t in set(_tokens(q)) & set(_tokens(cand_name)))
        rare_bonus = min(0.15, rare_bonus * 0.02)
        bm25_raw = bm25_score(q, cand_name, self.idf, self.avg_len)
        signals = {
            "jaro_winkler": jaro_winkler(q, cand_name),
            "trigram": trigram_similarity(q, cand_name),
            "jaccard": token_sort_jaccard(q, cand_name),
            "bm25_norm": min(1.0, bm25_raw / 5.0),
            "embedding": self._embedding_sim(q, cand_name),
            "rare_bonus": rare_bonus,
        }
        score = sum(self.WEIGHTS[k] * signals[k] for k in self.WEIGHTS) + rare_bonus
        return MatchResult(candidate=cand, score=min(1.0, score), signals=signals)

    def match(self, query: str, scope: Scope, top_k: int = 5,
              min_score: float = 0.55) -> list[MatchResult]:
        """Chỉ match trong scope. Ứng viên ngoài scope bị veto, không chỉ hạ điểm."""
        if not query or not query.strip():
            return []
        if not scope.province_code:
            raise ValueError("fuzzy match requires a locked province scope")
        q = normalize_text(query)
        # Exact match trước — rẻ và chính xác nhất.
        if q in self._by_unsigned:
            cand = self._by_unsigned[q]
            if cand.province_code == scope.province_code:
                return [MatchResult(candidate=cand, score=1.0,
                                    signals={"exact": 1.0})]
        # BK-tree rút gọn ứng viên, sau đó chấm đa tín hiệu.
        near = {term for term, _ in self.bk.search(q, max_dist=3)}
        pool = [c for c in self.candidates
                if (c.name_unsigned or normalize_text(c.name)) in near
                or token_sort_jaccard(q, c.name) > 0.3]
        if not pool:  # fallback có kiểm soát: vẫn trong scope, chấm toàn bộ scope
            pool = list(self.candidates)
        results: list[MatchResult] = []
        for cand in pool:
            # VETO cứng: khác tỉnh / khác huyện đã neo -> loại, không xếp hạng.
            if cand.province_code != scope.province_code:
                results.append(MatchResult(candidate=cand, score=0.0, vetoed=True,
                                           veto_reason="province_mismatch"))
                continue
            if scope.district_code and cand.district_code and \
                    cand.district_code != scope.district_code:
                results.append(MatchResult(candidate=cand, score=0.0, vetoed=True,
                                           veto_reason="district_mismatch"))
                continue
            results.append(self._score(query, cand))
        ranked = sorted((r for r in results if not r.vetoed),
                        key=lambda r: r.score, reverse=True)
        return [r for r in ranked if r.score >= min_score][:top_k]
