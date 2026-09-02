"""スナップショット取得のインターフェースと実装 (docs/specs/01-io-contract.md §2.2)。

取得経路が変わっても層3から見た姿を同一にする。決定は ADR 0002
（さくらプロキシの読み取り専用の口）。

- `UnavailableRepository` … 開発・テスト用。常に「取得不能」
- `ProxySnapshotRepository` … 本番。読み取り専用プロキシ経由

**この層だけが SQL と HTTP を知る。** リクエスト経路からは呼ばない
(docs/specs/runtime-phase-switching/01-snapshot-source.md「起きてはいけないこと」)。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from .. import logging as jsonl
from ..models import EventSnapshot
from .live_tables import EVENT_SCOPED_TABLES, LIVE_TABLES, build_select
from .proxy import ProxyError, ReadonlyProxyClient


@runtime_checkable
class SnapshotRepository(Protocol):
    def fetch(self, event_id: str | None = None) -> EventSnapshot:
        """イベントスナップショットを取得する。リクエスト経路の外で呼ぶこと。

        取得できなければ `EventSnapshot.unavailable()` を返す（例外を投げない）。
        「0件だった」と「測れなかった」を区別するため、後者は decision_table_size=None。
        """
        ...


class UnavailableRepository:
    """取得経路を持たない既定実装。常に「取得不能」を返す。

    これにより phase は COVERAGE、decision_table_size は null になり、
    SIMILARITY / DRSA へは昇格しない（仕様どおりの安全側の挙動）。
    """

    def fetch(self, event_id: str | None = None) -> EventSnapshot:
        return EventSnapshot.unavailable()


class ProxySnapshotRepository:
    """さくら読み取り専用プロキシ経由でイベントの生データを取得する。

    どんな失敗（未設定・500・タイムアウト・壊れた応答・未知テーブル）でも
    例外を投げず `EventSnapshot.unavailable()` を返す。失敗は JSONL(`kind=snapshot`)
    に記録するが、SQL 本文と鍵は載せない (ADR 0002「制約」, T-2..T-5)。
    """

    def __init__(
        self,
        client: ReadonlyProxyClient | None,
        *,
        default_event_id: str | None = None,
    ) -> None:
        self._client = client
        self._default_event_id = (default_event_id or "").strip() or None

    def fetch(self, event_id: str | None = None) -> EventSnapshot:
        eid = (event_id or "").strip() or self._default_event_id

        # URL / 鍵が未設定 → 取得を試みずに unavailable（T-1）。
        if self._client is None or not self._client.configured:
            return EventSnapshot.unavailable()
        # 対象イベントが決まらない → 全イベント横断を避けるため取得しない。
        if not eid:
            jsonl.emit("snapshot", {"ok": False, "error": "no event_id resolved"})
            return EventSnapshot.unavailable()

        tables: dict[str, list[dict]] = {}
        try:
            for name in LIVE_TABLES:
                scoped_eid = eid if name in EVENT_SCOPED_TABLES else None
                sql, params = build_select(name, scoped_eid)
                tables[name] = self._client.select(name, sql, params)
        except ProxyError as exc:
            # str(exc) はテーブル名までしか含まない（proxy.py / live_tables.py で保証）。
            jsonl.emit("snapshot", {"ok": False, "error": str(exc), "event_id": eid})
            return EventSnapshot.unavailable()

        participants = sum(
            1 for u in tables.get("users", []) if str(u.get("role")) == "participant"
        )
        return EventSnapshot(
            built=True,
            built_at=datetime.now(timezone.utc),
            decision_table_size=None,  # 決定表の組み立ては段3-b
            participants=participants,
            booths=len(tables.get("booths", [])),
            tables=tables,
        )


def build_repository(settings) -> SnapshotRepository:
    """設定から本番/開発のリポジトリを選ぶ。`data/` の外はこの関数だけ呼ぶ。"""
    url = getattr(settings, "readonly_proxy_url", "") or ""
    if not url.strip():
        return UnavailableRepository()
    client = ReadonlyProxyClient(
        url,
        getattr(settings, "readonly_proxy_key", "") or "",
        timeout_sec=getattr(settings, "readonly_proxy_timeout_sec", 20),
    )
    return ProxySnapshotRepository(
        client, default_event_id=getattr(settings, "snapshot_event_id", "") or ""
    )
