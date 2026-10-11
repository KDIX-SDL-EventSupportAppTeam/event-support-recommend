"""strategies/ — COVERAGE / RANDOM / (SIMILARITY / DRSA) / フォールバック。

FastAPI・SQL・HTTP を知らない。RecommendationContext だけを見る
(docs/specs/08-architecture.md §1, docs/specs/04-strategies.md §0 S-4)。

自動モードで使うのは RANDOM（ゲート不通過）→ DRSA（ゲート通過、使えなければ RANDOM）
(docs/decisions/adrs/0010-…)。COVERAGE / SIMILARITY は自動モードでは使わず、
`STRATEGY=coverage|similarity` で明示したときだけ使える。

戦略の選択は registry.resolve_strategy を通す。engine.py は具象クラスを import しない。
"""

from .base import Strategy, StrategyUnavailable
from .coverage import CoverageStrategy
from .drsa import DrsaStrategy
from .random import RandomStrategy
from .registry import resolve_strategy
from .similarity import SimilarityStrategy

__all__ = [
    "Strategy",
    "StrategyUnavailable",
    "CoverageStrategy",
    "RandomStrategy",
    "SimilarityStrategy",
    "DrsaStrategy",
    "resolve_strategy",
]
