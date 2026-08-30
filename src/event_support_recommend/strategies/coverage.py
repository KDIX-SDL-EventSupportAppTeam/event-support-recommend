"""COVERAGE — 訪問者数が少ない順 ＋ 関心分野一致 (docs/specs/04-strategies.md §2)。

データが無い時間帯の正規の戦略であり、全フェーズの最終退避先でもある。
層1（リクエスト）の情報だけで完結するので、スナップショットが一切無くても必ず結果を返せる
（= 500 を返さない、O-6 の裏づけ）。
"""

from __future__ import annotations

from ..features import ATTRIBUTES_SCHEMA_VERSION
from ..models import InterestMatch, RecommendationContext, ScoredBooth
from .. import __version__
from .common import compute_candidate_features

INTEREST_TERM = {
    InterestMatch.MATCH: 1.0,
    InterestMatch.PARTIAL: 0.6,
    InterestMatch.MISMATCH: 0.2,  # 0 にしない。不一致ブースが構造的に排除されるとセレンディピティが観測不能になる (P-5)
    InterestMatch.UNKNOWN: 0.2,
}


class CoverageStrategy:
    name = "COVERAGE"

    def recommend(self, ctx: RecommendationContext) -> list[ScoredBooth]:
        cfg = ctx.config
        feats = compute_candidate_features(ctx)
        candidates = list(ctx.request.candidates)
        n = len(candidates)

        # coverage_term: 訪問者が少ないほど高い。厳密に単調非増加。
        # rank = 自分より visitor_count が小さい候補数 / (n - 1)。同数なら同じ値。
        vcounts = [feats[c.booth_id].visitor_count for c in candidates]
        denom = max(n - 1, 1)

        w_sum = cfg.w_coverage + cfg.w_interest or 1.0

        out: list[ScoredBooth] = []
        for cand in candidates:
            f = feats[cand.booth_id]
            smaller = sum(1 for v in vcounts if v < f.visitor_count)
            coverage_term = 1.0 - (smaller / denom) if n > 1 else 1.0
            interest_term = INTEREST_TERM[f.interest_match]
            score = (cfg.w_coverage * coverage_term + cfg.w_interest * interest_term) / w_sum
            score = max(0.0, min(1.0, score))

            condition = {}
            for name in cfg.enabled_attributes:
                if name == "preference_match":
                    condition[name] = f.preference_match
                elif name == "rating_affinity":
                    condition[name] = f.rating_affinity
                elif name == "exploration_disposition" and f.exploration_disposition is not None:
                    condition[name] = f.exploration_disposition

            attributes = {
                "v": ATTRIBUTES_SCHEMA_VERSION,
                "strategy": self.name,
                "enabled": list(cfg.enabled_attributes),
                "condition": condition,
                "raw": {
                    **f.raw,
                    "coverage_term": round(coverage_term, 4),
                    "interest_term": interest_term,
                },
            }
            reason = {
                "v": 1,
                "strategy": self.name,
                "rules": [],
                "tie_break": "visitor_count_asc",
                "terms": {
                    "coverage": round(coverage_term, 4),
                    "interest": interest_term,
                    "w_coverage": cfg.w_coverage,
                    "w_interest": cfg.w_interest,
                },
                "engine": {"version": __version__, "rules_built_at": None},
            }
            out.append(
                ScoredBooth(
                    booth_id=cand.booth_id,
                    score=score,
                    interest_match=f.interest_match,
                    attributes=attributes,
                    reason=reason,
                )
            )
        return out
