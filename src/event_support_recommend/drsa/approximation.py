r"""近似 — VC-DRSA（可変一貫性版）(docs/specs/05-drsa.md §2)。

厳密版は本件のデータ（属性2個・強い個人差）ではほぼ空集合になり規則が出ない。
一貫性水準 l を導入する。l = 1.0 で厳密版に一致する（テストで固定）。

2クラス（LOW < HIGH）なので Cl_HIGH^>= と Cl_LOW^<= の境界領域は一致し、
境界領域は1つだけになる:  Bn = U \ ( P_l(Cl_HIGH^>=) ∪ P_l(Cl_LOW^<=) )。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import DecisionClass
from .decision_table import DecisionTable
from .dominance import dominated_indices, dominating_indices


@dataclass(frozen=True)
class Approximation:
    consistency_level: float
    lower_upward: frozenset[int]  # 確実に「>= HIGH」と言える行
    upper_upward: frozenset[int]
    lower_downward: frozenset[int]  # 確実に「<= LOW」と言える行
    upper_downward: frozenset[int]
    boundary: frozenset[int]  # 2クラスなので上下共通

    @property
    def gamma(self) -> float:
        total = len(self.lower_upward | self.lower_downward | self.boundary)
        if total == 0:
            return 1.0
        certain = total - len(self.boundary)
        return certain / total


def approximate(table: DecisionTable, consistency_level: float) -> Approximation:
    """上下近似・境界領域を計算する。空表・単一行・単一クラスでも例外を投げない。"""
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
        # 「この行以上に良い行」のうち l 以上が HIGH なら確実側（上方）
        if len(d_plus & high) / len(d_plus) >= l:
            lower_up.add(i)
        # 「この行以下の行」のうち l 以上が LOW なら確実側（下方）
        if len(d_minus & low) / len(d_minus) >= l:
            lower_down.add(i)

    lower_upward = frozenset(lower_up)
    lower_downward = frozenset(lower_down)
    # 厳密版と整合する上方近似 = 「確実に LOW」でない行
    upper_upward = universe - lower_downward
    upper_downward = universe - lower_upward
    boundary = universe - (lower_upward | lower_downward)

    return Approximation(
        consistency_level=l,
        lower_upward=lower_upward,
        upper_upward=upper_upward,
        lower_downward=lower_downward,
        upper_downward=upper_downward,
        boundary=boundary,
    )


def gamma(table: DecisionTable, consistency_level: float) -> float:
    """近似の質 γ_P = |U − 境界領域| / |U|。DRSA がどれだけ機能しているかの直接指標。"""
    return approximate(table, consistency_level).gamma
