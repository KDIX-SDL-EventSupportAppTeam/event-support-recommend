"""SIMILARITY — 属性が似た参加者が高評価したブース (docs/specs/04-strategies.md §3)。

決定表は育っていないが、参加者を数グループに割る程度の評価件数はある段階で使う。

**落としやすい点（03-strategy-wiring.md §「SIMILARITY の実装で特に注意すること」）:**

- ベイズ縮約は必須。無いと「近傍1人が高評価しただけ」のブースが最上位に来る
- `gender` を近傍の軸に入れない（去年の分布では「その他」が3名で個人を指しうる）
- `visitor_count` をスコアに入れない（S-2。人気度が復活する）

実行条件を満たせないとき（スナップショット無し／近傍が作れない／未回答）は
`StrategyUnavailable` を投げ、退避ラダー（engine.py）が COVERAGE へ落とす。
"""

from __future__ import annotations

from .. import __version__
from ..features import ATTRIBUTES_SCHEMA_VERSION, interest_match as interest_match_of
from ..models import InterestMatch, RecommendationContext, ScoredBooth, Survey
from .base import StrategyUnavailable

# interest_term: visitor_count に依存しない COVERAGE の関心項。近傍評価の無いブースの
# 同点化を避けるために低い重みで混ぜる。**visitor_count は混ぜない**（T-36）。
_INTEREST_TERM = {
    InterestMatch.MATCH: 1.0,
    InterestMatch.PARTIAL: 0.6,
    InterestMatch.MISMATCH: 0.2,
    InterestMatch.UNKNOWN: 0.2,
}

# 近傍距離の軸と重み (§3)。gender は入れない。
_W_INTEREST = 0.5
_W_AGE = 0.2
_W_OCC = 0.2
_W_EXPLORE = 0.1


def _as_axes(x) -> dict:
    """近傍距離に使う軸だけの dict へ正規化する。`Survey` でも dict でも受ける。

    `gender` は入れない（§3。去年の分布で「その他」が3名 → 個人特定になりうる）。
    """
    if isinstance(x, Survey):
        return {
            "interest_categories": list(x.interest_categories),
            "age_range": x.age_range,
            "occupation": x.occupation,
            "exploration_disposition": x.exploration_disposition,
        }
    d = dict(x or {})
    return {
        "interest_categories": list(d.get("interest_categories") or ()),
        "age_range": d.get("age_range"),
        "occupation": d.get("occupation"),
        "exploration_disposition": d.get("exploration_disposition"),
    }


def _jaccard(a, b) -> float:
    sa, sb = set(a or ()), set(b or ())
    if not sa and not sb:
        return 0.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def _distance(me: dict, other: dict) -> float | None:
    """0（同一）〜1（無関係）。比較できる軸が1つも無ければ None。"""
    total_w = 0.0
    acc = 0.0

    mi, oi = me.get("interest_categories"), other.get("interest_categories")
    if mi or oi:
        acc += _W_INTEREST * (1.0 - _jaccard(mi, oi))
        total_w += _W_INTEREST

    for key, w in (("age_range", _W_AGE), ("occupation", _W_OCC)):
        mv, ov = me.get(key), other.get(key)
        if mv is not None and ov is not None:
            acc += w * (0.0 if mv == ov else 1.0)
            total_w += w

    me_e, ot_e = me.get("exploration_disposition"), other.get("exploration_disposition")
    if me_e is not None and ot_e is not None:
        acc += _W_EXPLORE * min(1.0, abs(int(me_e) - int(ot_e)) * 0.5)
        total_w += _W_EXPLORE

    if total_w == 0.0:
        return None
    return acc / total_w


class SimilarityStrategy:
    name = "SIMILARITY"

    def recommend(self, ctx: RecommendationContext) -> list[ScoredBooth]:
        data = ctx.snapshot_data
        if data is None or not getattr(data, "surveys", None):
            raise StrategyUnavailable("snapshot cache not built")

        me = ctx.request.user_id
        my_survey = ctx.participant.survey
        if my_survey.answered:
            my_axes = _as_axes(my_survey)
        elif me in data.surveys:
            my_axes = _as_axes(data.surveys[me])
        else:
            raise StrategyUnavailable("participant has no pre-survey answers")

        # --- 近傍を作る ---
        scored_neighbors: list[tuple[float, str]] = []
        for uid, axes in data.surveys.items():
            if uid == me:
                continue
            d = _distance(my_axes, _as_axes(axes))
            if d is None or d >= 1.0:
                continue
            scored_neighbors.append((d, uid))
        if not scored_neighbors:
            raise StrategyUnavailable("no neighbours could be formed")

        scored_neighbors.sort(key=lambda t: (t[0], t[1]))
        k = max(1, int(ctx.config.similarity_neighbors))
        neighbours = [uid for _d, uid in scored_neighbors[:k]]

        ratings_by_user = getattr(data, "ratings_by_user", {}) or {}
        global_mean = float(getattr(data, "global_mean", 0.5) or 0.5)
        m = max(0.0, float(ctx.config.similarity_shrinkage))
        floor = min(1.0, max(0.0, float(ctx.config.similarity_coverage_floor)))

        out: list[ScoredBooth] = []
        for cand in ctx.request.candidates:
            im = interest_match_of(cand.category_id, my_survey)
            nb_ratings = [
                ratings_by_user[nb][cand.booth_id]
                for nb in neighbours
                if cand.booth_id in ratings_by_user.get(nb, {})
            ]
            n = len(nb_ratings)
            interest_term = _INTEREST_TERM[im]
            if n == 0:
                # 近傍評価なし → global_mean に落ちる。関心項を低重みで混ぜて同点化を避ける。
                score = (1.0 - floor) * global_mean + floor * interest_term
                shrunk = global_mean
            else:
                # ベイズ縮約（必須）。n 件を M 件ぶんの global_mean へ縮約する。
                shrunk = (sum(nb_ratings) + m * global_mean) / (n + m)
                score = shrunk
            score = max(0.0, min(1.0, score))

            attributes = {
                "v": ATTRIBUTES_SCHEMA_VERSION,
                "strategy": self.name,
                "enabled": list(ctx.config.enabled_attributes),
                "condition": {},
                "raw": {
                    "category_id": cand.category_id,
                    "visitor_count": cand.visitor_count,
                    "neighbour_count": len(neighbours),
                    "neighbour_ratings": n,
                    "shrunk_mean": round(shrunk, 4),
                    "global_mean": round(global_mean, 4),
                },
            }
            reason = {
                "v": 1,
                "strategy": self.name,
                "rules": [],
                "tie_break": "visitor_count_asc",
                "neighbours": len(neighbours),
                "shrinkage_m": m,
                "engine": {"version": ctx.config.engine_version or __version__},
            }
            out.append(
                ScoredBooth(
                    booth_id=cand.booth_id,
                    score=score,
                    interest_match=im,
                    attributes=attributes,
                    reason=reason,
                )
            )
        return out
