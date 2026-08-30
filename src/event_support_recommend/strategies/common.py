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

    free_cats = features.free_visit_categories(ctx.participant.visits)

    high_cats: set[str] = set()
    low_cats: set[str] = set()
    for r in ctx.participant.ratings:
        if r.category_id is None:
            continue
        if features.is_high_rating(
            r.rating,
            r.rating_scale or ctx.request.rating_scale,
            default_scale=cfg.rating_scale_default,
            high_ratio=cfg.high_rating_ratio,
        ):
            high_cats.add(r.category_id)
        if features.is_low_rating(
            r.rating,
            r.rating_scale or ctx.request.rating_scale,
            default_scale=cfg.rating_scale_default,
            low_ratio=cfg.low_rating_ratio,
        ):
            low_cats.add(r.category_id)

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


def _declared_signal(cand: CandidateBooth, survey) -> int:
    if not survey.answered or cand.category_id is None:
        return 0
    if survey.top_interest_category is not None and cand.category_id == survey.top_interest_category:
        return 2
    if cand.category_id in survey.interest_categories:
        return 1
    return 0
