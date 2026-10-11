"""参照実装 — 近似の素朴な O(n²) 版（#47 の最適化前の実装をそのまま残したもの）。

本番の `drsa.approximation.approximate` が、これと完全に同じ結果を返すことをテストで固定する。
本番コードからは import しない。
"""

from __future__ import annotations

from event_support_recommend.drsa.approximation import Approximation
from event_support_recommend.drsa.decision_table import DecisionTable
from event_support_recommend.drsa.dominance import dominated_indices, dominating_indices
from event_support_recommend.models import DecisionClass


def approximate_reference(table: DecisionTable, consistency_level: float) -> Approximation:
    n = len(table)
    universe = frozenset(range(n))
    if n == 0:
        empty: frozenset[int] = frozenset()
        return Approximation(consistency_level, empty, empty, empty, empty, empty)

    high = table.class_indices(DecisionClass.HIGH)
    low = table.class_indices(DecisionClass.LOW)
    l = consistency_level

    lower_up: set[int] = set()
    lower_down: set[int] = set()
    for i in table.indices:
        d_plus = dominating_indices(table, i)
        d_minus = dominated_indices(table, i)
        if len(d_plus & high) / len(d_plus) >= l:
            lower_up.add(i)
        if len(d_minus & low) / len(d_minus) >= l:
            lower_down.add(i)

    lower_upward = frozenset(lower_up)
    lower_downward = frozenset(lower_down)
    return Approximation(
        consistency_level=l,
        lower_upward=lower_upward,
        upper_upward=universe - lower_downward,
        lower_downward=lower_downward,
        upper_downward=universe - lower_upward,
        boundary=universe - (lower_upward | lower_downward),
    )
