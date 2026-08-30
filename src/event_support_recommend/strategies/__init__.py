"""strategies/ — COVERAGE / SIMILARITY / DRSA / フォールバック。

FastAPI・SQL・HTTP を知らない。RecommendationContext だけを見る
(docs/specs/08-architecture.md §1, docs/specs/04-strategies.md §0 S-4)。

現在結線されているのは COVERAGE のみ。SIMILARITY / DRSA は ADR 0002（データ入手経路）
の決着待ちで未実装 (docs/specs/08-architecture.md §6 段3-4)。
"""

from .base import Strategy
from .coverage import CoverageStrategy

__all__ = ["Strategy", "CoverageStrategy"]
