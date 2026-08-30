"""自前ログ — 推薦1回につき JSONL を1行、標準出力へ (docs/specs/01-io-contract.md §5)。

保存先は Cloud Logging。DB には書かない。サーバー側の永続化が失敗してもこちらに記録が残る。
イベントは年1回・1日だけ。迷ったら出す。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


def _default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.astimezone(timezone.utc).isoformat()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    return str(o)


def emit(kind: str, payload: dict[str, Any], *, stream=None) -> None:
    """1行 JSONL を書き出す。kind は recommend / rules_built / snapshot_built など
    (docs/specs/10-observability.md §3)。"""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        **payload,
    }
    line = json.dumps(record, ensure_ascii=False, default=_default, separators=(",", ":"))
    print(line, file=stream or sys.stdout, flush=True)
