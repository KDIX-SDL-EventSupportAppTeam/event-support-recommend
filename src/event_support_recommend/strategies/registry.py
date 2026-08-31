"""戦略の選択口 (docs/decisions/adrs/0007-戦略の選択を環境変数で行う.md)。

`STRATEGY` 環境変数（`Settings.strategy`）から戦略／退避ラダーを解決する。
`engine.py` は具象クラスを import せず、ここを通す。

| 値 | 意味 |
|---|---|
| `auto`（既定） | フェーズ判定に従う。段3・4 結線後はこれが実際に機能する |
| `coverage` | COVERAGE に固定 |
| `similarity` | SIMILARITY に固定。実行できなければ COVERAGE へ退避 |
| `drsa` | DRSA に固定。実行できなければラダーを下る |
| `random` | RANDOM に固定。`APP_ENV=production` では `auto` に落とす（従来どおり） |

**当日はこれを触らない**（ADR 0009）。障害時に COVERAGE へ固定するための逃げ道。
未知の値は例外にせず `auto` として扱う。
"""

from __future__ import annotations

from ..models import Phase
from .base import Strategy
from .coverage import CoverageStrategy
from .drsa import DrsaStrategy
from .random import RandomStrategy
from .similarity import SimilarityStrategy

_CTORS: dict[str, type] = {
    "coverage": CoverageStrategy,
    "similarity": SimilarityStrategy,
    "drsa": DrsaStrategy,
    "random": RandomStrategy,
}

# 判定フェーズ → 試す順（04-strategies.md §5 の退避ラダー）。末尾は必ず coverage。
_PHASE_LADDER: dict[str, tuple[str, ...]] = {
    Phase.COVERAGE.value: ("coverage",),
    Phase.SIMILARITY.value: ("similarity", "coverage"),
    Phase.DRSA.value: ("drsa", "similarity", "coverage"),
}
_FIXED_START = {"coverage": Phase.COVERAGE, "similarity": Phase.SIMILARITY, "drsa": Phase.DRSA}


def resolve_strategy(name: str | None, *, is_production: bool) -> tuple[Strategy, str | None]:
    """(戦略インスタンス, 警告文字列 or None)。警告は起動ログの `startup.strategy_note`。"""
    raw = (name or "auto").strip().lower()
    if raw == "random" and is_production:
        return CoverageStrategy(), (
            "STRATEGY=random is refused in production (research data protection); using auto"
        )
    if raw in ("", "auto"):
        return CoverageStrategy(), None
    cls = _CTORS.get(raw)
    if cls is None:
        return CoverageStrategy(), f"unknown STRATEGY={name!r}; falling back to auto"
    note = None
    if raw in ("similarity", "drsa"):
        note = f"STRATEGY={raw} pinned; falls back down the ladder when it cannot run"
    return cls(), note


def build_ladder(
    name: str | None, judged_phase: Phase, *, is_production: bool
) -> list[Strategy]:
    """試す順に並べた戦略インスタンスのリスト。末尾は必ず COVERAGE（必ず成功する）。"""
    raw = (name or "auto").strip().lower()

    if raw == "random":
        if is_production:
            return [CoverageStrategy()]
        return [RandomStrategy(), CoverageStrategy()]

    if raw in _FIXED_START:
        keys = _PHASE_LADDER[_FIXED_START[raw].value]
    else:  # auto / 未知 → 判定に従う
        keys = _PHASE_LADDER.get(judged_phase.value, ("coverage",))
    return [_CTORS[k]() for k in keys]


_NAME_TO_PHASE = {
    "COVERAGE": Phase.COVERAGE,
    "SIMILARITY": Phase.SIMILARITY,
    "DRSA": Phase.DRSA,
    "RANDOM": Phase.COVERAGE,  # RANDOM は契約の3値に無い。COVERAGE として記録する
}


def phase_of(strategy_name: str) -> Phase:
    return _NAME_TO_PHASE.get(strategy_name, Phase.COVERAGE)
