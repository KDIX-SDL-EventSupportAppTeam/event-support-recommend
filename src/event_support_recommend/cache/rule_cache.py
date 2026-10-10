"""規則キャッシュのホルダ (docs/specs/05-drsa.md §5)。

- 再生成はバックグラウンドタスク。リクエスト経路では生成しない
- 失敗時は前回の規則を保持し続ける（空にしない）
- 起動直後は規則なし = SIMILARITY へ退避、/ready が false

段3（ADR 0002 決着後）で `SnapshotRepository` からの決定表構築と定期再生成を結線する。
現状は「外部から put されるだけ」の受け皿。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from ..drsa import RuleSet
from ..rst import SerRule


@dataclass(frozen=True)
class RuleCacheView:
    """ある時点のキャッシュの中身を丸ごと写したもの。1 回のロックで取る。

    更新はバックグラウンドのスレッドで走る (#47)。項目ごとに別々に読むと、
    「新しい規則」と「古い基準率」のような更新途中の組み合わせを読みうるので、
    リクエスト経路は必ず `RuleCache.read()` で一度に読む。
    """

    ruleset: RuleSet | None
    built_at: datetime | None
    decision_table_size: int | None
    gamma: float
    base_rate_high: float | None
    base_rate_low: float | None
    ser_rules: tuple[SerRule, ...]


class RuleCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ruleset: RuleSet | None = None
        self._built_at: datetime | None = None
        self._decision_table_size: int | None = None
        self._gamma: float = float("nan")
        # 決定表全体の基準率。品質ゲートのリフト条件の比較基準
        # (docs/specs/03-phases.md §3.3)。決定表が空なら None。
        self._base_rate_high: float | None = None
        self._base_rate_low: float | None = None
        # セレンディピティ規則 (rst/)。DRSA の規則とは独立に持つ。
        self._ser_rules: tuple[SerRule, ...] = ()
        self._ser_rows: int = 0  # 規則の材料にできた関心外の評価の件数
        self._ser_built_at: datetime | None = None

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ruleset is not None and len(self._ruleset.rules) > 0

    def get(self) -> tuple[RuleSet | None, datetime | None]:
        with self._lock:
            return self._ruleset, self._built_at

    def read(self) -> RuleCacheView:
        """全項目を 1 回のロックで読む。`put` の途中の状態は見えない。"""
        with self._lock:
            return RuleCacheView(
                ruleset=self._ruleset,
                built_at=self._built_at,
                decision_table_size=self._decision_table_size,
                gamma=self._gamma,
                base_rate_high=self._base_rate_high,
                base_rate_low=self._base_rate_low,
                ser_rules=self._ser_rules,
            )

    @property
    def gamma(self) -> float:
        with self._lock:
            return self._gamma

    @property
    def decision_table_size(self) -> int | None:
        with self._lock:
            return self._decision_table_size

    @property
    def base_rates(self) -> tuple[float | None, float | None]:
        """(高評価の基準率, 低評価の基準率)。上方規則は前者、下方規則は後者と比べる。"""
        with self._lock:
            return self._base_rate_high, self._base_rate_low

    def put(
        self,
        ruleset: RuleSet,
        *,
        decision_table_size: int | None,
        gamma: float,
        built_at: datetime | None = None,
        base_rate_high: float | None = None,
        base_rate_low: float | None = None,
        ser_rules: tuple[SerRule, ...] | None = None,
        ser_mismatch_rows: int = 0,
    ) -> None:
        """DRSA 規則を差し替える。`ser_rules` を渡したときは同じロックの中で一緒に差し替える
        （更新の途中の状態を /recommend に見せない）。None なら前回のセレンディピティ規則を保つ。"""
        with self._lock:
            if ser_rules is not None:
                self._ser_rules = tuple(ser_rules)
                self._ser_rows = ser_mismatch_rows
                self._ser_built_at = built_at or datetime.now(timezone.utc)
            self._base_rate_high = base_rate_high
            self._base_rate_low = base_rate_low
            self._ruleset = ruleset
            self._decision_table_size = decision_table_size
            self._gamma = gamma
            self._built_at = built_at or datetime.now(timezone.utc)

    @property
    def ser_rules(self) -> tuple[SerRule, ...]:
        with self._lock:
            return self._ser_rules

    def put_ser(
        self, rules: tuple[SerRule, ...], *, mismatch_rows: int, built_at: datetime | None = None
    ) -> None:
        with self._lock:
            self._ser_rules = tuple(rules)
            self._ser_rows = mismatch_rows
            self._ser_built_at = built_at or datetime.now(timezone.utc)

    def ser_state(self) -> dict:
        with self._lock:
            return {
                "count": len(self._ser_rules),
                "mismatch_rows": self._ser_rows,
                "built_at": self._ser_built_at.isoformat() if self._ser_built_at else None,
                "rules": [r.summary() for r in self._ser_rules],
            }

    def snapshot_state(self) -> dict:
        with self._lock:
            rs = self._ruleset
            return {
                "built_at": self._built_at.isoformat() if self._built_at else None,
                "count_certain_up": len(rs.certain_up) if rs else 0,
                "count_certain_down": len(rs.certain_down) if rs else 0,
                "gamma": self._gamma,
                "consistency_level": rs.consistency_level if rs else None,
                "decision_table_size": self._decision_table_size,
                "base_rate_high": self._base_rate_high,
                "base_rate_low": self._base_rate_low,
            }
