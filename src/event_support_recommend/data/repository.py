"""スナップショット取得のインターフェース (docs/specs/01-io-contract.md §2.2)。

取得経路が DB 直読みでもサーバー追加 API でも、層3から見た姿を同一にする。
決定は ADR 0002。決まるまで実体は `UnavailableRepository` のみ。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import EventSnapshot


@runtime_checkable
class SnapshotRepository(Protocol):
    def fetch(self, event_id: str | None = None) -> EventSnapshot:
        """イベントスナップショットを取得する。リクエスト経路の外で呼ぶこと。

        取得できなければ `EventSnapshot.unavailable()` を返す（例外を投げない）。
        「0件だった」と「測れなかった」を区別するため、後者は decision_table_size=None。
        """
        ...


class UnavailableRepository:
    """ADR 0002 未決のあいだの既定実装。常に「取得不能」を返す。

    これにより phase は COVERAGE、decision_table_size は null になり、
    SIMILARITY / DRSA へは昇格しない（仕様どおりの安全側の挙動）。
    """

    def fetch(self, event_id: str | None = None) -> EventSnapshot:
        return EventSnapshot.unavailable()
