"""さくら読み取り専用プロキシの HTTP クライアント（段3 / ADR 0002）。

契約は書き込み用プロキシと同一（正本 `event-support-server/src/db/http-proxy.ts`）:

    POST {"sql": "...", "params": [...]}   ヘッダ X-Proxy-Key: <読み取り専用の鍵>
    → {"rows": [ {...}, ... ]}

**この経路の性質（ADR 0002「制約」）を前提にする:**

- 1リクエスト = 1SQL。トランザクションも行ロックも無い
- エラーはすべて HTTP 500 に潰れる。MySQL のエラーコードは取れない
- したがって例外に載せるのは「どのテーブルを試したか」だけ。**SQL 本文と鍵は載せない**

標準ライブラリのみ（`httpx` は dev 依存で本番に無い）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class ProxyError(Exception):
    """プロキシ経由の取得に失敗した事実だけを表す。

    メッセージにテーブル名以上の情報（SQL 本文・鍵・URL）を入れないこと
    (docs/specs/runtime-phase-switching/10-testing.md T-5)。
    """


class ReadonlyProxyClient:
    """読み取り専用プロキシへ `SELECT` を1本ずつ投げる。"""

    def __init__(self, url: str | None, key: str | None, *, timeout_sec: float = 20.0) -> None:
        self._url = (url or "").strip()
        self._key = (key or "").strip()
        self._timeout = max(1.0, float(timeout_sec))

    @property
    def configured(self) -> bool:
        """URL と鍵の両方がある場合のみ取得を試みる。空なら COVERAGE 固定で動く。"""
        return bool(self._url and self._key)

    def select(self, table: str, sql: str, params: list) -> list[dict]:
        """`SELECT` を1本送り、行のリストを返す。失敗は必ず `ProxyError`。

        `SELECT` で始まらない SQL は**送信前に**拒否する（T-6）。
        """
        if not sql.lstrip().upper().startswith("SELECT"):
            raise ProxyError(f"refused non-SELECT query for table {table!r}")

        body = json.dumps({"sql": sql, "params": list(params)}).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "X-Proxy-Key": self._key},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError):
            # URLError / HTTPError の str には URL が入るため連鎖させない（from None）。
            raise ProxyError(f"proxy request failed for table {table!r}") from None

        try:
            data = json.loads(raw)
        except ValueError:
            raise ProxyError(f"proxy returned non-JSON for table {table!r}") from None

        rows = data.get("rows") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ProxyError(f"proxy response missing rows for table {table!r}")
        return [r for r in rows if isinstance(r, dict)]
