"""最重要 — 人気順への退化を禁じるテスト (docs/specs/07-testing.md §1 P-1..P-6)。

評価指標を「当たった率」に置かない。人気ブースを全員に薦めれば当たった率は上がるが失敗。

2026-10-10（ADR 0010）に自動モードの段階が「ランダム → DRSA」になった。序盤は COVERAGE ではなく
ランダムなので、P-2（スコアと訪問者数が明確に負）は COVERAGE 限定の性質になった。
**弱めて済ませない:** 守る対象は三つの段階それぞれに残している。
  - COVERAGE（`STRATEGY=coverage` で明示。従来の P-1〜P-6 をそのまま維持）
  - ランダム期（自動モードの序盤）: 多数の参加者で、系統的な正の相関・人気ブースの先頭化が無いこと
  - DRSA 期: 同上（目安 +0.15 以下。資料 6章 S-4）
"""

from __future__ import annotations

import statistics

import pytest
from scipy.stats import spearmanr

from event_support_recommend.api.schemas import RecommendRequest
from event_support_recommend.cache import RuleCache
from event_support_recommend.drsa import DecisionTable, generate_rules
from event_support_recommend.drsa.decision_table import DecisionRow
from event_support_recommend.engine import run_recommendation
from event_support_recommend.models import DecisionClass
from event_support_recommend.settings import Settings

COUNTS = [1, 5, 9, 14, 20, 27, 33, 41, 55, 70]
USERS = 200


def _vc(score_entry) -> int:
    return score_entry.attributes["raw"]["visitor_count"]


def _settings(**over) -> Settings:
    return Settings(
        _env_file=None, enabled_attributes=["preference_match", "rating_affinity"], ops_token="", **over
    )


@pytest.fixture
def run_coverage():
    s = _settings(strategy="coverage")
    return lambda req: run_recommendation(req, settings=s, rule_cache=RuleCache())


@pytest.fixture
def run_auto():
    """自動モード・規則なし = ランダム期。"""
    s = _settings()
    return lambda req: run_recommendation(req, settings=s, rule_cache=RuleCache())


def _req(uid="u1", *, cell_count=6, counts=COUNTS, cats=3, survey=True):
    return RecommendRequest.model_validate(
        {
            "user_id": uid,
            "cell_count": cell_count,
            "candidate_booths": [
                {"booth_id": f"b{i}", "category_id": f"cat_{i % cats}", "visitor_count": v}
                for i, v in enumerate(counts)
            ],
            "pre_survey": {"interest_categories": ["cat_0"], "top_interest_category": "cat_0"}
            if survey
            else None,
        }
    )


def _rho(resp) -> float:
    rho, _ = spearmanr([s.score for s in resp.scores], [_vc(s) for s in resp.scores])
    return float(rho)


# =========================================================================== #
# COVERAGE（STRATEGY=coverage で明示）— 従来の P-1〜P-6 をそのまま維持
# =========================================================================== #
def test_p1_p2_coverage_score_visitor_correlation_is_negative(run_coverage):
    # P-1: 正にならない / P-2: COVERAGE では明確に負
    assert _rho(run_coverage(_req())) < -0.3


def test_p3_coverage_outlier_popular_booth_not_first_assigned(run_coverage):
    req = RecommendRequest.model_validate(
        {
            "user_id": "u1",
            "cell_count": 4,
            "candidate_booths": [
                {"booth_id": "pop", "category_id": "cat_0", "visitor_count": 9999},
                {"booth_id": "b1", "category_id": "cat_0", "visitor_count": 2},
                {"booth_id": "b2", "category_id": "cat_1", "visitor_count": 4},
                {"booth_id": "b3", "category_id": "cat_2", "visitor_count": 6},
            ],
            "pre_survey": {"interest_categories": ["cat_0"], "top_interest_category": "cat_0"},
        }
    )
    assert run_coverage(req).assigned[0].booth_id != "pop"


def test_p6_coverage_same_interest_match_sorted_by_visitor_count_asc(run_coverage):
    req = RecommendRequest.model_validate(
        {
            "user_id": "u1",
            "cell_count": 6,
            "candidate_booths": [
                {"booth_id": "m_hi", "category_id": "cat_x", "visitor_count": 80},
                {"booth_id": "m_lo", "category_id": "cat_y", "visitor_count": 3},
                {"booth_id": "m_mid", "category_id": "cat_z", "visitor_count": 30},
            ],
            "pre_survey": {"interest_categories": ["cat_a"], "top_interest_category": "cat_a"},
        }
    )
    by_rank = sorted(run_coverage(req).scores, key=lambda s: s.rank)
    assert [s.booth_id for s in by_rank] == ["m_lo", "m_mid", "m_hi"]


# =========================================================================== #
# ランダム期（自動モードの序盤）
# 乱数なので 1 件では揺れる。多数の参加者で「系統的な偏り」が無いことを固定する。
# =========================================================================== #
def test_p1_random_phase_no_systematic_positive_correlation(run_auto):
    rhos = [_rho(run_auto(_req(f"user_{i}"))) for i in range(USERS)]
    assert all(run_auto(_req(f"user_{i}")).phase == "COVERAGE" for i in range(3))
    # 正にならない（P-1）。期待値は 0 付近（4σ ≒ 0.1 の余裕）
    assert statistics.mean(rhos) <= 0.1, statistics.mean(rhos)
    # 逆向き（COVERAGE と同じ反人気）にも偏らない = 乱数であること。人気順の裏返しも隠れた偏り
    assert abs(statistics.mean(rhos)) <= 0.1


def test_p3_random_phase_popular_outlier_is_not_systematically_first(run_auto):
    counts = [9999, 2, 4, 6]  # 先頭が突出して人気

    def first(uid):
        req = RecommendRequest.model_validate(
            {
                "user_id": uid,
                "cell_count": 4,
                "candidate_booths": [
                    {"booth_id": f"b{i}", "category_id": "cat_0", "visitor_count": v}
                    for i, v in enumerate(counts)
                ],
                "pre_survey": {"interest_categories": ["cat_0"], "top_interest_category": "cat_0"},
            }
        )
        return run_auto(req).assigned[0].booth_id

    share = sum(first(f"u{i}") == "b0" for i in range(USERS)) / USERS
    assert share <= 0.4, share  # 一様なら 0.25。人気順（常に先頭）なら 1.0


def test_p4_random_phase_different_participants_get_different_recommendations(run_auto):
    seen = {tuple(a.booth_id for a in run_auto(_req(f"u{i}")).assigned) for i in range(10)}
    assert len(seen) > 1


def test_p5_random_phase_mismatch_is_not_structurally_excluded(run_auto):
    req = RecommendRequest.model_validate(
        {
            "user_id": "u1",
            "cell_count": 2,
            "candidate_booths": [{"booth_id": "b1", "category_id": "cat_x", "visitor_count": 5}],
            "pre_survey": {"interest_categories": ["cat_a"], "top_interest_category": "cat_a"},
        }
    )
    s = run_auto(req).scores[0]
    assert s.interest_match == "MISMATCH"
    assert s.score > 0.0  # 構造的に排除されない


# =========================================================================== #
# DRSA 期 — 人気順に退化していないこと（目安: 順位相関の平均が +0.15 以下, 資料 6章 S-4）
# =========================================================================== #
H, L = DecisionClass.HIGH, DecisionClass.LOW
_NAMES = ("preference_match", "rating_affinity")
_ROWS = [
    DecisionRow((3, 3), H), DecisionRow((3, 2), H), DecisionRow((2, 2), H),
    DecisionRow((2, 1), L), DecisionRow((1, 1), L), DecisionRow((0, 2), L),
]


@pytest.fixture
def run_drsa():
    s = _settings(drsa_min_rules=1)
    rc = RuleCache()
    rs = generate_rules(DecisionTable(_NAMES, _ROWS), min_support=1, consistency_level=0.8)
    rc.put(rs, decision_table_size=90, gamma=1.0, base_rate_high=0.5, base_rate_low=0.5)

    def _run(req):
        resp = run_recommendation(req, settings=s, rule_cache=rc)
        assert resp.phase == "DRSA", "このテストは DRSA 期でなければ意味が無い"
        return resp

    return _run


def test_p1_drsa_phase_no_systematic_positive_correlation(run_drsa):
    # 訪問者数はカテゴリと無関係に散らす（関心の強いカテゴリが人気、という交絡を入れない）。
    # 偶数番=cat_0、奇数番=cat_1 で、両カテゴリに同じ分布の訪問者数を割り当てる。
    counts = [1, 70, 14, 41, 27, 27, 41, 14, 70, 1]
    rhos = [_rho(run_drsa(_req(f"user_{i}", counts=counts, cats=2))) for i in range(60)]
    assert statistics.mean(rhos) <= 0.15, statistics.mean(rhos)


def test_p3_drsa_phase_popular_outlier_not_first_assigned(run_drsa):
    req = RecommendRequest.model_validate(
        {
            "user_id": "u1",
            "cell_count": 4,
            "candidate_booths": [
                {"booth_id": "pop", "category_id": "hi", "visitor_count": 9999},
                {"booth_id": "b1", "category_id": "hi", "visitor_count": 2},
                {"booth_id": "b2", "category_id": "hi", "visitor_count": 4},
                {"booth_id": "b3", "category_id": "hi", "visitor_count": 6},
            ],
            "pre_survey": {"interest_categories": ["hi"], "top_interest_category": "hi"},
        }
    )
    assert run_drsa(req).assigned[0].booth_id != "pop"
