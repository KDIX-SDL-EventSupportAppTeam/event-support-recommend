"""再現性 (docs/specs/07-testing.md §10 E-1..E-4) と
実験の発動条件 (§7 X-1, X-7)。"""

from __future__ import annotations

from event_support_recommend.api.schemas import RecommendRequest


def _req(uid="u1"):
    return RecommendRequest.model_validate(
        {
            "user_id": uid,
            "cell_count": 6,
            "candidate_booths": [
                {"booth_id": f"b{i}", "category_id": f"cat_{i%4}", "visitor_count": i * 7}
                for i in range(10)
            ],
            "pre_survey": {"interest_categories": ["cat_1"], "top_interest_category": "cat_1"},
        }
    )


def test_e1_same_input_same_output(run):
    a = run(_req())
    b = run(_req())
    assert [(s.booth_id, s.score, s.rank_in_event) for s in a.scores] == [
        (s.booth_id, s.score, s.rank_in_event) for s in b.scores
    ]
    assert [x.booth_id for x in a.assigned] == [x.booth_id for x in b.assigned]


def test_e2_different_participants_diverge(run):
    # 全候補が同点（同カテゴリ・同 visitor_count）になる入力では、並びはシード（user_id）で決まる。
    tied = RecommendRequest.model_validate(
        {
            "user_id": "PLACEHOLDER",
            "cell_count": 4,
            "candidate_booths": [
                {"booth_id": f"b{i}", "category_id": "cat_x", "visitor_count": 10}
                for i in range(8)
            ],
            "pre_survey": {"interest_categories": ["cat_a"], "top_interest_category": "cat_a"},
        }
    )
    a = [x.booth_id for x in run(tied.model_copy(update={"user_id": "uA"})).assigned]
    b = [x.booth_id for x in run(tied.model_copy(update={"user_id": "uB"})).assigned]
    assert a != b
    # 同じ参加者なら不変
    assert a == [x.booth_id for x in run(tied.model_copy(update={"user_id": "uA"})).assigned]


def test_e3_engine_version_stamped_on_every_row(run):
    resp = run(_req())
    for s in resp.scores:
        assert s.reason["engine"]["version"]


def test_x1_x7_no_split_before_quality_gate(run):
    """品質ゲート未通過（現状は常に未通過）では arm が付かず、scores は候補全件。"""
    resp = run(_req())
    assert len(resp.scores) == 10  # X-7: C-2 を壊さない
    for s in resp.scores:
        assert "arm" not in s.attributes  # X-1: 分割は発動しない
    for a in resp.assigned:
        assert "arm" not in a.attributes
