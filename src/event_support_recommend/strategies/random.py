"""RANDOM — 候補ごとに独立の一様乱数 (docs/decisions/adrs/0010-…, docs/specs/04-strategies.md §2.1)。

自動モードで品質ゲートを通る前の**正規の戦略**であり、DRSA が使えないときの退避先でもある。
かつては対照群・下限ベースラインとして interest_match の4区分単位で乱数を割り当てていた
（ADR 0007 §2 の P-6 を守るため）が、クラス内が同点になり順位が `visitor_count` 昇順で決まって
COVERAGE と同じ反人気バイアスが残った。**ADR 0007 の未決事項は「乱択性を採る」で決着した**
（2026-10-10, ADR 0010）。P-6 は課さない。

性質:
  - 各候補のスコアは、その候補だけから決まる独立の一様乱数。他の候補の有無・順序に依存しない
  - シードは `seed_for(user_id, unlock_context + "|random|" + booth_id)`。同じ入力なら同じ結果、
    参加者・解放ごとにばらける
  - 候補全件にスコアを返す (S-1)。スコアは 0 にしない (P-5。0 だと構造排除に見える)
  - `visitor_count`・関心一致を一切使わない。人気順にならないのは乱数であることで保たれる (S-2)
  - 契約の `phase` は 3 値なので COVERAGE として記録され、`reason.strategy = RANDOM` で区別される
"""

from __future__ import annotations

import random

from .. import __version__
from ..features import ATTRIBUTES_SCHEMA_VERSION
from ..models import RecommendationContext, ScoredBooth
from .base import seed_for
from .common import compute_candidate_features

# スコアを 0 にしない (P-5)。一様乱数の下端が 0 に張り付かない程度の小さな値。
_FLOOR = 1e-6


def random_score(user_id: str, unlock_context: str, booth_id: str) -> float:
    """候補 1 件ぶんの独立な一様乱数 (_FLOOR..1.0)。他の候補に依存しない。"""
    rng = random.Random(seed_for(user_id, f"{unlock_context}|random|{booth_id}"))
    return min(1.0, max(_FLOOR, round(rng.random(), 6)))


class RandomStrategy:
    name = "RANDOM"

    def recommend(self, ctx: RecommendationContext) -> list[ScoredBooth]:
        cfg = ctx.config
        feats = compute_candidate_features(ctx)

        out: list[ScoredBooth] = []
        for cand in ctx.request.candidates:
            f = feats[cand.booth_id]
            score = random_score(ctx.request.user_id, ctx.request.unlock_context, cand.booth_id)

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
                "raw": {**f.raw, "random_score": score},
            }
            reason = {
                "v": 1,
                "strategy": self.name,
                "rules": [],
                # 分析側がこの行だけを見て何をした戦略か判別できるようにする
                # (docs/specs/01-io-contract.md §3.3 (1))。
                "note": "independent uniform random per candidate; "
                        "ignores visitor_count and interest_match",
                "engine": {"version": cfg.engine_version or __version__, "rules_built_at": None},
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
