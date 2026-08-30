"""優越関係 (docs/specs/05-drsa.md §1)。

すべての条件属性は利得型。x が y を P-優越する ⇔ すべての q について x_q >= y_q。
"""

from __future__ import annotations

from .decision_table import DecisionTable


def dominates(x: tuple[int, ...], y: tuple[int, ...]) -> bool:
    """x が y を P-優越するか（全属性で x_q >= y_q）。"""
    return all(xi >= yi for xi, yi in zip(x, y, strict=True))


def dominating_indices(table: DecisionTable, i: int) -> frozenset[int]:
    """D_P^+(x_i) = { y : y が x_i を優越する } = x_i 以上に良い行の集合。"""
    xi = table.rows[i].values
    return frozenset(j for j in table.indices if dominates(table.rows[j].values, xi))


def dominated_indices(table: DecisionTable, i: int) -> frozenset[int]:
    """D_P^-(x_i) = { y : x_i が y を優越する } = x_i 以下の行の集合。"""
    xi = table.rows[i].values
    return frozenset(j for j in table.indices if dominates(xi, table.rows[j].values))
