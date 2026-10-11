"""STRATEGY による戦略選択 (docs/decisions/adrs/0007-戦略の選択を環境変数で行う.md)。"""

from __future__ import annotations

from event_support_recommend.strategies import resolve_strategy
from event_support_recommend.strategies.coverage import CoverageStrategy
from event_support_recommend.strategies.random import RandomStrategy


def test_auto_resolves_to_random_first():
    # 自動モードの序盤はランダム（ADR 0010）。COVERAGE は自動では使わない
    s, note = resolve_strategy("auto", is_production=False)
    assert isinstance(s, RandomStrategy)
    assert note is None


def test_blank_and_none_default_to_auto():
    assert isinstance(resolve_strategy("", is_production=False)[0], RandomStrategy)
    assert isinstance(resolve_strategy(None, is_production=False)[0], RandomStrategy)


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
    assert isinstance(s, RandomStrategy)  # 自動モードの先頭（ランダム期）。固定指定とは別物
    assert note and "production" in note


def test_unknown_value_falls_back_to_auto_not_exception():
    s, note = resolve_strategy("bogus", is_production=False)
    assert isinstance(s, RandomStrategy)  # 起動を止めない (ADR 0007 §1)。auto として扱う
    assert note and "bogus" in note


# --- 退避ラダー (ADR 0010) ----------------------------------------------------
from event_support_recommend.models import Phase  # noqa: E402
from event_support_recommend.strategies.drsa import DrsaStrategy  # noqa: E402
from event_support_recommend.strategies.registry import build_ladder  # noqa: E402
from event_support_recommend.strategies.similarity import SimilarityStrategy  # noqa: E402


def _names(ladder):
    return [type(x) for x in ladder]


def test_auto_ladder_is_random_then_drsa_only():
    for phase in (Phase.COVERAGE, Phase.SIMILARITY):
        assert _names(build_ladder("auto", phase, is_production=False)) == [RandomStrategy]
    assert _names(build_ladder("auto", Phase.DRSA, is_production=False)) == [DrsaStrategy, RandomStrategy]


def test_auto_ladder_never_contains_coverage_or_similarity():
    for phase in Phase:
        for prod in (True, False):
            ladder = build_ladder("auto", phase, is_production=prod)
            assert not any(isinstance(x, (CoverageStrategy, SimilarityStrategy)) for x in ladder)
            assert isinstance(ladder[-1], RandomStrategy)  # 末尾は必ず成功するランダム


def test_pinned_random_in_production_is_treated_as_auto():
    assert _names(build_ladder("random", Phase.DRSA, is_production=True)) == [DrsaStrategy, RandomStrategy]
    assert _names(build_ladder("random", Phase.COVERAGE, is_production=False)) == [RandomStrategy]


def test_explicitly_pinned_coverage_and_similarity_still_work():
    assert _names(build_ladder("coverage", Phase.DRSA, is_production=True)) == [CoverageStrategy]
    assert _names(build_ladder("similarity", Phase.COVERAGE, is_production=False)) == [
        SimilarityStrategy,
        CoverageStrategy,
    ]
    assert _names(build_ladder("drsa", Phase.COVERAGE, is_production=False)) == [DrsaStrategy, RandomStrategy]
