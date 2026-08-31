"""段4-c: actual_phase の結線と退避ラダー（本仕様の核心）。

docs/specs/runtime-phase-switching/10-testing.md T-19〜T-33。
"""

from __future__ import annotations

import json

import pytest

from event_support_recommend.api.schemas import RecommendRequest
from event_support_recommend.cache import RuleCache, SnapshotCache
from event_support_recommend.drsa import DecisionTable, generate_rules
from event_support_recommend.drsa.decision_table import DecisionRow
from event_support_recommend.engine import _run_ladder, _runtime_config, run_recommendation
from event_support_recommend.models import DecisionClass, Phase
from event_support_recommend.settings import Settings
from event_support_recommend.strategies.base import StrategyUnavailable

H, L = DecisionClass.HIGH, DecisionClass.LOW
NAMES = ("preference_match", "rating_affinity")
BASE_ROWS = [
    DecisionRow((3, 3), H), DecisionRow((3, 2), H), DecisionRow((2, 2), H),
    DecisionRow((2, 1), L), DecisionRow((1, 1), L), DecisionRow((0, 2), L),
]


def _settings(**over):
    return Settings(_env_file=None, enabled_attributes=list(NAMES), **over)


def _req(uid="u1"):
    return RecommendRequest.model_validate({
        "user_id": uid,
        "cell_count": 4,
        "candidate_booths": [
            {"booth_id": "b1", "category_id": "hi", "visitor_count": 3},
            {"booth_id": "b2", "category_id": "mid", "visitor_count": 30},
            {"booth_id": "b3", "category_id": "out", "visitor_count": 12},
        ],
        "pre_survey": {"interest_categories": ["hi", "mid"], "top_interest_category": "hi"},
    })


def _neighbours(n=6):
    axes = {"interest_categories": ["hi", "mid"], "age_range": "20s", "occupation": "student"}
    surveys = {"u1": dict(axes)}
    ratings = {}
    for i in range(n):
        surveys[f"n{i}"] = dict(axes)
        ratings[f"n{i}"] = {"b1": 0.8, "b2": 0.7, "b3": 0.2}
    sc = SnapshotCache()
    sc.put(decision_table_size=99, surveys=surveys, ratings_by_user=ratings,
           booth_category={"b1": "hi", "b2": "mid", "b3": "out"}, global_mean=0.5)
    return sc


def _rules():
    return generate_rules(DecisionTable(NAMES, BASE_ROWS), min_support=1, consistency_level=0.8)


def _run(req, *, settings, rule_cache=None, snapshot_cache=None):
    return run_recommendation(
        req, settings=settings, rule_cache=rule_cache or RuleCache(),
        snapshot_cache=snapshot_cache,
    )


# --------------------------------------------------------------------------- #
# T-19〜T-21: 件数からフェーズ
# --------------------------------------------------------------------------- #
def test_t19_zero_table_is_coverage():
    assert _run(_req(), settings=_settings()).phase == "COVERAGE"


def test_t20_below_similarity_min_is_coverage():
    rc = RuleCache()
    rc.put(_rules(), decision_table_size=29, gamma=1.0)
    assert _run(_req(), settings=_settings(), rule_cache=rc, snapshot_cache=_neighbours()).phase == "COVERAGE"


def test_t21_exactly_similarity_min_is_similarity():
    rc = RuleCache()
    rc.put(_rules(), decision_table_size=30, gamma=1.0)
    resp = _run(_req(), settings=_settings(), rule_cache=rc, snapshot_cache=_neighbours())
    assert resp.phase == "SIMILARITY"


# --------------------------------------------------------------------------- #
# T-22 / T-23: DRSA ゲート
# --------------------------------------------------------------------------- #
def test_t22_drsa_min_with_gate_passed_is_drsa():
    rc = RuleCache()
    rc.put(_rules(), decision_table_size=80, gamma=1.0)
    resp = _run(_req(), settings=_settings(drsa_min_rules=1), rule_cache=rc, snapshot_cache=_neighbours())
    assert resp.phase == "DRSA"


def test_t23_drsa_min_but_gate_failed_is_similarity():
    rc = RuleCache()
    rc.put(_rules(), decision_table_size=80, gamma=0.1)  # γ ゲート不通過
    resp = _run(_req(), settings=_settings(drsa_min_rules=1), rule_cache=rc, snapshot_cache=_neighbours())
    assert resp.phase == "SIMILARITY"


# --------------------------------------------------------------------------- #
# T-24: 育てながら COVERAGE → SIMILARITY → DRSA と実際に切り替わる
# --------------------------------------------------------------------------- #
def test_t24_grows_through_all_three_phases():
    rc, sc = RuleCache(), _neighbours()
    s = _settings(drsa_min_rules=1)
    seen = []
    for size in (0, 10, 30, 45, 60, 90):
        if size:
            rc.put(_rules(), decision_table_size=size, gamma=1.0)
        seen.append(_run(_req(), settings=s, rule_cache=rc, snapshot_cache=sc).phase)
    assert seen[0] == "COVERAGE"
    assert "SIMILARITY" in seen
    assert seen[-1] == "DRSA"
    # 単調に上がっている（下がらない）
    order = {"COVERAGE": 0, "SIMILARITY": 1, "DRSA": 2}
    assert [order[p] for p in seen] == sorted(order[p] for p in seen)


def test_t25_shrinking_table_does_not_raise():
    rc, sc = RuleCache(), _neighbours()
    s = _settings(drsa_min_rules=1)
    rc.put(_rules(), decision_table_size=90, gamma=1.0)
    assert _run(_req(), settings=s, rule_cache=rc, snapshot_cache=sc).phase == "DRSA"
    rc.put(_rules(), decision_table_size=5, gamma=1.0)  # 縮んだ
    assert _run(_req(), settings=s, rule_cache=rc, snapshot_cache=sc).phase == "COVERAGE"


# --------------------------------------------------------------------------- #
# T-26〜T-29: 退避
# --------------------------------------------------------------------------- #
def test_t26_drsa_pinned_falls_to_similarity_when_no_rules():
    resp = _run(_req(), settings=_settings(strategy="drsa"), snapshot_cache=_neighbours())
    assert resp.phase == "SIMILARITY"


def test_t27_similarity_pinned_falls_to_coverage_without_snapshot():
    resp = _run(_req(), settings=_settings(strategy="similarity"), snapshot_cache=None)
    assert resp.phase == "COVERAGE"


def test_t28_unanswered_participant_falls_to_coverage():
    req = RecommendRequest.model_validate({
        "user_id": "solo", "cell_count": 4,
        "candidate_booths": [{"booth_id": "b1", "category_id": "hi", "visitor_count": 1}],
        "pre_survey": None,
    })
    resp = _run(req, settings=_settings(strategy="similarity"), snapshot_cache=_neighbours())
    assert resp.phase == "COVERAGE"


def test_t29_no_neighbours_falls_to_coverage():
    sc = SnapshotCache()
    sc.put(decision_table_size=50, surveys={"u1": {"interest_categories": ["hi"]}},
           ratings_by_user={}, booth_category={}, global_mean=0.5)
    resp = _run(_req(), settings=_settings(strategy="similarity"), snapshot_cache=sc)
    assert resp.phase == "COVERAGE"


# --------------------------------------------------------------------------- #
# T-30 / T-31 / T-32: 例外・予算・phase は実際の戦略
# --------------------------------------------------------------------------- #
class _Boom:
    name = "SIMILARITY"

    def recommend(self, ctx):
        raise RuntimeError("kaboom")


class _Slow:
    name = "SIMILARITY"

    def recommend(self, ctx):
        import time as _t
        _t.sleep(0.02)
        return []


def _ctx():
    from event_support_recommend.engine import _build_context
    return _build_context(_req(), _runtime_config(_settings()), __import__("datetime").datetime.now())


def test_t30_strategy_exception_falls_back(capsys):
    from event_support_recommend.strategies.coverage import CoverageStrategy

    scored, phase, name, reason = _run_ladder(
        [_Boom(), CoverageStrategy()], _ctx(), _runtime_config(_settings()),
        budget_ms=600, log_kind="recommend",
    )
    assert phase == Phase.COVERAGE and name == "COVERAGE"
    assert "exception" in reason
    assert scored  # 落ちずに結果が出る


def test_t31_budget_exceeded_falls_to_coverage():
    from event_support_recommend.strategies.coverage import CoverageStrategy

    scored, phase, name, reason = _run_ladder(
        [_Slow(), CoverageStrategy()], _ctx(), _runtime_config(_settings()),
        budget_ms=0, log_kind="recommend",
    )
    assert phase == Phase.COVERAGE
    assert "budget_exceeded" in reason


def test_t31_recommend_returns_200_even_with_zero_budget(client):
    # end-to-end: 予算 0 でも 200 / COVERAGE
    rc = RuleCache()
    rc.put(_rules(), decision_table_size=40, gamma=1.0)
    r = client.post("/recommend/cells", json={
        "user_id": "u1", "cell_count": 2,
        "candidate_booths": [{"booth_id": "b1", "category_id": "hi", "visitor_count": 1}],
        "pre_survey": {"interest_categories": ["hi"]},
    })
    assert r.status_code == 200


def test_t32_phase_reflects_actual_not_judged():
    # judged=SIMILARITY（size 40）だが snapshot 無し → 実際は COVERAGE
    rc = RuleCache()
    rc.put(_rules(), decision_table_size=40, gamma=1.0)
    resp = _run(_req(), settings=_settings(), rule_cache=rc, snapshot_cache=None)
    assert resp.phase == "COVERAGE"  # 判定結果(SIMILARITY)ではない


# --------------------------------------------------------------------------- #
# T-33: 退避理由が JSONL に出る
# --------------------------------------------------------------------------- #
def test_t41_no_popularity_regression_in_similarity_and_drsa():
    """全フェーズで score と visitor_count の順位相関が正にならない（S-2 / T-41）。"""
    from event_support_recommend.demo import spearman

    # 関心度の交絡を消すため全候補を同一カテゴリ（関心外）にし、visitor_count だけを変える。
    vc = [500, 5, 250, 40, 120]
    req = RecommendRequest.model_validate({
        "user_id": "u1", "cell_count": 4,
        "candidate_booths": [
            {"booth_id": f"b{i}", "category_id": "out", "visitor_count": v}
            for i, v in enumerate(vc)
        ],
        "pre_survey": {"interest_categories": ["hi", "mid"], "top_interest_category": "hi"},
    })
    sc = SnapshotCache()
    axes = {"interest_categories": ["hi", "mid"], "age_range": "20s", "occupation": "student"}
    surveys = {"u1": dict(axes)}
    ratings = {}
    for i in range(6):
        surveys[f"n{i}"] = dict(axes)
        ratings[f"n{i}"] = {f"b{j}": 0.6 for j in range(len(vc))}  # 全ブース同じ評価
    sc.put(decision_table_size=99, surveys=surveys, ratings_by_user=ratings,
           booth_category={f"b{j}": "out" for j in range(len(vc))}, global_mean=0.5)

    rc = RuleCache()
    for phase, size in (("SIMILARITY", 40), ("DRSA", 90)):
        rc.put(_rules(), decision_table_size=size, gamma=1.0)
        resp = _run(req, settings=_settings(drsa_min_rules=1), rule_cache=rc, snapshot_cache=sc)
        assert resp.phase == phase
        by_id = {s.booth_id: s.score for s in resp.scores}
        rho = spearman(vc, [by_id[f"b{i}"] for i in range(len(vc))])
        assert rho <= 0.2, (phase, rho)


def test_t33_fallback_reason_in_log(capsys):
    rc = RuleCache()
    rc.put(_rules(), decision_table_size=40, gamma=1.0)
    _run(_req(), settings=_settings(), rule_cache=rc, snapshot_cache=None)
    recs = [json.loads(l) for l in capsys.readouterr().out.splitlines() if '"kind":"recommend"' in l]
    assert recs
    last = recs[-1]
    assert last["judged_phase"] == "SIMILARITY"
    assert last["phase"] == "COVERAGE"
    assert last["fell_back"] is True
    assert last["fallback_reason"] and "SIMILARITY" in last["fallback_reason"]
