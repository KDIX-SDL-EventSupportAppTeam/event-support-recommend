"""スナップショットそのものを保持するキャッシュ (段3-b)。

`RuleCache` は規則しか持たない。しかし `SIMILARITY` は
**他の参加者の事前アンケート回答と評価**をリクエスト時に必要とする
(docs/specs/runtime-phase-switching/01-snapshot-source.md「SnapshotCache の新設」)。

- `RuleCache` と同じくスレッドセーフ（読みはリクエストスレッド、書きはバックグラウンド）
- **失敗時は前回の内容を保持する。空にしない**
- 個人を特定できる列を持たない。`user_id` は近傍の突き合わせにのみ使い外へ出さない
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class SnapshotData:
    built_at: datetime
    decision_table_size: int
    # user_id -> 事前アンケート回答（近傍の距離計算に使う平坦化済み dict）
    surveys: dict[str, dict] = field(default_factory=dict)
    # user_id -> {booth_id -> 正規化済み評価(0..1)}
    ratings_by_user: dict[str, dict[str, float]] = field(default_factory=dict)
    # booth_id -> category_id（COVERAGE 混合や近傍集計の補助）
    booth_category: dict[str, str | None] = field(default_factory=dict)
    # 全評価の平均（ベイズ縮約の縮約先）
    global_mean: float = 0.5


class SnapshotCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: SnapshotData | None = None

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._data is not None

    def get(self) -> SnapshotData | None:
        with self._lock:
            return self._data

    def put(
        self,
        *,
        decision_table_size: int,
        surveys: dict[str, dict],
        ratings_by_user: dict[str, dict[str, float]],
        booth_category: dict[str, str | None],
        global_mean: float,
        built_at: datetime | None = None,
    ) -> None:
        data = SnapshotData(
            built_at=built_at or datetime.now(timezone.utc),
            decision_table_size=decision_table_size,
            surveys=dict(surveys),
            ratings_by_user={u: dict(m) for u, m in ratings_by_user.items()},
            booth_category=dict(booth_category),
            global_mean=global_mean,
        )
        with self._lock:
            self._data = data

    def snapshot_state(self) -> dict:
        with self._lock:
            d = self._data
            return {
                "built_at": d.built_at.isoformat() if d else None,
                "decision_table_size": d.decision_table_size if d else None,
                "participants": len(d.surveys) if d else 0,
                "global_mean": d.global_mean if d else None,
            }
