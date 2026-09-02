"""フェーズ判定と品質ゲート (docs/specs/03-phases.md §6, docs/specs/07-testing.md §4)。"""

from __future__ import annotations

import math

import pytest

from event_support_recommend.models import Phase
from event_support_recommend.phases import decide_phase, evaluate_quality_gate
from event_support_recommend.settings import Settings


def s(**over) -> Settings:
    base = dict(_env_file=None, enabled_attributes=["preference_match", "rating_affinity"])
    base.update(over)
    return Settings(**base)


@pytest.mark.parametrize(
    "size,expected",
    [
        (29, Phase.COVERAGE),
        (30, Phase.SIMILARITY),
        (59, Phase.SIMILARITY),
        (60, Phase.SIMILARITY),  # 件数は足りるが gate 未通過 -> SIMILARITY に留まる
        (0, Phase.COVERAGE),
        (-5, Phase.COVERAGE),
        (None, Phase.COVERAGE),
        (float("nan"), Phase.COVERAGE),
    ],
)
def test_boundary_values(size, expected):
    assert decide_phase(size, s()) is expected


def test_threshold_is_configurable():
    assert decide_phase(45, s(phase_similarity_min=50)) is Phase.COVERAGE
    assert decide_phase(45, s(phase_similarity_min=40)) is Phase.SIMILARITY


def test_gate_all_pass_promotes_to_drsa():
    gate = evaluate_quality_gate(60, 3, 0.6, 0.6, s())
    assert gate.passed is True
    assert decide_phase(60, s(), gate=gate) is Phase.DRSA


@pytest.mark.parametrize(
    "rules,gamma,cov",
    [
        (2, 0.6, 0.6),  # 規則不足
        (3, 0.4, 0.6),  # γ 不足
        (3, 0.6, 0.3),  # 被覆率不足
    ],
)
def test_gate_any_missing_stays_similarity(rules, gamma, cov):
    gate = evaluate_quality_gate(60, rules, gamma, cov, s())
    assert gate.passed is False
    assert decide_phase(60, s(), gate=gate) is Phase.SIMILARITY


def test_gate_detail_is_itemised():
    gate = evaluate_quality_gate(60, 2, 0.4, 0.6, s())
    d = gate.detail.as_dict()
    assert d == {"size": True, "rules": False, "gamma": False, "coverage": True}


def test_zero_rules_never_returns_drsa():
    gate = evaluate_quality_gate(9999, 0, 1.0, 1.0, s())
    assert decide_phase(9999, s(), gate=gate) is not Phase.DRSA


def test_enabled_attributes_third_moves_default_drsa_min_to_180():
    assert s().default_phase_drsa_min == 60
    assert s(
        enabled_attributes=["preference_match", "rating_affinity", "exploration_disposition"]
    ).default_phase_drsa_min == 180


def test_no_exception_on_weird_size():
    for bad in (None, float("nan"), float("inf"), -1, True):
        decide_phase(bad, s())
        evaluate_quality_gate(bad, 0, float("nan"), float("nan"), s())
