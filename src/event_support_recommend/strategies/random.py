"""RANDOM — 対照群・下限ベースライン (docs/decisions/adrs/0007-戦略の選択を環境変数で行う.md §2)。

**初期実装ではなく比較対象。** COVERAGE がランダムより本当に良いのかを示す下限として、
事前検証 (docs/specs/09-research-design.md) で使う。本番の既定にはしない
（`APP_ENV=production` では registry が auto へ落とす）。

不変条件の守り方 (docs/specs/07-testing.md §1):
  - S-1 候補全件を返す
  - S-3 例外を投げない
  - S-5 / P-6 同じ interest_match 内では score を一定にし、並べ替えを visitor_count 昇順へ委ねる
  - P-4 乱数シードは seed_for(user_id, unlock_context)。参加者ごとにばらける
  - P-5 MISMATCH / UNKNOWN の score を 0 にしない

visitor_count を一切見ない。そこが COVERAGE との違いであり、下限である理由。
"""

from __future__ import annotations

import random

from .. import __version__
from ..features import ATTRIBUTES_SCHEMA_VERSION
from ..models import InterestMatch, RecommendationContext, ScoredBooth
from .base import seed_for
from .common import compute_candidate_features

# MISMATCH / UNKNOWN をこの値より下げない (P-5)。
_FLOOR = 0.05
# クラスへ乱数を割り当てる固定順。シードが同じなら結果が同じになるよう順序を固定する。
_CLASS_ORDER = (
    InterestMatch.MATCH,
    InterestMatch.PARTIAL,
    InterestMatch.MISMATCH,
    InterestMatch.UNKNOWN,
)


class RandomStrategy:
    name = "RANDOM"

    def recommend(self, ctx: RecommendationContext) -> list[ScoredBooth]:
        cfg = ctx.config
        feats = compute_candidate_features(ctx)

        rng = random.Random(
            seed_for(ctx.request.user_id, ctx.request.unlock_context)
        )
        # interest_match クラスごとに一つの乱数値。クラス内は同点にして
        # 並べ替えを共通処理（visitor_count 昇順）へ委ねる → P-6 を壊さない。
        class_score = {
            cls: max(_FLOOR, round(rng.uniform(_FLOOR, 1.0), 6)) for cls in _CLASS_ORDER
        }

        out: list[ScoredBooth] = []
        for cand in ctx.request.candidates:
            f = feats[cand.booth_id]
            score = class_score[f.interest_match]

            condition = {}
            for attr in cfg.enabled_attributes:
                if attr == "preference_match":
                    condition[attr] = f.preference_match
                elif attr == "rating_affinity":
                    condition[attr] = f.rating_affinity
                elif attr == "exploration_disposition" and f.exploration_disposition is not None:
                    condition[attr] = f.exploration_disposition

            attributes = {
                "v": ATTRIBUTES_SCHEMA_VERSION,
                "strategy": self.name,
                "enabled": list(cfg.enabled_attributes),
                "condition": condition,
                "raw": {**f.raw, "class_score": score},
            }
            reason = {
                "v": 1,
                "strategy": self.name,
                "rules": [],
                "tie_break": "visitor_count_asc",
                "note": "baseline control: ignores visitor_count",
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
