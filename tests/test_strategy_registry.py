"""STRATEGY による戦略選択 (docs/decisions/adrs/0007-戦略の選択を環境変数で行う.md)。"""

from __future__ import annotations

from event_support_recommend.strategies import resolve_strategy
from event_support_recommend.strategies.coverage import CoverageStrategy
from event_support_recommend.strategies.random import RandomStrategy


def test_auto_resolves_to_coverage_while_stages_unwired():
    s, note = resolve_strategy("auto", is_production=False)
    assert isinstance(s, CoverageStrategy)
    assert note is None


def test_blank_and_none_default_to_auto():
    assert isinstance(resolve_strategy("", is_production=False)[0], CoverageStrategy)
    assert isinstance(resolve_strategy(None, is_production=False)[0], CoverageStrategy)


def test_coverage_is_pinned():
    s, note = resolve_strategy("coverage", is_production=True)
    assert isinstance(s, CoverageStrategy)
    assert note is None


def test_random_selectable_outside_production():
    s, note = resolve_strategy("RaNdOm", is_production=False)
    assert isinstance(s, RandomStrategy)
    assert note is None


def test_random_refused_in_production_falls_back_to_auto_with_warning():
    s, note = resolve_strategy("random", is_production=True)
    assert isinstance(s, CoverageStrategy)  # ADR 0007 §3
    assert note and "production" in note


def test_unknown_value_falls_back_to_auto_not_exception():
    s, note = resolve_strategy("bogus", is_production=False)
    assert isinstance(s, CoverageStrategy)  # 起動を止めない (ADR 0007 §1)
    assert note and "bogus" in note
