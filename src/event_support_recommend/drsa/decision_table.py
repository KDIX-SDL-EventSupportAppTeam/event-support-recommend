"""決定表の型 (docs/specs/05-drsa.md §1)。

行 = (参加者, ブース) ペア。条件属性はすべて利得型の順序尺度。決定属性は LOW/HIGH の2クラス。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..models import DecisionClass


@dataclass(frozen=True)
class DecisionRow:
    values: tuple[int, ...]  # attribute_names の順に並んだ条件属性値
    decision: DecisionClass


class DecisionTable:
    """条件属性名の並びと行の集合。行は順序を持って保持する（決定性のため）。"""

    def __init__(self, attribute_names: Sequence[str], rows: Iterable[DecisionRow]):
        self.attribute_names: tuple[str, ...] = tuple(attribute_names)
        self.rows: tuple[DecisionRow, ...] = tuple(rows)
        n = len(self.attribute_names)
        for r in self.rows:
            if len(r.values) != n:
                raise ValueError(
                    f"行の属性数 {len(r.values)} が属性名の数 {n} と一致しない"
                )

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def indices(self) -> range:
        return range(len(self.rows))

    def class_indices(self, cls: DecisionClass) -> frozenset[int]:
        return frozenset(i for i, r in enumerate(self.rows) if r.decision == cls)

    @classmethod
    def from_records(
        cls, attribute_names: Sequence[str], records: Iterable[dict]
    ) -> "DecisionTable":
        """dict の列（{attr: value, ..., "decision": "HIGH"/"LOW"}）から構築する。

        欠損属性を持つ行は落とす（測定不能な行は決定表に載らない、
        docs/specs/02-features.md §1.2）。
        """
        names = tuple(attribute_names)
        rows: list[DecisionRow] = []
        for rec in records:
            decision = rec.get("decision")
            if isinstance(decision, DecisionClass):
                dc = decision
            elif isinstance(decision, str) and decision.upper() in ("HIGH", "LOW"):
                dc = DecisionClass(decision.upper())
            else:
                continue
            try:
                values = tuple(int(rec[name]) for name in names)
            except (KeyError, TypeError, ValueError):
                continue
            rows.append(DecisionRow(values=values, decision=dc))
        return cls(names, rows)
