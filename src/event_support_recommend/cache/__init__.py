"""cache/ — 規則キャッシュ (docs/specs/05-drsa.md §5)。

規則生成はバックグラウンドで一定間隔（既定5分）に行い、リクエスト経路では絶対に生成しない。
現状はスナップショット取得（ADR 0002）が未結線のため、キャッシュは常に空 = SIMILARITY 以下へ退避。
"""

from .rule_cache import RuleCache

__all__ = ["RuleCache"]
