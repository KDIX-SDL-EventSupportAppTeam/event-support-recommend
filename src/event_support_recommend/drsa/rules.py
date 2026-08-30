"""規則の生成 — DOMLEM ではなく全列挙 (docs/specs/05-drsa.md §3)。

探索空間が極小（条件属性2〜3個・各3〜4値）なので全列挙で一瞬。かつ決定的で再現性と相性が良い。
目安として条件の候補が 10,000 を超えたら DOMLEM へ切り替えること。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import product

from ..models import DecisionClass
from .decision_table import DecisionTable

CONCLUSION_UP = ">=HIGH"
CONCLUSION_DOWN = "<=LOW"


@dataclass(frozen=True)
class Condition:
    attribute: str
    op: str  # ">=" (上方規則) または "<=" (下方規則)
    threshold: int

    def matches(self, value: int) -> bool:
        return value >= self.threshold if self.op == ">=" else value <= self.threshold

    def text(self) -> str:
        return f"{self.attribute}{self.op}{self.threshold}"


@dataclass(frozen=True)
class Rule:
    conclusion: str  # CONCLUSION_UP / CONCLUSION_DOWN
    conditions: tuple[Condition, ...]
    support: int
    confidence: float
    id: str
    coverage: frozenset[int] = field(default=frozenset(), repr=False, compare=False)

    def matches(self, vector: Mapping[str, int]) -> bool:
        for c in self.conditions:
            if c.attribute not in vector or not c.matches(vector[c.attribute]):
                return False
        return True

    def summary(self) -> dict:
        """reason.rules[] に載せる要約。規則本体は入れない (docs/specs/04-strategies.md §4)。"""
        return {
            "id": self.id,
            "class": self.conclusion,
            "support": self.support,
            "confidence": round(self.confidence, 4),
        }

    def as_text(self) -> str:
        conds = " and ".join(c.text() for c in self.conditions) or "true"
        return f"if {conds} then {self.conclusion}"


def _rule_id(conclusion: str, conditions: Sequence[Condition]) -> str:
    norm = ";".join(sorted(c.text() for c in conditions))
    digest = hashlib.sha1(f"{conclusion}|{norm}".encode()).hexdigest()
    return "R:" + digest[:8]


def _domains(table: DecisionTable) -> list[list[int]]:
    cols: list[set[int]] = [set() for _ in table.attribute_names]
    for row in table.rows:
        for k, v in enumerate(row.values):
            cols[k].add(v)
    return [sorted(c) for c in cols]


def _build_side(
    table: DecisionTable,
    *,
    op: str,
    conclusion: str,
    target_class: DecisionClass,
    min_support: int,
    consistency_level: float,
) -> list[Rule]:
    names = table.attribute_names
    domains = _domains(table)
    target = table.class_indices(target_class)

    # 各属性の条件候補: None（不問） + 各しきい値。
    per_attr_options: list[list[int | None]] = [
        [None, *vals] for vals in domains
    ]

    seen: dict[str, Rule] = {}
    for combo in product(*per_attr_options):
        conditions = tuple(
            Condition(names[k], op, thr)
            for k, thr in enumerate(combo)
            if thr is not None
        )
        if not conditions:
            continue  # 条件ゼロは規則にしない
        matched = frozenset(
            i
            for i, row in enumerate(table.rows)
            if all(c.matches(row.values[names.index(c.attribute)]) for c in conditions)
        )
        support = len(matched)
        if support < min_support:
            continue
        confidence = len(matched & target) / support
        if confidence < consistency_level:
            continue
        rid = _rule_id(conclusion, conditions)
        rule = Rule(
            conclusion=conclusion,
            conditions=tuple(sorted(conditions, key=lambda c: c.attribute)),
            support=support,
            confidence=confidence,
            id=rid,
            coverage=matched,
        )
        # 同一 id（同一内容）は1つに。全列挙なので重複はしきい値の等価表現のみ。
        seen.setdefault(rid, rule)
    return list(seen.values())


def _prune_redundant(rules: list[Rule]) -> list[Rule]:
    """条件が緩いのに confidence が同等以上の規則があれば、厳しいほうを捨てる
    (docs/specs/05-drsa.md §3.1)。結果は極小規則の集合になる。"""
    kept: list[Rule] = []
    for b in rules:
        redundant = False
        for a in rules:
            if a is b or a.conclusion != b.conclusion:
                continue
            if b.coverage <= a.coverage and a.confidence + 1e-12 >= b.confidence:
                if a.coverage == b.coverage:
                    # 同じ被覆なら、条件が少ない/ id が小さいほうを残す（決定的）
                    if (len(a.conditions), a.id) < (len(b.conditions), b.id):
                        redundant = True
                        break
                else:
                    redundant = True
                    break
        if not redundant:
            kept.append(b)
    return kept


@dataclass(frozen=True)
class RuleSet:
    rules: tuple[Rule, ...]
    consistency_level: float
    min_support: int

    @property
    def certain_up(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.conclusion == CONCLUSION_UP)

    @property
    def certain_down(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.conclusion == CONCLUSION_DOWN)

    def apply(self, vector: Mapping[str, int]) -> tuple[float, float, list[Rule]]:
        """候補の条件属性ベクトルに規則を当てる。

        戻り値 (up, down, matched):
          up   = 適合した「>= HIGH」規則の confidence の最大値（無ければ 0）
          down = 適合した「<= LOW」規則の confidence の最大値（無ければ 0）
        スコアへの集約は docs/specs/04-strategies.md §4。
        """
        matched = [r for r in self.rules if r.matches(vector)]
        up = max((r.confidence for r in matched if r.conclusion == CONCLUSION_UP), default=0.0)
        down = max((r.confidence for r in matched if r.conclusion == CONCLUSION_DOWN), default=0.0)
        return up, down, matched

    def candidate_coverage(self, vectors: Sequence[Mapping[str, int]]) -> float:
        """直近リクエストで、適合規則が1本以上あった候補の割合 (docs/specs/03-phases.md §3.3-4)。"""
        if not vectors:
            return 0.0
        hit = sum(1 for v in vectors if any(r.matches(v) for r in self.rules))
        return hit / len(vectors)


def generate_rules(
    table: DecisionTable,
    *,
    min_support: int,
    consistency_level: float,
) -> RuleSet:
    """決定表から確実規則の集合を生成する。入力順に依存しない（決定的）。

    空表・単一行・単一クラスでも例外を投げず、規則0本の RuleSet を返す。
    """
    up_rules = _build_side(
        table,
        op=">=",
        conclusion=CONCLUSION_UP,
        target_class=DecisionClass.HIGH,
        min_support=min_support,
        consistency_level=consistency_level,
    )
    down_rules = _build_side(
        table,
        op="<=",
        conclusion=CONCLUSION_DOWN,
        target_class=DecisionClass.LOW,
        min_support=min_support,
        consistency_level=consistency_level,
    )
    pruned = _prune_redundant(up_rules) + _prune_redundant(down_rules)
    ordered = tuple(
        sorted(
            pruned,
            key=lambda r: (r.conclusion, tuple(c.text() for c in r.conditions), r.id),
        )
    )
    return RuleSet(rules=ordered, consistency_level=consistency_level, min_support=min_support)
