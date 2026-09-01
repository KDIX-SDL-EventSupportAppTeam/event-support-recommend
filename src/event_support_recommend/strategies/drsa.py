"""DRSA — 規則による推薦 (docs/specs/04-strategies.md §4)。

**規則の生成は行わない。** キャッシュ済みの規則（`ctx.ruleset`）を、その参加者の
候補に当てはめるだけ。生成はバックグラウンド（段3-b）。

- `score = (1 + up - down) / 2`
- 適合規則0本の候補は `score = 0.5`（判断保留）＋ `DRSA_COVERAGE_FLOOR` で
  visitor_count に依存しない関心項を混ぜて順序を付ける（S-1）
- `reason.rules` には**規則 id と要約だけ**（本体を入れると3万行になる）

規則が0本／未構築なら `StrategyUnavailable` を投げ、退避ラダーが SIMILARITY へ落とす。
"""

from __future__ import annotations

from .. import __version__
from ..features import ATTRIBUTES_SCHEMA_VERSION
from ..models import InterestMatch, RecommendationContext, ScoredBooth
from .base import StrategyUnavailable
from .common import compute_candidate_features

_INTEREST_TERM = {
    InterestMatch.MATCH: 1.0,
    InterestMatch.PARTIAL: 0.6,
    InterestMatch.MISMATCH: 0.2,
    InterestMatch.UNKNOWN: 0.2,
}
# MISMATCH の候補でもスコアを 0 にしない (P-5 / T-40)。純粋な下方規則適合で
# (1+0-1)/2 = 0 になるのを避ける。
_SCORE_FLOOR = 0.05


class DrsaStrategy:
    name = "DRSA"

    def recommend(self, ctx: RecommendationContext) -> list[ScoredBooth]:
        ruleset = ctx.ruleset
        rules = getattr(ruleset, "rules", None)
        if ruleset is None or not rules:
            raise StrategyUnavailable("no cached DRSA rules")

        feats = compute_candidate_features(ctx)
        enabled = list(ctx.config.enabled_attributes)
        floor = min(1.0, max(0.0, float(ctx.config.drsa_coverage_floor)))

        out: list[ScoredBooth] = []
        for cand in ctx.request.candidates:
            f = feats[cand.booth_id]
            vector: dict[str, int] = {}
            for name in enabled:
                if name == "preference_match":
                    vector[name] = f.preference_match
                elif name == "rating_affinity":
                    vector[name] = f.rating_affinity
                elif name == "exploration_disposition" and f.exploration_disposition is not None:
                    vector[name] = f.exploration_disposition

            up, down, matched = ruleset.apply(vector)
            interest_term = _INTEREST_TERM[f.interest_match]
            if not matched:
                # 判断保留。関心項を低重みで混ぜて順序だけ付ける（0.5 付近に落ちる）。
                score = (1.0 - floor) * 0.5 + floor * interest_term
            else:
                score = (1.0 + up - down) / 2.0
            score = max(_SCORE_FLOOR, min(1.0, score))

            rule_summaries = [r.summary() for r in matched]
            attributes = {
                "v": ATTRIBUTES_SCHEMA_VERSION,
                "strategy": self.name,
                "enabled": enabled,
                "condition": dict(vector),
                "raw": {
                    **f.raw,
                    "up": round(up, 4),
                    "down": round(down, 4),
                    "matched_rules": len(matched),
                },
            }
            reason = {
                "v": 1,
                "strategy": self.name,
                "rules": rule_summaries,  # id と要約のみ。規則本体は入れない
                "tie_break": "visitor_count_asc",
                "engine": {"version": ctx.config.engine_version or __version__},
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
