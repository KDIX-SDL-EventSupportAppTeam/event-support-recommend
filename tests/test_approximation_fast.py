"""近似の高速化 (#47) — 結果が変わらないこと・速いこと・構築中も止まらないこと。"""

from __future__ import annotations

import asyncio
import random
import threading
import time

import pytest

from event_support_recommend.cache import RuleCache, SnapshotCache
from event_support_recommend.data.refresher import SnapshotRefresher
from event_support_recommend.drsa import DecisionTable, approximate, generate_rules
from event_support_recommend.drsa.decision_table import DecisionRow
from event_support_recommend.models import DecisionClass, EventSnapshot
from _reference_approximation import approximate_reference

H, L = DecisionClass.HIGH, DecisionClass.LOW
LEVELS = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


def _table(rows: int, rng: random.Random, *, p_high: float = 0.5) -> DecisionTable:
    return DecisionTable(
        ("preference_match", "rating_affinity"),
        [
            DecisionRow((rng.randint(0, 3), rng.randint(1, 3)), H if rng.random() < p_high else L)
            for _ in range(rows)
        ],
    )


def _same(a, b) -> bool:
    return a == b  # frozen dataclass: 全フィールド（frozenset）を比較


@pytest.mark.parametrize("level", LEVELS)
def test_new_matches_reference_on_random_tables(level):
    rng = random.Random(47)
    for size in (0, 1, 2, 3, 10, 50, 300):
        for p_high in (0.0, 0.3, 0.5, 1.0):  # 0.0 / 1.0 は単一クラス
            t = _table(size, rng, p_high=p_high)
            assert _same(approximate(t, level), approximate_reference(t, level)), (size, p_high)


def test_new_matches_reference_three_attributes():
    rng = random.Random(3)
    t = DecisionTable(
        ("a", "b", "c"),
        [DecisionRow((rng.randint(0, 3), rng.randint(1, 3), rng.randint(0, 2)), rng.choice([H, L])) for _ in range(200)],
    )
    for level in LEVELS:
        assert _same(approximate(t, level), approximate_reference(t, level))


def test_two_thousand_rows_under_100ms():
    t = _table(2000, random.Random(1))
    t0 = time.perf_counter()
    approximate(t, 0.8)
    assert time.perf_counter() - t0 < 0.1


def test_generate_rules_stays_fast_at_two_thousand_rows():
    """generate_rules は行数に比例（値の組の数は一定）。結果を変えないので最適化せず、桁違いの悪化だけ見る。"""
    t = _table(2000, random.Random(2))
    t0 = time.perf_counter()
    generate_rules(t, min_support=5, consistency_level=0.8)
    assert time.perf_counter() - t0 < 1.0


def test_refresher_does_not_block_event_loop_during_build():
    started = threading.Event()

    def slow_build(_snap):
        started.set()
        time.sleep(0.6)  # 構築を人工的に遅くする

    class Repo:
        def fetch(self, _event_id):
            return EventSnapshot.unavailable()

    refresher = SnapshotRefresher(Repo(), interval_sec=60, on_snapshot=slow_build)

    async def scenario() -> float:
        task = asyncio.create_task(refresher.run_once())
        worst = 0.0
        last = time.perf_counter()
        while not task.done():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            worst = max(worst, now - last)
            last = now
        assert started.is_set()
        return worst

    worst_gap = asyncio.run(scenario())
    assert worst_gap < 0.3  # 同期呼び出しなら 0.6 秒以上止まる


def test_failed_build_keeps_previous_caches(monkeypatch):
    from event_support_recommend.settings import Settings
    from event_support_recommend import snapshot_build
    from event_support_recommend.snapshot_build import refresh_caches

    def boom(*_a, **_k):
        raise RuntimeError("build failed")

    monkeypatch.setattr(snapshot_build, "approximate", boom)

    rc, sc = RuleCache(), SnapshotCache()
    rc.put(generate_rules(_table(0, random.Random(0)), min_support=5, consistency_level=0.8),
           decision_table_size=7, gamma=0.5)

    class Snap:
        built = True
        tables = {"check_ins": []}

    with pytest.raises(RuntimeError):
        refresh_caches(Snap(), settings=Settings(_env_file=None), rule_cache=rc, snapshot_cache=sc)
    assert rc.decision_table_size == 7
    assert not sc.ready
