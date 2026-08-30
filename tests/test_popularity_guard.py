"""最重要 — 人気順への退化を禁じるテスト (docs/specs/07-testing.md §1 P-1..P-6)。

評価指標を「当たった率」に置かない。人気ブースを全員に薦めれば当たった率は上がるが失敗。
"""

from __future__ import annotations

from scipy.stats import spearmanr

from event_support_recommend.api.schemas import RecommendRequest


def _vc(score_entry) -> int:
    return score_entry.attributes["raw"]["visitor_count"]


def test_p1_p2_score_visitor_correlation_is_negative(run):
    req = RecommendRequest.model_validate(
        {
            "user_id": "u1",
            "cell_count": 6,
            "candidate_booths": [
                {"booth_id": f"b{i}", "category_id": f"cat_{i%3}", "visitor_count": v}
                for i, v in enumerate([1, 5, 9, 14, 20, 27, 33, 41, 55, 70])
            ],
            "pre_survey": {"interest_categories": ["cat_0"], "top_interest_category": "cat_0"},
        }
    )
    resp = run(req)
    scores = [s.score for s in resp.scores]
    vcounts = [_vc(s) for s in resp.scores]
    rho, _ = spearmanr(scores, vcounts)
    # P-1: 正にならない / P-2: COVERAGE では明確に負
    assert rho < -0.3


def test_p3_outlier_popular_booth_not_first_assigned(run):
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
    resp = run(req)
    assert resp.assigned[0].booth_id != "pop"


def test_p4_different_participants_get_different_recommendations(run):
    def req_for(uid, top):
        return RecommendRequest.model_validate(
            {
                "user_id": uid,
                "cell_count": 4,
                "candidate_booths": [
                    {"booth_id": "b1", "category_id": "cat_a", "visitor_count": 10},
                    {"booth_id": "b2", "category_id": "cat_b", "visitor_count": 10},
                    {"booth_id": "b3", "category_id": "cat_c", "visitor_count": 10},
                    {"booth_id": "b4", "category_id": "cat_d", "visitor_count": 10},
                ],
                "pre_survey": {"interest_categories": [top], "top_interest_category": top},
            }
        )

    a = run(req_for("ua", "cat_a"))
    b = run(req_for("ub", "cat_c"))
    assert [s.booth_id for s in a.assigned] != [s.booth_id for s in b.assigned]


def test_p5_mismatch_interest_term_is_not_zero(run):
    req = RecommendRequest.model_validate(
        {
            "user_id": "u1",
            "cell_count": 2,
            "candidate_booths": [
                {"booth_id": "b1", "category_id": "cat_x", "visitor_count": 5},
            ],
            "pre_survey": {"interest_categories": ["cat_a"], "top_interest_category": "cat_a"},
        }
    )
    resp = run(req)
    s = resp.scores[0]
    assert s.interest_match == "MISMATCH"
    assert s.score > 0.0  # 構造的に排除されない


def test_p6_same_interest_match_sorted_by_visitor_count_asc(run):
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
    resp = run(req)
    # 全て MISMATCH。rank_in_event が visitor_count 昇順に一致する
    by_rank = sorted(resp.scores, key=lambda s: s.rank_in_event)
    assert [s.booth_id for s in by_rank] == ["m_lo", "m_mid", "m_hi"]
