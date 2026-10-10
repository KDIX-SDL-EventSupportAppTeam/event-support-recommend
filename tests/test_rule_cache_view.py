"""RuleCache.read() — 更新途中の組み合わせを読まないこと (#47 のレビュー指摘)。

更新はバックグラウンドのスレッドで走るので、リクエスト経路は 1 回のロックで全項目を写す。
"""

from __future__ import annotations

import threading

from event_support_recommend.cache import RuleCache
from event_support_recommend.drsa import DecisionTable, generate_rules
from event_support_recommend.drsa.decision_table import DecisionRow
from event_support_recommend.models import DecisionClass


def _ruleset():
    rows = [DecisionRow((3, 3), DecisionClass.HIGH)] * 5 + [DecisionRow((0, 1), DecisionClass.LOW)] * 5
    table = DecisionTable(["preference_match", "rating_affinity"], rows)
    return generate_rules(table, consistency_level=0.6, min_support=1)


def test_read_returns_all_fields_from_one_put():
    rc = RuleCache()
    rs = _ruleset()
    rc.put(rs, decision_table_size=10, gamma=0.9, base_rate_high=0.5, base_rate_low=0.5, ser_rules=())
    v = rc.read()
    assert v.ruleset is rs and v.decision_table_size == 10 and v.gamma == 0.9
    assert (v.base_rate_high, v.base_rate_low) == (0.5, 0.5) and v.ser_rules == ()


def test_read_never_mixes_two_puts_under_concurrent_updates():
    """更新側が「件数 n・基準率 n/1000・γ n/1000」をそろえて書き続ける間に読み、組がずれないこと。"""
    rc = RuleCache()
    rs = _ruleset()
    stop = threading.Event()

    def writer():
        n = 0
        while not stop.is_set():
            n = n % 999 + 1
            rc.put(rs, decision_table_size=n, gamma=n / 1000, base_rate_high=n / 1000, base_rate_low=1 - n / 1000)

    t = threading.Thread(target=writer)
    t.start()
    try:
        rc.put(rs, decision_table_size=1, gamma=0.001, base_rate_high=0.001, base_rate_low=0.999)
        for _ in range(20_000):
            v = rc.read()
            n = v.decision_table_size
            assert v.gamma == n / 1000 and v.base_rate_high == n / 1000 and v.base_rate_low == 1 - n / 1000
    finally:
        stop.set()
        t.join()
