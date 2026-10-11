"""品質ゲートのリフト条件と、上下合わせた数え方（issue #39）。

γ（近似の質）は予測力を測らない。★4 を付けやすい会場では、好みと無関係な
「どの条件でも高評価」という偽の規則が確信度だけでゲートを通る。確信度が基準率を
`drsa_min_lift` 以上上回る規則だけを数えて、これを防ぐ。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from event_support_recommend.api.schemas import RecommendRequest
from event_support_recommend.cache import RuleCache
from event_support_recommend.drsa import DecisionTable, generate_rules
from event_support_recommend.drsa.decision_table import DecisionRow
from event_support_recommend.engine import run_recommendation
from event_support_recommend.models import DecisionClass
from event_support_recommend.phases import count_effective_rules, evaluate_quality_gate
from event_support_recommend.settings import Settings
from event_support_recommend.snapshot_build import base_rates

H, L = DecisionClass.HIGH, DecisionClass.LOW
NAMES = ("preference_match", "rating_affinity")


def _s(**over) -> Settings:
    return Settings(_env_file=None, enabled_attributes=list(NAMES), **over)


def _rule(confidence):
    return SimpleNamespace(confidence=confidence)


def _rs(up=(), down=()):
    return SimpleNamespace(certain_up=tuple(_rule(c) for c in up), certain_down=tuple(_rule(c) for c in down))


# --- 既定値 -----------------------------------------------------------------
def test_defaults_follow_adr_0010():
    s = _s()
    assert (s.drsa_consistency, s.drsa_min_rules, s.drsa_min_lift) == (0.6, 2, 0.10)
    assert (s.min_support, s.phase_drsa_min, s.drsa_min_gamma, s.drsa_min_coverage) == (5, 60, 0.5, 0.5)
    assert s.high_rating_ratio == 0.75  # 4段階の★4 だけが高評価


# --- 数え方 -----------------------------------------------------------------
def test_up_and_down_rules_are_both_counted():
    # 基準率: HIGH 0.4 / LOW 0.6。上方 0.7（+0.3）と下方 0.8（+0.2）はどちらも有効
    assert count_effective_rules(_rs(up=[0.7], down=[0.8]), 0.4, 0.6, 0.10) == 2
    assert count_effective_rules(_rs(up=[0.7]), 0.4, 0.6, 0.10) == 1
    assert count_effective_rules(_rs(down=[0.8]), 0.4, 0.6, 0.10) == 1


def test_up_compares_with_high_rate_and_down_with_low_rate():
    # 下方規則 0.7 は LOW 率 0.65 に対して +0.05 → 無効。HIGH 率 0.35 と比べてはいけない
    assert count_effective_rules(_rs(down=[0.7]), 0.35, 0.65, 0.10) == 0
    # 上方規則 0.5 は HIGH 率 0.35 に対して +0.15 → 有効。LOW 率 0.65 と比べてはいけない
    assert count_effective_rules(_rs(up=[0.5]), 0.35, 0.65, 0.10) == 1


@pytest.mark.parametrize(
    "conf,expected",
    [(0.599, 0), (0.6, 1), (0.601, 1)],  # 基準率 0.5・リフト 0.10 → 0.6 ちょうどは有効（>=）
)
def test_lift_boundary(conf, expected):
    assert count_effective_rules(_rs(up=[conf]), 0.5, 0.5, 0.10) == expected


def test_lift_boundary_is_robust_to_float_rounding():
    assert 0.6 - 0.5 < 0.10  # 浮動小数の素の比較では落ちる（0.0999…）
    assert count_effective_rules(_rs(up=[0.6]), 0.5, 0.5, 0.10) == 1


def test_no_base_rate_counts_nothing():
    assert count_effective_rules(_rs(up=[1.0], down=[1.0]), None, None, 0.10) == 0
    assert count_effective_rules(None, 0.5, 0.5, 0.10) == 0


# --- ゲート境界（件数 59/60、規則 1/2 本）-----------------------------------
@pytest.mark.parametrize("size,rules,ok", [(59, 2, False), (60, 2, True), (60, 1, False), (60, 3, True)])
def test_gate_boundaries(size, rules, ok):
    gate = evaluate_quality_gate(size, rules, 0.6, 0.6, _s())
    assert gate.passed is ok


# --- ★4 を付けやすい会場の偽の規則 ------------------------------------------
def _table(rows_per_pattern, high_of):
    """属性パターン 4x3=12 通りを rows_per_pattern 行ずつ。high_of(pm, ra) = HIGH の行数。"""
    rows = []
    for pm in range(4):
        for ra in (1, 2, 3):
            h = high_of(pm, ra)
            rows += [DecisionRow((pm, ra), H)] * h + [DecisionRow((pm, ra), L)] * (rows_per_pattern - h)
    return DecisionTable(NAMES, rows)


def _gate_inputs(table, settings):
    rs = generate_rules(table, min_support=settings.min_support, consistency_level=settings.drsa_consistency)
    records = [{"decision": r.decision.value} for r in table.rows]
    high, low = base_rates(records)
    return rs, high, low


def test_generous_venue_fake_rules_do_not_pass_the_gate():
    """好みと無関係に 90% が★4 の会場。どのパターンも確信度 0.9 の「規則」になるが、基準率も 0.9。"""
    s = _s()
    table = _table(10, lambda pm, ra: 9)  # 120 行・HIGH 率 0.9・好みの差は一切無い
    rs, high, low = _gate_inputs(table, s)
    raw = len(rs.certain_up) + len(rs.certain_down)
    assert raw >= 1  # 確信度 0.9 の「規則」は出てしまう（γ も 1.0）
    # リフトを見ない数え方なら、最小本数 1 のゲートを素通りする
    assert evaluate_quality_gate(len(table), raw, 1.0, 1.0, _s(drsa_min_rules=1)).passed is True
    # リフト条件があると 1 本も数えない
    eff = count_effective_rules(rs, high, low, s.drsa_min_lift)
    assert eff == 0
    assert evaluate_quality_gate(len(table), eff, 1.0, 1.0, _s(drsa_min_rules=1)).passed is False


def test_real_preference_signal_passes_the_gate():
    """pm が高いほど★4 が付く会場（好みの差がある）。規則は基準率を大きく上回る。"""
    s = _s()
    table = _table(10, lambda pm, ra: {0: 0, 1: 1, 2: 9, 3: 10}[pm])
    rs, high, low = _gate_inputs(table, s)
    eff = count_effective_rules(rs, high, low, s.drsa_min_lift)
    assert eff >= 2
    up_only = count_effective_rules(_rs(up=[r.confidence for r in rs.certain_up]), high, None, s.drsa_min_lift)
    assert eff > up_only  # 下方の規則も数えている
    assert evaluate_quality_gate(len(table), eff, 1.0, 1.0, s).passed is True


# --- エンジン経由（RuleCache の基準率が使われる）--------------------------------
def _run(rc, settings):
    class St:
        pass

    st = St()
    req = RecommendRequest.model_validate(
        {
            "user_id": "u1",
            "cell_count": 2,
            "candidate_booths": [
                {"booth_id": "b1", "category_id": "hi", "visitor_count": 3},
                {"booth_id": "b2", "category_id": "out", "visitor_count": 9},
            ],
            "pre_survey": {"interest_categories": ["hi"], "top_interest_category": "hi"},
        }
    )
    run_recommendation(req, settings=settings, rule_cache=rc, app_state=st)
    return st.last_gate


def test_engine_uses_cached_base_rates_for_the_gate():
    s = _s()
    # 同じ規則でも、基準率が高い会場（0.95）ではゲートを通らない
    informative = _table(10, lambda pm, ra: {0: 0, 1: 1, 2: 9, 3: 10}[pm])
    rs = generate_rules(informative, min_support=5, consistency_level=0.6)
    ok = RuleCache()
    ok.put(rs, decision_table_size=120, gamma=1.0, base_rate_high=0.5, base_rate_low=0.5)
    assert _run(ok, s).gate.detail.rules is True

    generous = RuleCache()
    generous.put(rs, decision_table_size=120, gamma=1.0, base_rate_high=0.95, base_rate_low=0.95)
    last = _run(generous, s)
    assert last.gate.detail.rules is False and last.gate.passed is False
    assert last.certain_rules_count == len(rs.certain_up) + len(rs.certain_down)
    assert last.effective_rules_count < last.certain_rules_count

    unknown = RuleCache()  # 基準率が無ければ数えない（安全側）
    unknown.put(rs, decision_table_size=120, gamma=1.0)
    assert _run(unknown, s).gate.detail.rules is False
