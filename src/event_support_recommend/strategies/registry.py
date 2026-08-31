"""戦略の選択口 (docs/decisions/adrs/0007-戦略の選択を環境変数で行う.md)。

`STRATEGY` 環境変数（`Settings.strategy`）から具象戦略を解決する。
`engine.py` は具象クラスを import せず、ここを通す。

| 値 | 意味 |
|---|---|
| `auto`（既定） | フェーズ判定に従う。段3・4 未結線のため現状は必ず COVERAGE |
| `coverage` | フェーズ判定を無視して COVERAGE に固定 |
| `random` | RANDOM（下限ベースライン）に固定。`APP_ENV=production` では auto に落とす |

未知の値は例外にせず auto として扱い、警告文字列を返す（起動ログに出す）。
設定ミスでサービスが起動しないのは、このサービスの立ち位置に対して過剰
（落ちても本体は止まらない）。段3・4 の実装時はこの表に1行足すだけで済む。
"""

from __future__ import annotations

from .base import Strategy
from .coverage import CoverageStrategy
from .random import RandomStrategy

_FIXED: dict[str, type] = {
    "coverage": CoverageStrategy,
    "random": RandomStrategy,
}


def resolve_strategy(name: str | None, *, is_production: bool) -> tuple[Strategy, str | None]:
    """(戦略インスタンス, 警告文字列 or None) を返す。

    警告は起動ログの `startup.strategy_note` に載せる想定。
    """
    raw = (name or "auto").strip().lower()

    if raw == "random" and is_production:
        return CoverageStrategy(), (
            "STRATEGY=random is refused in production (research data protection); using auto"
        )
    if raw in ("", "auto"):
        # 段3・4 未結線: auto は必ず COVERAGE に落ちる (docs/specs/08-architecture.md §6)。
        return CoverageStrategy(), None
    cls = _FIXED.get(raw)
    if cls is None:
        return CoverageStrategy(), f"unknown STRATEGY={name!r}; falling back to auto"
    return cls(), None
