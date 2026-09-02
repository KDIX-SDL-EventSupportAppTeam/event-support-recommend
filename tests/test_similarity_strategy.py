"""段4-a: SIMILARITY 戦略。

docs/specs/runtime-phase-switching/10-testing.md T-34〜T-36, T-39, T-40。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from event_support_recommend.engine import _runtime_config
from event_support_recommend.models import (
    CandidateBooth,
    InterestMatch,
    Participant,
    RecommendationContext,
    RequestContext,
    Survey,
)
from event_support_recommend.models import EventSnapshot
from event_support_recommend.settings import Settings
from event_support_recommend.strategies.base import StrategyUnavailable
from event_support_recommend.strategies.similarity import SimilarityStrategy


def _cfg():
    return _runtime_config(
        Settings(_env_file=None, enabled_attributes=["preference_match", "rating_affinity"])
    )


def _ctx(*, candidates, survey, snapshot_data, user_id="me"):
    cands = tuple(
        CandidateBooth(booth_id=b, category_id=c, visitor_count=v, is_active=True)
        for (b, c, v) in candidates
    )
    return RecommendationContext(
        request=RequestContext(
            user_id=user_id,
            cell_count=4,
            exclude_booth_ids=frozenset(),
            candidates=cands,
            received_at=datetime.now(timezone.utc),
            rating_scale=4,
            unlock_context="ctx",
        ),
        participant=Participant(user_id=user_id, survey=survey, visits=(), ratings=()),
        snapshot=EventSnapshot.unavailable(),
        config=_cfg(),
        snapshot_data=snapshot_data,
    )


def _survey(cats=("c1",), age="20s", occ="student", explore=2):
    return Survey(
        answered=True,
        interest_categories=tuple(cats),
        top_interest_category=cats[0] if cats else None,
        age_range=age,
        occupation=occ,
        exploration_disposition=explore,
    )


def _data(surveys, ratings_by_user, global_mean=0.5):
    return SimpleNamespace(
        surveys=surveys, ratings_by_user=ratings_by_user, global_mean=global_mean
    )


# --------------------------------------------------------------------------- #
# T-34: 近傍1人の高評価だけのブースが最上位に来ない（ベイズ縮約）
# --------------------------------------------------------------------------- #
def test_t34_single_high_rating_does_not_top():
    surveys = {
        "me": _survey(),
        "n1": _survey(),
        "n2": _survey(),
        "n3": _survey(),
        "n4": _survey(),
        "n5": _survey(),
    }
    ratings = {
        "n1": {"hype": 1.0},                    # 近傍1人だけが絶賛
        "n2": {"solid": 0.8},
        "n3": {"solid": 0.75},
        "n4": {"solid": 0.8},
        "n5": {"solid": 0.78},
    }
    ctx = _ctx(
        candidates=[("hype", "c1", 5), ("solid", "c1", 5)],
        survey=_survey(),
        snapshot_data=_data(surveys, ratings, global_mean=0.5),
    )
    out = {s.booth_id: s.score for s in SimilarityStrategy().recommend(ctx)}
    assert out["solid"] > out["hype"]  # よく支持された方が上


# --------------------------------------------------------------------------- #
# T-35: gender が近傍距離に影響しない
# --------------------------------------------------------------------------- #
def test_t35_gender_does_not_affect_neighbours():
    base = _survey()
    other = Survey(
        answered=True,
        interest_categories=("c1",),
        top_interest_category="c1",
        age_range="20s",
        occupation="student",
        gender="その他",
        exploration_disposition=2,
    )
    surveys_a = {"me": base, "n1": _survey(), "n2": _survey()}
    surveys_b = {"me": base, "n1": other, "n2": _survey()}
    ratings = {"n1": {"x": 0.9}, "n2": {"x": 0.1}}
    c = [("x", "c1", 3)]
    sa = SimilarityStrategy().recommend(_ctx(candidates=c, survey=base, snapshot_data=_data(surveys_a, ratings)))
    sb = SimilarityStrategy().recommend(_ctx(candidates=c, survey=base, snapshot_data=_data(surveys_b, ratings)))
    assert sa[0].score == sb[0].score


# --------------------------------------------------------------------------- #
# T-36: visitor_count を変えてもスコア順位が変わらない
# --------------------------------------------------------------------------- #
def test_t36_visitor_count_does_not_change_ranking():
    surveys = {"me": _survey(), "n1": _survey(), "n2": _survey()}
    ratings = {"n1": {"a": 0.9, "b": 0.2}, "n2": {"a": 0.8, "b": 0.3}}

    def ranked(vcounts):
        cands = [("a", "c1", vcounts[0]), ("b", "c1", vcounts[1]), ("c", "c2", vcounts[2])]
        out = SimilarityStrategy().recommend(
            _ctx(candidates=cands, survey=_survey(), snapshot_data=_data(surveys, ratings))
        )
        return [s.booth_id for s in sorted(out, key=lambda s: -s.score)], {s.booth_id: s.score for s in out}

    order1, scores1 = ranked([1, 1, 1])
    order2, scores2 = ranked([999, 2, 500])
    assert order1 == order2
    assert scores1 == scores2  # スコアそのものも不変


# --------------------------------------------------------------------------- #
# T-39: 全候補にスコアが付く（S-1）
# --------------------------------------------------------------------------- #
def test_t39_every_candidate_scored():
    surveys = {"me": _survey(), "n1": _survey(), "n2": _survey()}
    ratings = {"n1": {"a": 0.7}, "n2": {"a": 0.6}}
    cands = [("a", "c1", 3), ("b", "c2", 4), ("c", "c3", 5), ("d", "c1", 6)]
    out = SimilarityStrategy().recommend(
        _ctx(candidates=cands, survey=_survey(), snapshot_data=_data(surveys, ratings))
    )
    assert {s.booth_id for s in out} == {"a", "b", "c", "d"}
    assert all(0.0 <= s.score <= 1.0 for s in out)


# --------------------------------------------------------------------------- #
# T-40: interest_match = MISMATCH がスコア0にならない（P-5）
# --------------------------------------------------------------------------- #
def test_t40_mismatch_is_not_zero():
    surveys = {"me": _survey(cats=("c1",)), "n1": _survey(), "n2": _survey()}
    ratings = {"n1": {}, "n2": {}}  # 近傍評価ゼロ
    cands = [("mm", "zzz", 3)]  # カテゴリ zzz は関心外 → MISMATCH
    out = SimilarityStrategy().recommend(
        _ctx(candidates=cands, survey=_survey(cats=("c1",)), snapshot_data=_data(surveys, ratings, 0.5))
    )
    s = out[0]
    assert s.interest_match == InterestMatch.MISMATCH
    assert s.score > 0.0


# --------------------------------------------------------------------------- #
# 退避シグナル: StrategyUnavailable
# --------------------------------------------------------------------------- #
def test_unavailable_when_no_snapshot_data():
    with pytest.raises(StrategyUnavailable):
        SimilarityStrategy().recommend(
            _ctx(candidates=[("a", "c1", 1)], survey=_survey(), snapshot_data=None)
        )


def test_unavailable_when_no_neighbours():
    with pytest.raises(StrategyUnavailable):
        SimilarityStrategy().recommend(
            _ctx(candidates=[("a", "c1", 1)], survey=_survey(), snapshot_data=_data({"me": _survey()}, {}))
        )


def test_unavailable_when_participant_not_answered_and_absent():
    with pytest.raises(StrategyUnavailable):
        SimilarityStrategy().recommend(
            _ctx(
                candidates=[("a", "c1", 1)],
                survey=Survey.empty(),
                snapshot_data=_data({"n1": _survey(), "n2": _survey()}, {"n1": {"a": 0.5}}),
            )
        )
