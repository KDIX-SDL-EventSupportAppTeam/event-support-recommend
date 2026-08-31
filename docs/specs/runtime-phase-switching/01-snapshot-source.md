---
状態: 確定
最終更新: 2026-09-01
---

# スナップショットの取得（段3）

**このファイルが扱うのは `data/` 層だけである。** ここだけが SQL と HTTP を知る。
上の層（`strategies/` `features/` `drsa/`）は取得経路を知らない。

## 経路

[ADR 0002](../../decisions/adrs/0002-決定表のデータ入手経路.md) で決着した
**さくらプロキシの読み取り専用の口**を使う。

```
[Cloud Run: event-support-recommend]
        │ POST {"sql": "...", "params": [...]}   X-Proxy-Key: <読み取り専用の鍵>
        ▼
[さくら: /bingo/query-ro/index.php]  ← SELECT しか通さない
        │
        ▼
[MySQL]  ← bingo_ro ユーザー（SELECT のみ・users は id/role だけ）
```

契約（リクエスト・レスポンスの形）は既存の書き込み用プロキシと同一。
正本は `event-support-server/src/db/http-proxy.ts`。

## 設定値

| 環境変数 | 既定 | 意味 |
|---|---|---|
| `READONLY_PROXY_URL` | 空 | 読み取り専用の口の URL。**空なら取得しない**（= `COVERAGE` 固定で動く） |
| `READONLY_PROXY_KEY` | 空 | その口専用の鍵。**書き込み可能な鍵を入れてはならない** |
| `READONLY_PROXY_TIMEOUT_SEC` | 20 | 1テーブルあたり。更新間隔（300秒）を食い潰さない値 |
| `SNAPSHOT_EVENT_ID` | 空 | 対象イベント。空なら**直近のリクエストの `event_id`** を使う |
| `SNAPSHOT_TTL_SEC` | 300 | 再取得の間隔（既存の設定値を流用する） |

**`READONLY_PROXY_URL` が空でもサービスは正常に起動する。**
その場合スナップショットは常に「取得不能」となり、`COVERAGE` で動き続ける。
開発・テストでは空のまま動かす。

## 取得するもの

**テーブル名と列は定義から組み立てる。`SELECT *` を書かない。**
`event-support-analytics/src/rec_db.py` の `LIVE_TABLES` / `POST_TABLES` と同じ流儀にする。

| テーブル | 取る列 | 用途 |
|---|---|---|
| `check_ins` | `id, user_id, booth_id, event_id, visit_order, checked_in_at` | 訪問の事実 |
| `booth_ratings` | `checkin_id, user_id, booth_id, event_id, rating, scale` | **決定属性** |
| `user_survey_answers` | `user_id, event_id, age_range, occupation, industry, custom_answers` | 条件属性・近傍軸 |
| `booths` | `id, event_id, category_id, is_active` | ブースの分類 |
| `categories` | `id, event_id, name` | 関心分野との対応 |
| `users` | `id, role` | **`role='participant'` 以外を除外する** |

**取得してはならないもの:** `users.email` / `users.password_hash`。
権限の側でも読めないが、クライアント側でも列の定義に入れない（二重の壁）。

## イベントの絞り込み

**本番の DB には過去のイベントも同居する。絞らないと混ざる。**

- `check_ins` / `booth_ratings` / `booths` / `user_survey_answers` は `event_id` を持つので直接絞る
- **`users.event_id` では絞らない**（出展者・運営アカウントが混ざる）
- 絞り込みの規則は `data/` の1箇所にだけ書く。`strategies/` や `features/` には書かない

## 更新のしかた

- FastAPI の lifespan で**バックグラウンドタスク**を1本起動し、`SNAPSHOT_TTL_SEC` ごとに回す
- 起動直後に1回走らせる（**起動を待たせない。** 取得中も `COVERAGE` で応答できる）
- 1周でやること: 取得 → 決定表の組み立て（[02](02-decision-table.md)）→ 規則生成 → `RuleCache.put()` と `SnapshotCache.put()`
- **失敗したら前回のキャッシュを保持したまま、次の周期を待つ。** 空にしない
- 失敗は JSONL ログに `kind=snapshot` で残す。**SQL 本文と鍵は載せない**

## `SnapshotCache` の新設

`RuleCache` は規則しか持たない。しかし `SIMILARITY` は
**他の参加者の事前アンケート回答と評価**をリクエスト時に必要とする。

そこで、規則とは別に**スナップショットそのもの**を保持するキャッシュを新設する。

| 保持するもの | 用途 |
|---|---|
| 参加者ごとの事前アンケート回答 | 近傍の距離計算 |
| 参加者 × ブースの正規化済み評価 | 近傍の評価平均 |
| 全体の平均評価（`global_mean`） | ベイズ縮約 |
| 決定表の件数・構築時刻 | フェーズ判定・`/ops/state` |

- `RuleCache` と同じく**スレッドセーフ**にする（読みはリクエストスレッド、書きはバックグラウンド）
- **個人を特定できる列を持たない。** `user_id` は近傍の突き合わせにのみ使い、外へ出さない

## 「起きてはいけないこと」

- **リクエスト経路からこの層を呼ぶこと。** 呼んだ瞬間に1000msのタイムアウトを踏む
- **取得失敗を例外として上へ投げること。** `data/` の失敗は `EventSnapshot.unavailable()` で表す
- **書き込み可能な鍵（`SAKURA_PROXY_KEY`）をここに設定すること。** 分けた意味が消える
- **SQL 文字列を外から受け取ること。** テーブル名も列も定義から組み立てる
