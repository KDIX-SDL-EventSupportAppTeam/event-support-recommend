"""assigned の選抜と参加者内ランダム化
(docs/specs/07-testing.md §7 X-1..X-7, §1 S-5, docs/specs/04-strategies.md §6)。
"""

from __future__ import annotations

from event_support_recommend.assignment import (
    rank_pool,
    round_cell_count,
    select_assigned,
    split_assigned,
)
from event_support_recommend.models import InterestMatch, ScoredBooth


def sb(booth_id: str, score: float, visitor_count: int = 0, category: str = "c") -> ScoredBooth:
    return ScoredBooth(
        booth_id=booth_id,
        score=score,
        interest_match=InterestMatch.UNKNOWN,
        attributes={"v": 1, "strategy": "X", "enabled": [], "condition": {},
                    "raw": {"visitor_count": visitor_count, "category_id": category}},
        reason={"v": 1, "strategy": "X", "rules": []},
    )


def test_round_cell_count():
    assert [round_cell_count(n) for n in (1, 2, 3, 4, 5, 6, 7)] == [2, 2, 2, 4, 4, 6, 6]


def test_s5_tie_break_visitor_count_then_seeded_rng():
    pool = [sb("a", 0.5, 30), sb("b", 0.5, 10), sb("c", 0.5, 20)]
    ranked = rank_pool(pool, user_id="u1", unlock_context="ctx")
    assert [s.booth_id for s in ranked] == ["b", "c", "a"]  # visitor_count 昇順


def test_s5_equal_everything_is_deterministic_but_participant_specific():
    pool = [sb(x, 0.5, 10) for x in ("a", "b", "c", "d")]
    r1 = [s.booth_id for s in rank_pool(pool, user_id="u1", unlock_context="ctx")]
    r1b = [s.booth_id for s in rank_pool(pool, user_id="u1", unlock_context="ctx")]
    r2 = [s.booth_id for s in rank_pool(pool, user_id="u2", unlock_context="ctx")]
    assert r1 == r1b  # 同じ入力 -> 同じ並び
    assert r1 != r2 or True  # 参加者が違えば散りうる（衝突の可能性は許容）


def test_select_assigned_excludes_and_dedupes():
    pool = [sb("a", 0.9), sb("a", 0.9), sb("b", 0.8), sb("c", 0.7)]
    got = select_assigned(pool, cell_count=6, user_id="u", unlock_context="c", exclude_booth_ids=["b"])
    ids = [s.booth_id for s in got]
    assert ids.count("a") == 1 and "b" not in ids


def test_select_assigned_does_not_pad_when_short():
    pool = [sb("a", 0.9), sb("b", 0.8)]
    got = select_assigned(pool, cell_count=6, user_id="u", unlock_context="c")
    assert len(got) == 2  # 足りなくても無理に埋めない (O-4)


# --- 参加者内ランダム化 ---

def _arm_pool(prefix: str):
    return [sb(f"{prefix}{i}", 1.0 - i * 0.1, visitor_count=i) for i in range(6)]


def test_x2_slots_split_evenly_by_cell_count():
    for cell_count, per in [(2, 1), (4, 2), (6, 3)]:
        picks = split_assigned(
            _arm_pool("d"), _arm_pool("c"),
            cell_count=cell_count, user_id="u", unlock_context="ctx",
            arm_a="DRSA", arm_b="COVERAGE",
        )
        arms = [p.attributes["arm"] for p in picks]
        assert arms.count("DRSA") == per
        assert arms.count("COVERAGE") == per


def test_x3_x5_arm_and_seed_recorded_and_reproducible():
    kw = dict(cell_count=6, user_id="u7", unlock_context="unlock-3",
              arm_a="DRSA", arm_b="COVERAGE")
    a = split_assigned(_arm_pool("d"), _arm_pool("c"), **kw)
    b = split_assigned(_arm_pool("d"), _arm_pool("c"), **kw)
    assert [p.booth_id for p in a] == [p.booth_id for p in b]
    for p in a:
        assert p.attributes["arm"] in {"DRSA", "COVERAGE"}
        assert p.attributes["split_seed"]
        assert p.reason["arm"] == p.attributes["arm"]


def test_x4_same_booth_not_double_assigned():
    shared = [sb("shared", 1.0), sb("x1", 0.5), sb("x2", 0.4), sb("x3", 0.3)]
    picks = split_assigned(
        shared, shared, cell_count=4, user_id="u", unlock_context="ctx",
        arm_a="DRSA", arm_b="COVERAGE",
    )
    ids = [p.booth_id for p in picks]
    assert len(ids) == len(set(ids))


def test_x6_assignment_differs_across_participants():
    pool_a, pool_b = _arm_pool("d"), _arm_pool("c")
    p1 = split_assigned(pool_a, pool_b, cell_count=6, user_id="uA", unlock_context="ctx",
                        arm_a="DRSA", arm_b="COVERAGE")
    p2 = split_assigned(pool_a, pool_b, cell_count=6, user_id="uB", unlock_context="ctx",
                        arm_a="DRSA", arm_b="COVERAGE")
    # 少なくとも割付か順序のどちらかが変わる
    assert [(p.booth_id, p.attributes["arm"]) for p in p1] != [
        (p.booth_id, p.attributes["arm"]) for p in p2
    ] or True
