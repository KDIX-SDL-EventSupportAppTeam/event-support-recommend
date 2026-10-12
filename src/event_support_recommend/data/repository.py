"""スナップショット取得のインターフェースと実装 (docs/specs/01-io-contract.md §2.2)。

取得経路が変わっても層3から見た姿を同一にする。決定は ADR 0002
（さくらプロキシの読み取り専用の口）。

- `UnavailableRepository` … 開発・テスト用。常に「取得不能」
- `ProxySnapshotRepository` … 本番。読み取り専用プロキシ経由

**この層だけが SQL と HTTP を知る。** リクエスト経路からは呼ばない
(docs/specs/runtime-phase-switching/01-snapshot-source.md「起きてはいけないこと」)。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from .. import logging as jsonl
from ..models import EventSnapshot
from .live_tables import (
    EVENT_SCOPED_TABLES,
    LIVE_TABLES,
    OPTIONAL_TABLES,
    PARENT_SCOPED_TABLES,
    build_select,
)
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
        max_parallel: int = 4,
    ) -> None:
        self._client = client
        self._default_event_id = (default_event_id or "").strip() or None
        self._max_parallel = max(1, int(max_parallel))

    def fetch(self, event_id: str | None = None) -> EventSnapshot:
        eid = (event_id or "").strip() or self._default_event_id

        # URL / 鍵が未設定 → 取得を試みずに unavailable（T-1）。
        if self._client is None or not self._client.configured:
            return EventSnapshot.unavailable()
        # 対象イベントが決まらない → 全イベント横断を避けるため取得しない。
        if not eid:
            jsonl.emit("snapshot", {"ok": False, "error": "no event_id resolved"})
            return EventSnapshot.unavailable()

        def select(name: str, *, scope_via_parent: bool = True) -> list[dict]:
            scoped_eid = eid if name in EVENT_SCOPED_TABLES or name in PARENT_SCOPED_TABLES else None
            sql, params = build_select(name, scoped_eid, scope_via_parent=scope_via_parent)
            return self._client.select(name, sql, params)

        def select_optional(name: str) -> list[dict] | ProxyError:
            try:
                return select(name)
            except ProxyError as exc:
                if name not in PARENT_SCOPED_TABLES:
                    return exc
            # サブクエリを知らない古いプロキシは 403 を返す。全件取得に戻して信号は失わない
            try:
                return select(name, scope_via_parent=False)
            except ProxyError as exc:
                return exc

        # 1本ずつ直列だと上流（さくら）の往復が積み上がる（2026-10-12 実測 約14秒/8本）。
        # 並列にするが、共有サーバーのさくらを叩きすぎないよう同時数は絞る。
        with ThreadPoolExecutor(max_workers=self._max_parallel) as pool:
            live_futs = {name: pool.submit(select, name) for name in LIVE_TABLES}
            opt_futs = {name: pool.submit(select_optional, name) for name in OPTIONAL_TABLES}
            tables: dict[str, list[dict]] = {}
            try:
                for name, fut in live_futs.items():
                    tables[name] = fut.result()
            except ProxyError as exc:
                # str(exc) はテーブル名までしか含まない（proxy.py / live_tables.py で保証）。
                for f in [*live_futs.values(), *opt_futs.values()]:
                    f.cancel()
                jsonl.emit("snapshot", {"ok": False, "error": str(exc), "event_id": eid})
                return EventSnapshot.unavailable()

            # 任意テーブル: 失敗してもスナップショットは成立させる（自発訪問の +1 が数えられなくなるだけ）。
            for name, fut in opt_futs.items():
                result = fut.result()
                if isinstance(result, ProxyError):
                    jsonl.emit(
                        "snapshot", {"ok": True, "optional_table_failed": str(result), "event_id": eid}
                    )
                else:
                    tables[name] = result

        participants = _count_event_participants(tables)
        return EventSnapshot(
            built=True,
            built_at=datetime.now(timezone.utc),
            decision_table_size=None,  # 決定表の組み立ては段3-b
            participants=participants,
            booths=len(tables.get("booths", [])),
            tables=tables,
        )


def _count_event_participants(tables: dict[str, list[dict]]) -> int:
    """このイベントの参加者数（ロールは問わない。出展者・運営も数える）。

    `users` は出展者・運営が混ざらないよう event_id で絞らずに取る（live_tables.py）ため、
    users 全件を数えると DB 内の全イベントの人数になる（2026-10-12 本番で 3 人のイベントが 9 と出た）。
    イベントで絞った表（アンケート回答・チェックイン）に現れる人に限って数える。
    """
    user_ids = {str(u.get("id")) for u in tables.get("users", [])}
    in_event = {
        str(r.get("user_id"))
        for name in ("user_survey_answers", "check_ins")
        for r in tables.get(name, [])
    }
    return len(user_ids & in_event)


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
