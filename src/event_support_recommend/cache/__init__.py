"""cache/ — 規則キャッシュとスナップショットキャッシュ (docs/specs/05-drsa.md §5)。

規則生成・決定表の組み立てはバックグラウンドで一定間隔（既定5分）に行い、
リクエスト経路では絶対に生成しない。失敗時は前回の内容を保持し、空にしない。
"""

from .rule_cache import RuleCache
from .snapshot_cache import SnapshotCache, SnapshotData

__all__ = ["RuleCache", "SnapshotCache", "SnapshotData"]
