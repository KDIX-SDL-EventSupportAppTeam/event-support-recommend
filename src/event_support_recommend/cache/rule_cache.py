"""規則キャッシュのホルダ (docs/specs/05-drsa.md §5)。

- 再生成はバックグラウンドタスク。リクエスト経路では生成しない
- 失敗時は前回の規則を保持し続ける（空にしない）
- 起動直後は規則なし = SIMILARITY へ退避、/ready が false

段3（ADR 0002 決着後）で `SnapshotRepository` からの決定表構築と定期再生成を結線する。
現状は「外部から put されるだけ」の受け皿。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from ..drsa import RuleSet


class RuleCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ruleset: RuleSet | None = None
        self._built_at: datetime | None = None
        self._decision_table_size: int | None = None
        self._gamma: float = float("nan")

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ruleset is not None and len(self._ruleset.rules) > 0

    def get(self) -> tuple[RuleSet | None, datetime | None]:
        with self._lock:
            return self._ruleset, self._built_at

    @property
    def gamma(self) -> float:
        with self._lock:
            return self._gamma

    @property
    def decision_table_size(self) -> int | None:
        with self._lock:
            return self._decision_table_size

    def put(
        self,
        ruleset: RuleSet,
        *,
        decision_table_size: int | None,
        gamma: float,
        built_at: datetime | None = None,
    ) -> None:
        with self._lock:
            self._ruleset = ruleset
            self._decision_table_size = decision_table_size
            self._gamma = gamma
            self._built_at = built_at or datetime.now(timezone.utc)

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
            }
