# 契約の扱い

## 1. 正本はここに無い

**HTTP 契約の正本は `event-support-server` の
`docs/specs/bingo-dynamic-unlock/05-recommender/contract.md` である。**

**内容をこのリポジトリにコピーしてはならない。**
コピーすると二重管理になり、必ずずれる
（[ADR 0004（サーバー側）](https://github.com/KDIX-SDL-EventSupportAppTeam/event-support-server/blob/develop/docs/decisions/adrs/0004-split-recommender-repository.md)）。

**書いてよいのは次の3つ。**

1. 正本への**リンク**
2. **実装から観測した事実**（「サーバーのコードはこう動いている」）
3. **こちら側の設計**（内部モデル・`attributes` / `reason` のスキーマなど）

## 2. 契約は変えられない

呼び出し元（`event-support-server`）は**実装・テスト済み**であり、
この API を呼ぶコードが既に入っている。**こちらが合わせる側である。**

契約を変えたくなったら、実装を変えるのではなく**サーバー側へ差し戻す。**
差し戻した事項は [docs/README.md](../README.md) の末尾に記録する。

## 3. 破ると静かに壊れるもの

サーバー側の検証（`recommenderClient.ts`）は**寛容に作られている。**
不正な値は例外にならず、**黙って捨てられるか既定値に潰される。**
つまり**壊しても気づかない。** 以下はテストで固定してある（[07-testing.md](../specs/07-testing.md) §2）。

| やらかし | 何が起きるか |
|---|---|
| `decisionTableSize`（camelCase）で返す | `null` として記録され、**データ量と精度の関係が永久に分析できない** |
| `scores` を一部しか返さない | サーバーが `UNKNOWN` で埋める。**「沈黙した」のか「手を抜いた」のか区別できず、セレンディピティ率の分母が壊れる** |
| 判定できるのに `interest_match: UNKNOWN` を返す | 同上。**研究データが丸ごと使えなくなる** |
| `candidate_booths` に無い `booth_id` を返す | 黙って捨てられる。返したのに反映されない |
| 500 を返す | `phase='COVERAGE'` / `decision_table_size=null` が記録される。**障害だったという事実が復元できない** |

**イベントは年1回、1日だけである。取らなかったデータは二度と取れない。**

## 4. 唯一の拡張口

サーバーが永続化するのは6列だけで、そのうち
**`attributes` と `reason` はサーバーが解釈せず JSON のまま保存する。**

- **トップレベルに独自フィールドを足しても保存されない**
- したがってアルゴリズムの変更を後から解釈するための情報は、
  **すべて `attributes` / `reason` に入れる**
- この2つを変えても、サーバー・DB・フロントの変更は**一切不要**

代わりに**自己記述の規律**を守る（[02-features.md](../specs/02-features.md) §6）。

- `v`（スキーマ版）・`strategy`・`enabled`・`engine.version` / `rules_built_at` を刻む
- 属性の意味を変えたら `v` を上げ、旧版の定義をドキュメントに残す
- **キー名の意味を後から変えない。** 変えるくらいなら新しいキーを足す
- **1行あたり数百バイトに収める**（3万行生成される）
