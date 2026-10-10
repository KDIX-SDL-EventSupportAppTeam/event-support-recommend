"""戦略の選択口 (docs/decisions/adrs/0007-戦略の選択を環境変数で行う.md)。

`STRATEGY` 環境変数（`Settings.strategy`）から戦略／退避ラダーを解決する。
`engine.py` は具象クラスを import せず、ここを通す。

| 値 | 意味 |
|---|---|
| `auto`（既定） | フェーズ判定に従う。ゲート不通過は RANDOM、通過後は DRSA（使えなければ RANDOM） |
| `coverage` | COVERAGE（訪問者が少ない順）に固定。**自動モードでは使わない**戦略を明示するときだけ |
| `similarity` | SIMILARITY に固定。同上。実行できなければ COVERAGE へ退避 |
| `drsa` | DRSA に固定。実行できなければ RANDOM へ退避 |
| `random` | RANDOM に固定。`APP_ENV=production` では `auto` に落とす（自動モードのランダム期とは別物） |

自動モードの段階（ADR 0010）: `COVERAGE` 期（ゲート不通過）→ `[RANDOM]`、`DRSA` 判定 → `[DRSA, RANDOM]`。
**SIMILARITY と COVERAGE は自動モードで使わない。**

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

# 自動モード: 判定フェーズ → 試す順（04-strategies.md §5 の退避ラダー）。末尾は必ず random。
# SIMILARITY / COVERAGE は入れない (ADR 0010)。
_AUTO_LADDER: dict[str, tuple[str, ...]] = {
    Phase.COVERAGE.value: ("random",),
    Phase.SIMILARITY.value: ("random",),  # decide_phase は返さないが、来ても使わない
    Phase.DRSA.value: ("drsa", "random"),
}
# STRATEGY で明示したときのラダー。coverage / similarity は明示したときだけ使える。
_PINNED_LADDER: dict[str, tuple[str, ...]] = {
    "coverage": ("coverage",),
    "similarity": ("similarity", "coverage"),
    "drsa": ("drsa", "random"),
}


def resolve_strategy(name: str | None, *, is_production: bool) -> tuple[Strategy, str | None]:
    """(戦略インスタンス, 警告文字列 or None)。警告は起動ログの `startup.strategy_note`。"""
    raw = (name or "auto").strip().lower()
    if raw == "random" and is_production:
        return RandomStrategy(), (
            "STRATEGY=random is refused in production (research data protection); using auto"
        )
    if raw in ("", "auto"):
        return RandomStrategy(), None
    cls = _CTORS.get(raw)
    if cls is None:
        return RandomStrategy(), f"unknown STRATEGY={name!r}; falling back to auto"
    note = None
    if raw in ("similarity", "drsa"):
        note = f"STRATEGY={raw} pinned; falls back down the ladder when it cannot run"
    return cls(), note


def build_ladder(
    name: str | None, judged_phase: Phase, *, is_production: bool
) -> list[Strategy]:
    """試す順に並べた戦略インスタンスのリスト。

    自動モードの末尾は必ず RANDOM（候補だけで動くので必ず成功する）。
    本番で `STRATEGY=random` が指定されても拒否して自動モードとして扱う。
    """
    raw = (name or "auto").strip().lower()

    if raw == "random" and not is_production:
        return [RandomStrategy()]
    if raw in _PINNED_LADDER:
        keys = _PINNED_LADDER[raw]
    else:  # auto / 未知 / 本番での random → 判定に従う
        keys = _AUTO_LADDER.get(judged_phase.value, ("random",))
    return [_CTORS[k]() for k in keys]


_NAME_TO_PHASE = {
    "COVERAGE": Phase.COVERAGE,
    "SIMILARITY": Phase.SIMILARITY,
    "DRSA": Phase.DRSA,
    "RANDOM": Phase.COVERAGE,  # RANDOM は契約の3値に無い。COVERAGE として記録する
}


def phase_of(strategy_name: str) -> Phase:
    return _NAME_TO_PHASE.get(strategy_name, Phase.COVERAGE)
