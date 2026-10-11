"""戦略が共通で使う、候補ごとの特徴量の組み立て。

features/ を呼ぶだけの薄い層。features/ 自体は純関数（分析側が import する公開 API）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import features
from ..models import (
    CandidateBooth,
    InterestMatch,
    RecommendationContext,
)


@dataclass(frozen=True)
class CandidateFeatures:
    booth_id: str
    category_id: str | None
    visitor_count: int
    interest_match: InterestMatch
    preference_match: int
    rating_affinity: int
    exploration_disposition: int | None
    raw: dict


def compute_candidate_features(ctx: RecommendationContext) -> dict[str, CandidateFeatures]:
    survey = ctx.participant.survey
    cfg = ctx.config

    # 決定表 (snapshot_build.py) と同じ関数を通す。ここだけ別計算にしない。
    free_cats, high_cats, low_cats = features.history_signals(
        ctx.participant.visits,
        default_scale=cfg.rating_scale_default,
        high_ratio=cfg.high_rating_ratio,
        low_ratio=cfg.low_rating_ratio,
        request_scale=ctx.request.rating_scale,
    )

    explore = features.exploration_disposition(survey)

    out: dict[str, CandidateFeatures] = {}
    for cand in ctx.request.candidates:
        im = features.interest_match(cand.category_id, survey)
        pm = features.preference_match(cand.category_id, survey, free_cats)
        ra = features.rating_affinity(cand.category_id, high_cats, low_cats)
        out[cand.booth_id] = CandidateFeatures(
            booth_id=cand.booth_id,
            category_id=cand.category_id,
            visitor_count=cand.visitor_count,
            interest_match=im,
            preference_match=pm,
            rating_affinity=ra,
            exploration_disposition=explore,
            raw={
                "category_id": cand.category_id,
                "visitor_count": cand.visitor_count,
                "declared": _declared_signal(cand, survey),
                "behavioral": 1 if cand.category_id in free_cats else 0,
            },
        )
    return out


def vector_from_features(f: CandidateFeatures, enabled) -> dict[str, int]:
    """有効な条件属性だけの順序ベクトル（DRSA 規則の適合判定・被覆率算出に使う）。"""
    out: dict[str, int] = {}
    for name in enabled:
        if name == "preference_match":
            out[name] = f.preference_match
        elif name == "rating_affinity":
            out[name] = f.rating_affinity
        elif name == "exploration_disposition" and f.exploration_disposition is not None:
            out[name] = f.exploration_disposition
    return out


def _declared_signal(cand: CandidateBooth, survey) -> int:
    if not survey.answered or cand.category_id is None:
        return 0
    if survey.top_interest_category is not None and cand.category_id == survey.top_interest_category:
        return 2
    if cand.category_id in survey.interest_categories:
        return 1
    return 0
