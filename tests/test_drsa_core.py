"""DRSA コア — 手計算できる小さな決定表で検証する
(docs/specs/05-drsa.md §6, docs/specs/07-testing.md §5)。"""

from __future__ import annotations

import random

import pytest

from event_support_recommend.drsa import (
    DecisionTable,
    approximate,
    dominated_indices,
    dominating_indices,
    generate_rules,
)
from event_support_recommend.drsa.decision_table import DecisionRow
from event_support_recommend.models import DecisionClass

H, L = DecisionClass.HIGH, DecisionClass.LOW

# docs/specs/05-drsa.md §6 の表
BASE_ROWS = [
    DecisionRow((3, 3), H),
    DecisionRow((3, 2), H),
    DecisionRow((2, 2), H),
    DecisionRow((2, 1), L),
    DecisionRow((1, 1), L),
    DecisionRow((0, 2), L),
]
NAMES = ("preference_match", "rating_affinity")


@pytest.fixture
def table() -> DecisionTable:
    return DecisionTable(NAMES, BASE_ROWS)


def test_dominating_sets_match_hand_calc(table):
    assert dominating_indices(table, 0) == frozenset({0})
    assert dominating_indices(table, 1) == frozenset({0, 1})
    assert dominating_indices(table, 2) == frozenset({0, 1, 2})
    assert dominating_indices(table, 3) == frozenset({0, 1, 2, 3})
    assert dominating_indices(table, 4) == frozenset({0, 1, 2, 3, 4})
    assert dominating_indices(table, 5) == frozenset({0, 1, 2, 5})


def test_dominated_sets_match_hand_calc(table):
    assert dominated_indices(table, 0) == frozenset({0, 1, 2, 3, 4, 5})
    assert dominated_indices(table, 1) == frozenset({1, 2, 3, 4, 5})
    assert dominated_indices(table, 2) == frozenset({2, 3, 4, 5})
    assert dominated_indices(table, 3) == frozenset({3, 4})
    assert dominated_indices(table, 4) == frozenset({4})
    assert dominated_indices(table, 5) == frozenset({5})


def test_strict_approximation_at_l_equals_one(table):
    ap = approximate(table, consistency_level=1.0)
    assert ap.lower_upward == frozenset({0, 1, 2})
    assert ap.lower_downward == frozenset({3, 4, 5})
    assert ap.boundary == frozenset()
    assert ap.gamma == pytest.approx(1.0)


def test_contradiction_row_falls_into_boundary(table):
    rows = [*BASE_ROWS, DecisionRow((3, 3), L)]  # 上位 (3,3) なのに LOW
    contradictory = DecisionTable(NAMES, rows)
    ap = approximate(contradictory, consistency_level=1.0)
    assert len(ap.boundary) > 0
    assert ap.gamma < 1.0


def test_vc_drsa_relaxes_lower_approximation(table):
    # l を下げると、矛盾を含む優越集合でも確実側に入りうる
    strict = approximate(table, consistency_level=1.0)
    relaxed = approximate(table, consistency_level=0.6)
    assert relaxed.lower_upward >= strict.lower_upward


def test_generate_rules_are_sound_and_minimal(table):
    rs = generate_rules(table, min_support=2, consistency_level=1.0)
    for r in rs.rules:
        assert r.support >= 2
        assert r.confidence >= 1.0 - 1e-9  # l=1.0 なので確実規則のみ
    assert len(rs.certain_up) >= 1 and len(rs.certain_down) >= 1
    # 高選好領域を覆う上方規則と、低選好領域を覆う下方規則が存在する
    assert any(rs_r.matches({"preference_match": 3, "rating_affinity": 3}) for rs_r in rs.certain_up)
    assert any(rs_r.matches({"preference_match": 0, "rating_affinity": 2}) for rs_r in rs.certain_down)
    # 冗長規則が落ちている: 同一被覆でより厳しい条件の規則は残らない
    by_cov: dict[frozenset, int] = {}
    for r in rs.rules:
        by_cov.setdefault(r.coverage, 0)
        by_cov[r.coverage] += 1
    assert all(count == 1 for count in by_cov.values())


def test_rule_ids_are_stable_across_regeneration(table):
    a = generate_rules(table, min_support=2, consistency_level=1.0)
    b = generate_rules(table, min_support=2, consistency_level=1.0)
    assert [r.id for r in a.rules] == [r.id for r in b.rules]


def test_generation_is_order_independent(table):
    ref = generate_rules(table, min_support=2, consistency_level=1.0)
    ref_ids = sorted(r.id for r in ref.rules)
    rng = random.Random(0)
    for _ in range(5):
        shuffled = list(BASE_ROWS)
        rng.shuffle(shuffled)
        got = generate_rules(DecisionTable(NAMES, shuffled), min_support=2, consistency_level=1.0)
        assert sorted(r.id for r in got.rules) == ref_ids


def test_low_support_rules_are_dropped(table):
    rs = generate_rules(table, min_support=5, consistency_level=1.0)
    assert rs.rules == ()  # support 1〜2 しか作れないので0本。例外は投げない


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [DecisionRow((2, 2), H)],
        [DecisionRow((2, 2), H), DecisionRow((3, 1), H)],  # 全部同じクラス
    ],
)
def test_degenerate_tables_do_not_raise(rows):
    t = DecisionTable(NAMES, rows)
    ap = approximate(t, consistency_level=0.8)
    assert 0.0 <= ap.gamma <= 1.0
    rs = generate_rules(t, min_support=1, consistency_level=0.8)
    assert isinstance(rs.rules, tuple)


def test_apply_aggregates_confidence(table):
    rs = generate_rules(table, min_support=2, consistency_level=1.0)
    up, down, matched = rs.apply({"preference_match": 3, "rating_affinity": 3})
    assert up == pytest.approx(1.0)
    assert down == 0.0
    up2, down2, _ = rs.apply({"preference_match": 0, "rating_affinity": 1})
    assert down2 == pytest.approx(1.0)
    assert up2 == 0.0
