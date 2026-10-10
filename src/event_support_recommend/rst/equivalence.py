"""等価クラスの集計（古典ラフ集合の識別不能関係）。

条件属性の値が完全に等しい観測を同じクラスにまとめ、件数・正例数・人数を数える。
純関数。入力の順序に依存しない（決定的）。
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class ClassStats:
    n: int  # 観測の件数
    positives: int  # 結果が成り立つ（★4）件数
    persons: int  # 異なる人数

    @property
    def rate(self) -> float:
        return self.positives / self.n if self.n else 0.0


def equivalence_classes(
    observations: Iterable[tuple[Hashable, str, bool]],
) -> dict[Hashable, ClassStats]:
    """(条件, 人, 結果) の列を条件ごとに集計する。"""
    n: dict[Hashable, int] = {}
    pos: dict[Hashable, int] = {}
    people: dict[Hashable, set[str]] = {}
    for cond, person, outcome in observations:
        n[cond] = n.get(cond, 0) + 1
        pos[cond] = pos.get(cond, 0) + (1 if outcome else 0)
        people.setdefault(cond, set()).add(person)
    return {c: ClassStats(n=n[c], positives=pos[c], persons=len(people[c])) for c in n}
