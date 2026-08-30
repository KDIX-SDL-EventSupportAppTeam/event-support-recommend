"""drsa/ — 優越関係・近似・規則生成。純粋（DB も HTTP も features/ も知らない）。

決定表を受け取り規則を返す。それだけ (docs/specs/05-drsa.md)。
DRSA は「説明可能な選択フィルタ」であって精度最大化の予測器ではない。
設計の優先順位は 1.規則が人間に読めること 2.少データで壊れないこと 3.精度。
"""

from .approximation import Approximation, approximate, gamma
from .decision_table import DecisionRow, DecisionTable
from .dominance import dominated_indices, dominating_indices
from .rules import Rule, RuleSet, generate_rules

__all__ = [
    "Approximation",
    "approximate",
    "gamma",
    "DecisionRow",
    "DecisionTable",
    "dominated_indices",
    "dominating_indices",
    "Rule",
    "RuleSet",
    "generate_rules",
]
