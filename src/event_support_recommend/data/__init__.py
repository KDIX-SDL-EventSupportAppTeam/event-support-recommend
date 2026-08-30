"""data/ — スナップショットの取得。ここだけが SQL / 外部 I/O を知る。

取得経路（DB 直読み / サーバーの追加 API）は ADR 0002 が未決定のため未実装。
層3から見た姿を固定する 1 インターフェースだけ先に置く
(docs/specs/01-io-contract.md §2.2, docs/specs/08-architecture.md §6 段3)。
"""

from .repository import SnapshotRepository, UnavailableRepository

__all__ = ["SnapshotRepository", "UnavailableRepository"]
