"""段4-b: DRSA 戦略の結線。

docs/specs/runtime-phase-switching/10-testing.md T-37〜T-40。
規則生成は行わず、キャッシュ済み規則を当てはめるだけであることを確認する。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from event_support_recommend.drsa import DecisionTable, generate_rules
from event_support_recommend.drsa.decision_table import DecisionRow
from event_support_recommend.engine import _runtime_config
from event_support_recommend.models import (
    CandidateBooth,
    DecisionClass,
    EventSnapshot,
    InterestMatch,
    Participant,
    RecommendationContext,
    RequestContext,
    Survey,
)
from event_support_recommend.settings import Settings
from event_support_recommend.strategies.base import StrategyUnavailable
from event_support_recommend.strategies.drsa import DrsaStrategy

H, L = DecisionClass.HIGH, DecisionClass.LOW
NAMES = ("preference_match", "rating_affinity")
BASE_ROWS = [
    DecisionRow((3, 3), H), DecisionRow((3, 2), H), DecisionRow((2, 2), H),
    DecisionRow((2, 1), L), DecisionRow((1, 1), L), DecisionRow((0, 2), L),
]


def _ruleset(min_support=3):
    return generate_rules(DecisionTable(NAMES, BASE_ROWS), min_support=min_support, consistency_level=0.8)


def _survey():
    return Survey(
        answered=True,
        interest_categories=("hi", "mid"),
        top_interest_category="hi",
        age_range="20s",
        occupation="student",
    )


def _ctx(ruleset):
    cands = (
        CandidateBooth("A", "hi", 5, True),    # pm=2 -> 上方規則に適合
        CandidateBooth("B", "mid", 5, True),   # pm=1 -> 適合なし
        CandidateBooth("C", "out", 5, True),   # pm=0 -> 適合なし・MISMATCH
    )
    return RecommendationContext(
        request=RequestContext(
            user_id="u1", cell_count=4, exclude_booth_ids=frozenset(),
            candidates=cands, received_at=datetime.now(timezone.utc),
            rating_scale=4, unlock_context="c",
        ),
        participant=Participant(user_id="u1", survey=_survey(), visits=(), ratings=()),
        snapshot=EventSnapshot.unavailable(),
        config=_runtime_config(Settings(_env_file=None, enabled_attributes=list(NAMES))),
        ruleset=ruleset,
    )


# --------------------------------------------------------------------------- #
# T-37: 適合規則0本の候補が 0.5 付近に落ち、順序が付く
# --------------------------------------------------------------------------- #
def test_t37_unmatched_candidates_near_half_and_ordered():
    out = {s.booth_id: s for s in DrsaStrategy().recommend(_ctx(_ruleset()))}
    assert out["A"].score == pytest.approx(1.0)  # 上方規則 conf=1.0 に適合
    for bid in ("B", "C"):
        assert 0.4 <= out[bid].score <= 0.6
        assert not out[bid].reason["rules"]
    assert out["B"].score != out["C"].score  # 関心項で順序が付く（PARTIAL > MISMATCH）
    assert out["B"].score > out["C"].score


# --------------------------------------------------------------------------- #
# T-38: reason.rules に規則本体が入らない（id と要約のみ）
# --------------------------------------------------------------------------- #
def test_t38_reason_rules_are_summaries_only():
    out = {s.booth_id: s for s in DrsaStrategy().recommend(_ctx(_ruleset()))}
    rules = out["A"].reason["rules"]
    assert rules and all(set(r) == {"id", "class", "support", "confidence"} for r in rules)
    assert not any("conditions" in r or "coverage" in r for r in rules)


# --------------------------------------------------------------------------- #
# T-39: 全候補にスコアが付く（S-1）
# --------------------------------------------------------------------------- #
def test_t39_every_candidate_scored():
    out = DrsaStrategy().recommend(_ctx(_ruleset()))
    assert {s.booth_id for s in out} == {"A", "B", "C"}
    assert all(0.0 <= s.score <= 1.0 for s in out)


# --------------------------------------------------------------------------- #
# T-40: interest_match = MISMATCH がスコア0にならない（P-5）
# --------------------------------------------------------------------------- #
def test_t40_mismatch_not_zero_even_with_down_rule():
    # min_support=1 だと preference_match<=1 -> <=LOW(conf 1.0) が出る。
    # 候補 C(pm=0) はこの下方規則に適合し raw score (1+0-1)/2 = 0 → 下限 0.05 へ。
    out = {s.booth_id: s for s in DrsaStrategy().recommend(_ctx(_ruleset(min_support=1)))}
    assert out["C"].interest_match == InterestMatch.MISMATCH
    assert out["C"].score > 0.0


# --------------------------------------------------------------------------- #
# 退避シグナル
# --------------------------------------------------------------------------- #
def test_unavailable_when_ruleset_none():
    with pytest.raises(StrategyUnavailable):
        DrsaStrategy().recommend(_ctx(None))


def test_unavailable_when_ruleset_empty():
    empty = generate_rules(DecisionTable(NAMES, []), min_support=1, consistency_level=0.8)
    with pytest.raises(StrategyUnavailable):
        DrsaStrategy().recommend(_ctx(empty))


# --------------------------------------------------------------------------- #
# 規則生成をしない（当てはめるだけ）
# --------------------------------------------------------------------------- #
def test_does_not_regenerate_rules():
    rs = _ruleset()

    class Guard:
        rules = rs.rules

        def apply(self, vec):
            return rs.apply(vec)

        def __getattr__(self, name):  # generate_* を呼んだら失敗させる
            raise AssertionError(f"unexpected access: {name}")

    out = DrsaStrategy().recommend(_ctx(Guard()))
    assert len(out) == 3
