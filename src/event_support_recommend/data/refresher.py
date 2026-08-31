"""スナップショットの定期再取得（バックグラウンドタスク）。

FastAPI の lifespan から1本だけ起動する。
- 起動直後に1回走らせる（**起動を待たせない**、01-snapshot-source.md）
- 以後 `interval_sec`（`SNAPSHOT_TTL_SEC`、既定300）ごとに1周
- **失敗しても前回のキャッシュを保持したまま次の周期を待つ。空にしない**
- 1周ごとに JSONL に `kind=snapshot` を記録する（SQL 本文と鍵は載せない）

決定表の組み立て・キャッシュ更新は `on_snapshot` コールバックに委ねる（段3-b で結線）。
このモジュール自体は `drsa/` も `features/` も知らない。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from .. import logging as jsonl
from ..models import EventSnapshot
from .repository import SnapshotRepository


class SnapshotRefresher:
    def __init__(
        self,
        repo: SnapshotRepository,
        *,
        interval_sec: float,
        on_snapshot: Callable[[EventSnapshot], None] | None = None,
        event_id_getter: Callable[[], str | None] | None = None,
    ) -> None:
        self._repo = repo
        self._interval = max(1.0, float(interval_sec))
        self._on_snapshot = on_snapshot or (lambda _s: None)
        self._event_id_getter = event_id_getter or (lambda: None)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def run_once(self) -> EventSnapshot:
        """1周だけ実行する。例外は外へ漏らさない。"""
        started = time.monotonic()
        try:
            event_id = self._event_id_getter()
        except Exception:  # pragma: no cover - 防御
            event_id = None
        try:
            snapshot = await asyncio.to_thread(self._repo.fetch, event_id)
        except Exception as exc:  # pragma: no cover - repo は投げない約束だが防御
            jsonl.emit("snapshot", {"ok": False, "error": f"fetch_raised: {exc!r}"})
            return EventSnapshot.unavailable()

        try:
            self._on_snapshot(snapshot)
        except Exception as exc:  # pragma: no cover - 防御。前回キャッシュは保持される
            jsonl.emit("snapshot", {"ok": False, "error": f"build_raised: {exc!r}"})
            return snapshot

        jsonl.emit(
            "snapshot",
            {
                "ok": snapshot.built,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                "decision_table_size": snapshot.decision_table_size,
                "participants": snapshot.participants,
                "booths": snapshot.booths,
            },
        )
        return snapshot

    async def _loop(self) -> None:
        while not self._stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        """バックグラウンドループを開始する。すでに動いていれば何もしない。"""
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
