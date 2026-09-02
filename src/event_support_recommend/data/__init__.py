"""data/ — スナップショットの取得。ここだけが SQL / 外部 I/O を知る。

取得経路は ADR 0002（さくらプロキシの読み取り専用の口）で決着。
リクエスト経路からは呼ばない。5分ごとにバックグラウンドで取得する
(docs/specs/runtime-phase-switching/01-snapshot-source.md)。
"""

from .live_tables import EVENT_SCOPED_TABLES, LIVE_TABLES, build_select
from .proxy import ProxyError, ReadonlyProxyClient
from .refresher import SnapshotRefresher
from .repository import (
    ProxySnapshotRepository,
    SnapshotRepository,
    UnavailableRepository,
    build_repository,
)

__all__ = [
    "SnapshotRepository",
    "UnavailableRepository",
    "ProxySnapshotRepository",
    "build_repository",
    "ReadonlyProxyClient",
    "ProxyError",
    "SnapshotRefresher",
    "LIVE_TABLES",
    "EVENT_SCOPED_TABLES",
    "build_select",
]
