"""strategies/ — COVERAGE / RANDOM / (SIMILARITY / DRSA) / フォールバック。

FastAPI・SQL・HTTP を知らない。RecommendationContext だけを見る
(docs/specs/08-architecture.md §1, docs/specs/04-strategies.md §0 S-4)。

本番で使うのは COVERAGE。RANDOM は下限ベースライン（対照群）で本番既定にしない
(docs/decisions/adrs/0007-戦略の選択を環境変数で行う.md)。SIMILARITY / DRSA は
ADR 0002（データ入手経路）の決着待ちで未実装 (docs/specs/08-architecture.md §6 段3-4)。

戦略の選択は registry.resolve_strategy を通す。engine.py は具象クラスを import しない。
"""

from .base import Strategy, StrategyUnavailable
from .coverage import CoverageStrategy
from .random import RandomStrategy
from .registry import resolve_strategy
from .similarity import SimilarityStrategy

__all__ = [
    "Strategy",
    "StrategyUnavailable",
    "CoverageStrategy",
    "RandomStrategy",
    "SimilarityStrategy",
    "resolve_strategy",
]
