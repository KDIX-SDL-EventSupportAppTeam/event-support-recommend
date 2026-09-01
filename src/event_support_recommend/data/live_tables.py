"""読み取り専用プロキシから取得するテーブルと列の定義（段3 / ADR 0002）。

**ここだけが「どのテーブルのどの列を読むか」を知る。** `SELECT *` を書かず、
この定義から SQL を組み立てる（`event-support-analytics/src/rec_db.py` の LIVE_TABLES と同じ流儀）。

イベントの絞り込み規則も `data/` のこの1箇所にだけ書く（`strategies/` や `features/` には書かない、
docs/specs/runtime-phase-switching/01-snapshot-source.md）。
"""

from __future__ import annotations

from .proxy import ProxyError

# テーブル名 → 取得する列。ここに無い列は構造的に要求できない。
# users は id / role だけ。email / password_hash は権限側でも読めないが、
# クライアント側でも定義に入れない（二重の壁、01-snapshot-source.md）。
LIVE_TABLES: dict[str, tuple[str, ...]] = {
    "check_ins": ("id", "user_id", "booth_id", "event_id", "visit_order", "checked_in_at"),
    "booth_ratings": ("checkin_id", "user_id", "booth_id", "event_id", "rating", "scale"),
    "user_survey_answers": (
        "user_id",
        "event_id",
        "age_range",
        "occupation",
        "industry",
        "custom_answers",
    ),
    "booths": ("id", "event_id", "category_id", "is_active"),
    "categories": ("id", "event_id", "name"),
    "users": ("id", "role"),
}

# event_id で直接絞るテーブル。`users` はここに入れない
# （users.event_id で絞ると出展者・運営アカウントが混ざる、01-snapshot-source.md）。
EVENT_SCOPED_TABLES: frozenset[str] = frozenset(
    {"check_ins", "booth_ratings", "user_survey_answers", "booths", "categories"}
)

# 決して要求してはならない列（防御。定義に入れていないので通常は到達しない）。
FORBIDDEN_COLUMNS: frozenset[str] = frozenset({"email", "password_hash"})


def build_select(table: str, event_id: str | None) -> tuple[str, list]:
    """テーブル名と列の定義から `SELECT` 文とパラメータを組み立てる。

    - `LIVE_TABLES` に無いテーブルは拒否する（送信前）
    - `FORBIDDEN_COLUMNS` が定義に混入していたら拒否する（送信前）
    - `EVENT_SCOPED_TABLES` かつ `event_id` が与えられていれば `WHERE event_id = ?`
      （プレースホルダは `?`。プロキシは mysql2 互換 = event-support-server/src/db/http-proxy.ts） を付ける
    """
    if table not in LIVE_TABLES:
        raise ProxyError(f"unknown table {table!r}")

    columns = LIVE_TABLES[table]
    if FORBIDDEN_COLUMNS & set(columns):
        raise ProxyError(f"forbidden column requested for table {table!r}")

    col_sql = ", ".join(f"`{c}`" for c in columns)
    sql = f"SELECT {col_sql} FROM `{table}`"
    params: list = []
    if table in EVENT_SCOPED_TABLES and event_id:
        sql += " WHERE `event_id` = ?"
        params.append(event_id)
    return sql, params
