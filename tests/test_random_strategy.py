"""RANDOM — 候補ごとに独立の一様乱数 (ADR 0010, docs/specs/04-strategies.md §2.1)。

固定する性質: 候補ごとに独立・シードで再現・候補全件・0 点にしない。
人気順に退化しないこと（P-1）は test_popularity_guard.py でも多数の参加者で固定している。
ADR 0007 の未決事項は「乱択性を採る」で決着した。P-6（同じ関心一致内は visitor_count 昇順）は
RANDOM に課さない。
"""

from __future__ import annotations

import statistics

import pytest

from event_support_recommend.api.schemas import RecommendRequest
from event_support_recommend.cache import RuleCache
from event_support_recommend.engine import run_recommendation
from event_support_recommend.settings import Settings
from event_support_recommend.strategies.random import random_score

COUNTS = [2, 6, 11, 18, 27, 39, 52, 70, 95, 130]


@pytest.fixture
def run_random():
    settings = Settings(
        _env_file=None,
        enabled_attributes=["preference_match", "rating_affinity"],
        strategy="random",
        app_env="development",
        ops_token="",
    )

    def _run(req: RecommendRequest):
        return run_recommendation(req, settings=settings, rule_cache=RuleCache())

    return _run


def _req(uid: str, *, cell_count: int = 4, ids=range(10), unlock: str | None = None) -> RecommendRequest:
    body = {
        "user_id": uid,
        "cell_count": cell_count,
        "candidate_booths": [
            {"booth_id": f"b{i}", "category_id": f"cat_{i % 4}", "visitor_count": COUNTS[i]} for i in ids
        ],
        "pre_survey": {"interest_categories": ["cat_0", "cat_1"], "top_interest_category": "cat_0"},
    }
    if unlock is not None:
        body["unlock_context"] = unlock
    return RecommendRequest.model_validate(body)


def _by_id(resp):
    return {s.booth_id: s.score for s in resp.scores}


def test_s1_returns_every_candidate(run_random):
    resp = run_random(_req("u1"))
    assert len(resp.scores) == 10


def test_scores_are_uniform_not_stuck_to_a_few_values(run_random):
    # 旧実装は interest_match の4区分ごとに1つの値（高々4種類）。候補ごとに別の値になること
    scores = [s.score for s in run_random(_req("u1")).scores]
    assert len(set(scores)) == len(scores)
    allv = [s.score for i in range(100) for s in run_random(_req(f"u{i}")).scores]
    assert 0.4 < statistics.mean(allv) < 0.6  # 一様乱数なら 0.5
    assert min(allv) < 0.05 and max(allv) > 0.95


def test_each_candidate_is_independent_of_the_others(run_random):
    full = _by_id(run_random(_req("u1")))
    subset = _by_id(run_random(_req("u1", ids=[1, 4, 7])))
    reordered = _by_id(run_random(_req("u1", ids=list(reversed(range(10))))))
    for bid, score in subset.items():
        assert score == full[bid]  # 他の候補が居なくても、その候補の値は変わらない
    assert reordered == full  # 並びを変えても変わらない


def test_same_input_is_reproducible_and_seed_varies_by_user_and_unlock(run_random):
    a = run_random(_req("u1", unlock="card1:2"))
    b = run_random(_req("u1", unlock="card1:2"))
    assert _by_id(a) == _by_id(b)
    assert [x.booth_id for x in a.assigned] == [x.booth_id for x in b.assigned]
    assert _by_id(run_random(_req("u2", unlock="card1:2"))) != _by_id(a)  # 参加者ごとにばらける
    assert _by_id(run_random(_req("u1", unlock="card1:4"))) != _by_id(a)  # 解放ごとにばらける


def test_random_score_function_is_pure_and_in_range():
    assert random_score("u", "ctx", "b1") == random_score("u", "ctx", "b1")
    assert random_score("u", "ctx", "b1") != random_score("u", "ctx", "b2")
    for i in range(500):
        assert 0.0 < random_score(f"u{i}", "ctx", "b") <= 1.0


def test_p5_never_zero_even_for_mismatch(run_random):
    req = RecommendRequest.model_validate(
        {
            "user_id": "u1",
            "cell_count": 2,
            "candidate_booths": [{"booth_id": "b1", "category_id": "cat_x", "visitor_count": 5}],
            "pre_survey": {"interest_categories": ["cat_a"], "top_interest_category": "cat_a"},
        }
    )
    s = run_random(req).scores[0]
    assert s.interest_match == "MISMATCH"
    assert s.score > 0.0
    assert all(x.score > 0.0 for i in range(50) for x in run_random(_req(f"u{i}")).scores)


def test_ignores_visitor_count_and_interest_match(run_random):
    """同じ候補 ID・同じ参加者なら、visitor_count や関心一致を変えても値は同じ（人気も関心も見ていない）。"""
    base = _by_id(run_random(_req("u1")))
    req = RecommendRequest.model_validate(
        {
            "user_id": "u1",
            "cell_count": 4,
            "candidate_booths": [
                {"booth_id": f"b{i}", "category_id": "cat_other", "visitor_count": 9999 - i}
                for i in range(10)
            ],
            "pre_survey": None,
        }
    )
    assert _by_id(run_random(req)) == base


def test_independent_candidates_are_not_systematically_popularity_ordered(run_random):
    """P-1: 系統的な正の相関が無い。個々の参加者では揺れるので多数で見る。"""
    from scipy.stats import spearmanr

    rhos = []
    for i in range(200):
        resp = run_random(_req(f"user_{i}"))
        rho, _ = spearmanr([s.score for s in resp.scores], [s.attributes["raw"]["visitor_count"] for s in resp.scores])
        rhos.append(float(rho))
    assert abs(statistics.mean(rhos)) <= 0.1


def test_single_interest_class_input_still_varies_across_participants(run_random):
    """旧実装は候補の関心一致が1クラスだと全員同じ並び（visitor_count 昇順）になった。いまは参加者ごとに変わる。"""
    tied = {
        "cell_count": 4,
        "candidate_booths": [
            {"booth_id": f"b{i}", "category_id": "cat_x", "visitor_count": v} for i, v in enumerate(COUNTS)
        ],
        "pre_survey": None,
    }
    seen = {
        tuple(a.booth_id for a in run_random(RecommendRequest.model_validate({**tied, "user_id": f"u{i}"})).assigned)
        for i in range(20)
    }
    assert len(seen) > 5


def test_log_carries_strategy_field(run_random, capsys):
    run_random(_req("u1"))
    lines = [l for l in capsys.readouterr().out.splitlines() if '"kind":"recommend"' in l]
    assert lines and '"strategy":"RANDOM"' in lines[-1]
    assert '"phase":"COVERAGE"' in lines[-1]  # 契約の phase は3値のまま (ADR 0007 §4)
