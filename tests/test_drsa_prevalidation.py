"""事前検証 §8.1 の縮小版 — 埋め込んだ規則を DRSA が復元するか
(docs/specs/07-testing.md §8.1)。

本番の「地図づくり」(件数 x ノイズ率) は event-support-analytics 側で行う。
ここでは「システムとして目的を果たす」ことの最小確認に留める。
"""

from __future__ import annotations

import random

import pytest

from event_support_recommend.drsa import DecisionTable, generate_rules
from event_support_recommend.drsa.approximation import gamma
from event_support_recommend.drsa.decision_table import DecisionRow
from event_support_recommend.models import DecisionClass

NAMES = ("preference_match", "rating_affinity")


def _synth(n: int, noise: float, seed: int) -> DecisionTable:
    """正解の規則を埋め込む: pm>=3 -> HIGH, pm<=1 -> LOW（各 (1-noise) の確率）。"""
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        pm = rng.randint(0, 3)
        ra = rng.randint(1, 3)
        if pm >= 3:
            base = DecisionClass.HIGH
        elif pm <= 1:
            base = DecisionClass.LOW
        else:
            base = rng.choice([DecisionClass.HIGH, DecisionClass.LOW])
        if rng.random() < noise:
            base = DecisionClass.LOW if base == DecisionClass.HIGH else DecisionClass.HIGH
        rows.append(DecisionRow((pm, ra), base))
    return DecisionTable(NAMES, rows)


@pytest.mark.parametrize("noise", [0.05, 0.20])
@pytest.mark.parametrize("n", [120, 180])
def test_embedded_rules_are_recovered(n, noise):
    table = _synth(n, noise, seed=hash((n, noise)) & 0xFFFF)
    rs = generate_rules(table, min_support=5, consistency_level=0.7)
    texts = {r.as_text() for r in rs.rules}
    up_has_pm3 = any(
        r.conclusion == ">=HIGH"
        and any(c.attribute == "preference_match" and c.threshold >= 3 for c in r.conditions)
        for r in rs.rules
    )
    down_has_pm1 = any(
        r.conclusion == "<=LOW"
        and any(c.attribute == "preference_match" and c.threshold <= 1 for c in r.conditions)
        for r in rs.rules
    )
    assert up_has_pm3, texts
    assert down_has_pm1, texts
    assert gamma(table, 0.7) > 0.3  # 優越原理との整合がある程度保たれている
