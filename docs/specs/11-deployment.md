---
状態: 確定（§9 の 1〜6 実施済。初回デプロイ完了 2026-09-02）
最終更新: 2026-09-02
---

# デプロイ — Cloud Run

**本ファイルは実装指示である。** これを読んで実装すれば本番に出せる状態になることを目標にする。

## 実装状況

| §9 の手順 | 状態 |
|---|---|
| 1. `.dockerignore`（D-1）＋ ローカル `docker build` / `docker run`（D-5） | **済**（`APP_ENV=production` で起動し `/health` 200・`/ready` 503・`/ops/state` 401 を確認。**`/demo` は ADR 0008 で 401 に変わった**） |
| 2. `APP_ENV` と `/demo`・`/ops/*` の条件付き登録（D-3・D-4） | **済**（`api/app.py` の `create_app()`・`tests/test_deployment.py`） |
| 3. `STRATEGY` レジストリ（ADR 0007） | **済**（`strategies/registry.py`・`strategies/random.py`） |
| 4. `cloudbuild.yaml`（§1・§3） | **済** |
| 5. `.env.example` と [08-architecture.md](08-architecture.md) §4 | **済** |
| 6. デプロイ → §7 の確認 → [07-testing.md](07-testing.md) §12 へ反映 | **済**（2026-09-02 初回デプロイ。V-1〜V-8・V-14・V-15 合格。残りは §7.4） |

**初回デプロイの記録は §7.4。読むべき順序としては、まず [OPERATIONS.md](../OPERATIONS.md) の A-1
（`READONLY_PROXY_URL` 未設定のため現状のリビジョンは `COVERAGE` 固定である）を見ること。**

## 0. 前提の確認（ここを誤解しない）

| 誤解しやすいこと | 事実 |
|---|---|
| 「推薦エンジンが参加者 DB へ直接つなぐ」 | **つながない。** 1リクエストの判断材料はリクエストボディが全部（[01-io-contract.md](01-io-contract.md) §2）。決定表は別経路（読み取り専用プロキシ）から**定期取得**する（[ADR 0002](../decisions/adrs/0002-決定表のデータ入手経路.md) 採用・案A′） |
| 「デプロイに DB 接続情報が要る」 | **MySQL の接続情報は要らない。** ただし `READONLY_PROXY_URL` / `READONLY_PROXY_KEY` は要る。**無いと決定表が育たず一日 `COVERAGE` 固定**（[OPERATIONS.md](../OPERATIONS.md) A-1） |
| 「アルゴリズム切り替えの仕組みを作る必要がある」 | **継ぎ目は既にある**（`strategies/base.py` の `Strategy` Protocol）。足りないのは選択の口だけ（[ADR 0007](../decisions/adrs/0007-戦略の選択を環境変数で行う.md)） |
| 「最初はランダムで動かす」 | しない。`COVERAGE` が既に動いている。ランダムは**対照群・下限ベースライン**としてのみ持つ（[ADR 0007](../decisions/adrs/0007-戦略の選択を環境変数で行う.md)） |

**1リクエストの処理はステートレスで、外部依存が無い。** 推薦そのものは常に応答できる。
決定表の取得は背景タスクであり、**落ちても止まっても推薦は `COVERAGE` として成立する。**

---

## 1. 構成

`event-support-server` と同じ場所・同じ型に揃える。**運用者が2つの流儀を覚えなくて済むことを優先する。**

| 項目 | 値 | 根拠 |
|---|---|---|
| プラットフォーム | Cloud Run（managed） | サーバー・分析と同じ |
| リージョン | `asia-northeast1` | サーバーの `cloudbuild.yaml` と同一。リージョン間 RTT を足さない |
| Artifact Registry リポジトリ | `event-support` | 同上（既存を共用） |
| Cloud Run サービス名 | `event-support-recommend` | — |
| イメージ名 | `event-support-recommend` | — |
| ビルド | Cloud Build（`cloudbuild.yaml`） | サーバーと同じ |
| デプロイの起動 | **`main` への push（CD トリガー）** | サーバー・フロントと同じ（下記 §1.1） |

### 1.1 CD — `main` への push で自動デプロイする（2026-09-02 構成）

`event-support-server` / `event-support-frontend` と**同じ型に揃えた**。

| 項目 | 値 |
|---|---|
| トリガー名 | `deploy-event-support-recommend` |
| リージョン | `asia-northeast1`（`global` には作らない。既存2件もこちら） |
| GitHub 接続 | 第2世代。リポジトリごとに1接続（`event-support-recommend`） |
| 発火条件 | `^main$` への push |
| 設定ファイル | `cloudbuild.yaml` |
| 実行サービスアカウント | `cloud-build-deployer@event-support-app.iam.gserviceaccount.com` |

**トリガー経由なら `$COMMIT_SHA` が自動で入る。** 手動 `gcloud builds submit` で
`--substitutions` を忘れて `ENGINE_VERSION` が空になる事故（X-6）が構造的に消える。
**これが CD にする一番の実利であり、手動デプロイへ戻してはいけない理由である。**

構築時につまずいた点:

| 症状 | 原因 | 対処 |
|---|---|---|
| `connections create` が `could not assert Secret Manager permissions` | Cloud Build の P4SA（`service-<番号>@gcp-sa-cloudbuild…`）に Secret Manager 権限が無い。接続時に GitHub トークンを Secret として保存するため要る | P4SA に `roles/secretmanager.admin` を付与 |
| `repositories create` が `installation_state COMPLETE` を要求して失敗 | 接続が `PENDING_USER_OAUTH`。**GitHub 側の認可はブラウザ操作が必須** | `connections describe … --format="value(installationState.actionUri)"` の URL を開いて承認し、**対象リポジトリを App のインストール先に含める** |

**当日は `main` を凍結する。** マージがそのまま本番差し替えになるため
（[OPERATIONS.md](../OPERATIONS.md) §1.1・[ADR 0009](../decisions/adrs/0009-当日の切り替えは既定値のまま走らせ調整は事後に行う.md)）。

### サーバーと**揃えない**もの

サーバー側の設定には socket.io 起因の制約が入っている。**そのままコピーしてはいけない。**

| 設定 | サーバー | 本サービス | 理由 |
|---|---|---|---|
| `--session-affinity` | あり | **なし** | ステートレス。付けても得が無く、負荷が偏る |
| `--max-instances` | 1 | **4** | 1 に固定する理由（socket のインメモリ管理）が本サービスには無い。同時解放でキューイングさせない |
| `--timeout` | 3600 | **10s** | サーバー側は 1000ms で見切る（`RECOMMENDER_TIMEOUT_MS`）。3600 秒保持する意味が無い |
| `--min-instances` | 0 | **0**（当日のみ 1） | コスト。§6 を参照 |
| `--concurrency` | 既定 | **80**（既定のまま） | 1リクエストが軽い。§7 V-13 の実測で見直す |

---

## 2. デプロイ前に直すもの（着手順）

**この5件は「あると良い」ではなく「無いと事故る」。**

### D-1 `.dockerignore` が無い ★最優先

現状 `docker build .` はリポジトリ全体をビルドコンテキストとして送る。**`.venv/`（数百 MB）・`.git/`・`.env` を含む。**

- `.env` が Cloud Build へアップロードされる。イメージには入らない（`COPY` は `pyproject.toml` / `README.md` / `src` のみ）が、**秘密がビルドの経路に乗ること自体が事故**
- ビルドが不要に遅くなる

`event-support-server/.dockerignore` を下敷きに Python 向けへ置き換える。**最低限このすべてを除外する。**

```
.git .gitignore
.venv .pytest_cache __pycache__ *.pyc
.env .env.*        （.env.example は残す）
tests docs tools
.claude .vscode .idea
*.md               （README.md は Dockerfile が COPY するので残す）
```

### D-2 `/ready` はプローブに使わない ★起動失敗の原因になる

[routes_ops.py](../../src/event_support_recommend/api/routes_ops.py) の `/ready` は規則キャッシュが温まっていなければ 503 を返す。

段3・段4 の結線後、`/ready` は**スナップショットと規則が温まったかを正しく表す**ようになった。
ただし `READONLY_PROXY_URL` が未設定のあいだは定期取得が起動しないため、**503 のままである**
（これは仕様どおりの正しい挙動。[OPERATIONS.md](../OPERATIONS.md) A-1）。

**規則が0本でもサービスは正常である**（`COVERAGE` で応答できる）。
つまり `/ready` の 503 は「サービスが使えない」ことを意味しない。

- **Cloud Run のヘルスチェック（startup / liveness probe）に `/ready` を使ってはいけない。** 使うとリビジョンが起動しない
- **プローブは `/health` のみ。** `/health` は依存ゼロで即答する
- `/ready` の意味は変えない。監視用として残し、**紛らわしさを消すためレスポンスと docstring に「プローブに使わないこと」を明記する**

### D-3 `/demo` と `/demo/run` が未認証で公開される

[app.py](../../src/event_support_recommend/api/app.py) の `/demo` はパラメータ調整プレイグラウンド。§4 の方針でサービスを未認証公開にする以上、**本番では URL を知る誰でも開ける。**

**[ADR 0008](../decisions/adrs/0008-パラメータ調整画面の置き場所とデモログの分離.md) で決着した**
（当初の「本番では常に 404」は暫定措置だった。P-1・P-2 の未決が理由であり、両方とも決まった）。

- **`/demo` は `/ops/*` と同じ条件で登録し、同じ `require_ops()` で保護する**
- `APP_ENV=production` かつ `OPS_TOKEN` 未設定なら、`/ops/*` ともども**登録しない**（404）
- ログ汚染（`run_recommendation()` が JSONL を出す問題）は
  `kind: "recommend_demo"` への分離で断つ（ADR 0008 §1）

| `APP_ENV` | `OPS_TOKEN` | `/ops/*` | `/demo` |
|---|---|---|---|
| production | あり | 401 / 200 | **401 / 200** |
| production | なし | 404 | **404** |
| development | — | 素通し | 素通し |

### D-4 `OPS_TOKEN` 未設定時に `/ops/*` が素通しになる

`_require_ops()` は token が空なら**何も検証せず通す**（開発利便のため）。本番でこれが起きると `/ops/state` が無認証で読める。

- `APP_ENV=production` かつ `OPS_TOKEN` 未設定なら、**`/ops/*` を登録しない**（404）
- **起動時ログに状態を出す**（`kind: "startup"` に `ops_protected: true|false`）
- 本番では Secret Manager 経由で必ず設定する（§3）

**「本番なのに未設定」を、素通しではなく機能停止で表現する。** 書き込みを行わないサービスなので、止めても失うのは可観測性だけである。

### D-5 `Dockerfile` がビルド検証されていない

コミットはされているが一度も `docker build` を通していない。**デプロイ作業の最初にローカルでビルドと起動を通すこと。**

あわせて `CMD` に `--workers 1` を明示する。Cloud Run は `--concurrency` で並行を制御するので、ワーカー多重化はメモリを食うだけ。アクセスログは**残す**（レイテンシ調査に要る）。

---

## 3. 環境変数と Secret

### 追加する変数

| 変数 | 既定 | 意味 |
|---|---|---|
| `APP_ENV` | `development` | `production` かつ `OPS_TOKEN` 未設定なら `/ops/*` と `/demo` を無効化する（D-3・D-4・ADR 0008） |
| `STRATEGY` | `auto` | 戦略の選択。`auto` / `coverage` / `random`（[ADR 0007](../decisions/adrs/0007-戦略の選択を環境変数で行う.md)） |

**この2つ以外の新規変数を増やさない。** 既存の変数は [08-architecture.md](08-architecture.md) §4 が正本。

### Cloud Run に渡すもの

```
--update-env-vars = APP_ENV=production, ENGINE_VERSION=$COMMIT_SHA, STRATEGY=auto, LOG_LEVEL=info
--set-secrets     = OPS_TOKEN=RECOMMEND_OPS_TOKEN:latest
```

- **`ENGINE_VERSION` にコミット SHA を入れる。** 研究データの突合はこれが頼り。
  「どのリビジョンが出した推薦か」がログから復元できないと、当日の設定変更の前後を分けられない（[09-research-design.md](09-research-design.md) R-2）
- **しきい値（`PHASE_*`・`W_*`・`DRSA_*`）を Cloud Run 側で明示的に渡さない。** イメージ内の既定に任せる。
  当日キルスイッチとして `PHASE_DRSA_MIN` を上書きしたとき、**上書きしたことがコンソール上で一目で分かる状態を保つため**（[10-observability.md](10-observability.md) §4）
- Secret 名はサーバーの Secret と衝突しないよう `RECOMMEND_` を前置する

### `.env.example` の更新

`APP_ENV` と `STRATEGY` を追記し、[08-architecture.md](08-architecture.md) §4 と一致させる。**片方だけ更新しない。**

---

## 4. 認証 — 未認証公開でよい

**結論: `--allow-unauthenticated`。** 判断と根拠は [ADR 0006](../decisions/adrs/0006-推薦サービスを未認証で公開する.md)。要約:

- サーバーは `Authorization` ヘッダを付けずに呼んでいる（`recommenderClient.ts`）。認証を要求すると**実装・テスト済みのサーバーを改修することになる**（[ADR 0002](../decisions/adrs/0002-決定表のデータ入手経路.md) と同じ判断軸）
- 本サービスは **DB へ書かない・氏名や連絡先を受け取らない・状態を持たない**
- 残るリスクは**計算資源の浪費**と**研究データの汚染**。前者は `--max-instances` で頭打ちにし、後者は §5 で扱う

**ただし `/ops/*` と `/demo` は `OPS_TOKEN` で保護する**（D-3・D-4）。無防備にしてよいのは `/recommend/cells` と `/health` だけ。

---

## 5. 未認証公開に伴う手当て

**「大した問題ではない」で終わらせず、残るリスクに対応を書いておく。**

| リスク | 影響 | 対応 |
|---|---|---|
| 第三者が `/recommend/cells` を叩ける | 課金・インスタンス占有 | `--max-instances=4` で頭打ち。悪化したらサーバー側の `RECOMMENDER_URL` を新 URL に差し替える（安く速い） |
| **偽リクエストが JSONL ログに混ざる** | **研究データの汚染。実質的な最大リスク** | ログの `user_id` を DB の参加者一覧と突合して事後に除外する。**分析側の突合で落ちるので実装追加は不要**。§7 の確認項目に含める |
| URL の漏洩 | 上記の前提条件 | サービス URL を公開資料・スライド・リポジトリに載せない |

**個人情報の観点**: リクエストに含まれるのは `user_id`（内部 ID）・ブース ID・評価値・事前アンケート回答（年代・職業・興味カテゴリ）。**氏名・連絡先は含まれない**。`LOG_RAW_REQUEST=false` を本番既定として維持する（[rules/data-handling.md](../rules/data-handling.md)）。

---

## 6. 性能と当日運用

| 項目 | 決め |
|---|---|
| コールドスタート | Cloud Run + Python + numpy で**数秒かかる。`RESPONSE_BUDGET_MS=600` を必ず超える** |
| 当日の対応 | **イベント開始1時間前に `--min-instances=1` へ引き上げ、終了後 0 に戻す**（サーバーと同じ運用） |
| タイムアウトしたら | サーバーがフォールバックするのでアプリは止まらない（[08-architecture.md](08-architecture.md) §5）。ただし**フォールバック率が上がるぶん研究データが減る** |

切替コマンドと記録欄は [OPERATIONS.md](../OPERATIONS.md) §8。

**コールドスタートは「起きるかもしれない障害」ではなく「`min-instances=0` なら確実に起きること」として扱う。**

---

## 7. デプロイ後の確認項目

**[07-testing.md](07-testing.md) §12（当日リハーサル）の実施項目に本節を加える。**
単体テストでは一つも担保できない。

### 7.1 サービス単体

| # | 確認 | 期待 |
|---|---|---|
| V-1 | `GET /health` | 200・`engine_version` がデプロイしたコミット SHA |
| V-2 | `GET /ready` | **503。`READONLY_PROXY_URL` 未設定のあいだはこれが正常**（鍵の設定後は 200 になる） |
| V-3 | `GET /ops/state` トークン無し | **401** |
| V-4 | `GET /ops/state` トークン有り | 200・`phase.current` が `COVERAGE`・`decision_table_size` が `null` |
| V-5 | `GET /demo` トークン無し | **401**（`OPS_TOKEN` 設定時。未設定なら 404。ADR 0008 §2） |
| V-5b | `GET /demo` トークン有り | 200・画面上部に「これはシミュレータ」の警告が出ている（Q-5） |
| V-6 | `POST /recommend/cells` 正常ボディ | 200・`scores` が候補全件・`assigned` が `cell_count` 件 |
| V-7 | `POST /recommend/cells` 壊れたボディ（空・不正 JSON・型違い） | **200。500 も 422 も返らない**（[01-io-contract.md](01-io-contract.md) O-6） |
| V-8 | 同一リクエストを2回 | **完全に同じ出力**（シードが効いている） |

### 7.2 サーバーとの結合

| # | 確認 | 期待 |
|---|---|---|
| V-9 | サーバーの `RECOMMENDER_URL` を Cloud Run URL に設定して解放 | `card_unlock_events.strategy` が `RECOMMEND` |
| V-10 | 推薦サービスを停止して解放 | 解放は成功し `strategy` が `FALLBACK_COVERAGE` |
| V-11 | 実リクエストのログを目視 | **`pre_survey` のキー名が `age_range` か `age_group` か**（既知のズレ・`docs/README.md` の差し戻し事項1） |

### 7.3 性能・観測

| # | 確認 | 期待 |
|---|---|---|
| V-12 | コールドスタート後の初回応答時間 | 実測して記録する。**600ms を超えることの確認であって、超えたら失敗ではない** |
| V-13 | ウォーム状態の p95（候補40件想定） | 600ms 未満 |
| V-14 | Cloud Logging で JSONL が `jsonPayload` として構造化されているか | されている。**されていなければ `severity` を付ける修正が要る**（現状 `logging.py` は出していない） |
| V-15 | 1リクエストのログ行のサイズ | Cloud Logging のエントリ上限（256KB）に対して十分小さい |
| V-16 | キルスイッチ（`PHASE_DRSA_MIN` の引き上げ）が新リビジョンで効く | 効く |

**V-14 が落ちると事後のログ回収が手作業になる。** デプロイ当日に確認すること。

---

### 7.4 初回デプロイの結果（2026-09-02）

`develop` の `bc21695`（`ENGINE_VERSION` に同 SHA が入っていることを V-1 で確認）を Cloud Build から
デプロイした。リビジョン `event-support-recommend-00002-rph`・`asia-northeast1`。

| # | 結果 | 備考 |
|---|---|---|
| V-1 | **合格** | 200・`engine_version` が `bc21695…`（X-6 クリア） |
| V-2 | **合格** | 503。スナップショット未取得のため正常 |
| V-3 | **合格** | 401 |
| V-4 | **合格** | 200・`phase.current` = `COVERAGE`・`decision_table_size` = `null`・しきい値は既定のまま（X-5 クリア） |
| V-5 | **合格** | 401（ADR 0008 の想定どおり。当初仕様の 404 ではない） |
| V-5b | 未実施 | ブラウザからヘッダを付けられないため確認方法の検討が要る（下記 D-7） |
| V-6 | **合格** | 200・`scores` 5件（候補全件）・`assigned` 4件（`cell_count` 件） |
| V-7 | **合格** | 空オブジェクト・不正 JSON・型違い・null・空文字の5パターンすべて 200。500 も 422 も出ず（O-6 クリア） |
| V-8 | **合格** | 同一リクエスト2回が完全一致 |
| V-9〜V-11 | 未実施 | サーバー側の作業。当日リハーサルで実施 |
| V-12・V-13 | 未計測 | 当日リハーサルで実施 |
| V-14 | **合格** | `jsonPayload` として完全に構造化。`severity` 追加の修正は**不要** |
| V-15 | **合格** | 1リクエストあたり数 KB。256KB 上限に対して十分小さい |
| V-16 | 未実施 | 当日リハーサルで実施 |

**副次的に確認できたこと**: `visitor_count` が最多（40）の候補が `rank_in_event` 最下位になっており、
**人気順への退化が本番環境でも起きていない**（[04-strategies.md](04-strategies.md) の全戦略共通の不変条件）。

**デプロイ作業でつまずいた点**（次回のため）:

| 症状 | 原因 | 対処 |
|---|---|---|
| step 2 が `Secret … versions/latest was not found` で失敗 | Secret の**入れ物だけ**作られ、バージョン（中身）が無かった。`gcloud secrets create` を `--data-file` 無しで実行するとこうなる | `gcloud secrets versions add` で中身を入れる |
| デプロイは成功するが URL が 403 | `Setting IAM Policy……warning`。`--allow-unauthenticated` の `allUsers` 付与だけが失敗していた | `gcloud run services add-iam-policy-binding … --member=allUsers --role=roles/run.invoker` を手で実行 |
| `add-iam-policy-binding` が条件の選択を対話で聞いてくる | 既存ポリシーに条件付きバインディングがある | `--condition=None` を付ける |

### 7.5 この確認で新たに分かった、直すべきもの

#### D-6 `READONLY_PROXY_URL` が Cloud Run に渡されていない ★最重要

**現状のリビジョンは一日中 `COVERAGE` のまま動く。** 段3・段4 は `develop` で結線済みだが、
`cloudbuild.yaml` が `READONLY_PROXY_URL` / `READONLY_PROXY_KEY` を渡していないため
`build_repository()` が `UnavailableRepository` を返し、スナップショットの定期取得が起動しない。

- 決定表が育たない → `decision_table_size` が永久に `null` → 判定は常に `COVERAGE`
- **実装は正しいのに設定だけで研究が1本に縮む。** 最も気づきにくい壊れ方である
- 鍵は Secret Manager 経由にする（`RECOMMEND_READONLY_PROXY_KEY`）。§3 の命名規則に従う
- **`READONLY_PROXY_KEY` に書き込み可能な鍵を入れてはならない**（[settings.py](../../src/event_support_recommend/settings.py) の注記）

プロキシの設置一式は `event-support-analytics/deploy/sakura-readonly-proxy/` にある
（[ADR 0002](../decisions/adrs/0002-決定表のデータ入手経路.md)）。**URL が確定し次第 `cloudbuild.yaml` に追加する。**

#### D-7 `/demo` をブラウザから開く手段が無い

`/demo` は `X-Ops-Token` ヘッダか `Authorization: Bearer` を要求する（[routes_ops.py](../../src/event_support_recommend/api/routes_ops.py) `require_ops`）。
**ブラウザのアドレス欄からは任意ヘッダを付けられないため、V-5b を人が実施できない。**

ADR 0008 は `/demo` を推薦側に残すと決めたが、本番での**開き方**は決めていない。
クエリパラメータでのトークン受け取りは URL とログに秘密が残るので採らない。
**未決定として扱い、[docs/README.md](../README.md) の一覧に載せる。**

#### D-8 パース失敗のログが成功時と同じ `kind: "recommend"` で出る

リクエストのパースに失敗した行が `{"kind": "recommend", "error": …, "ts": …}` として出る。
`scores` を持たないため、分析側が `kind == "recommend"` で拾うと**スコアの無い行が混ざる**。

[ADR 0008](../decisions/adrs/0008-パラメータ調整画面の置き場所とデモログの分離.md) が `recommend_demo` を分離したのと同じ理屈で
`recommend_error` へ分けるのが素直に見えるが、**[10-observability.md](10-observability.md) の意図を確認してから決める。**
分析側の集計に影響するため、勝手に変えない。

---

## 8. 起きてはいけないこと

| # | 禁止 | 破ると |
|---|---|---|
| X-1 | `/ready` を Cloud Run のプローブに指定する | **リビジョンが永久に起動しない** |
| X-2 | `/demo` を `OPS_TOKEN` 無しで本番に出す | 誰でもパラメータを試せる。計算資源の増幅口にもなる（ADR 0008 §2・Q-3） |
| X-3 | `OPS_TOKEN` 未設定のまま本番へ出す | `/ops/*` が無認証で読める（D-4 の実装があれば 404 で止まる） |
| X-4 | サーバーの `cloudbuild.yaml` をそのままコピーする | `--session-affinity` / `--max-instances=1` / `--timeout=3600` を意味なく継承する（§1） |
| X-5 | しきい値を Cloud Run の env で上書きした状態を既定にする | **当日の変更点が見分けられなくなる**（§3） |
| X-6 | `ENGINE_VERSION` を空のままデプロイする | どのコードが出した推薦か復元できない。研究データの価値が落ちる |
| X-7 | デプロイを機に `main` へ直接 push する | [rules/git.md](../rules/git.md) |
| X-8 | `READONLY_PROXY_URL` 未設定のまま当日を迎える | **`SIMILARITY` / `DRSA` が一度も動かず、研究の主張が `COVERAGE` 1本に縮む**（D-6） |
| X-9 | `READONLY_PROXY_KEY` に書き込み可能な鍵を入れる | 読むだけのサービスが書けてしまう。ADR 0002 の前提が崩れる |
| X-10 | イベント当日に `main` へマージする | **CD が発火して本番が差し替わる**（§1.1・[OPERATIONS.md](../OPERATIONS.md) O-7） |

---

## 9. 実装の順序

1. `.dockerignore`（D-1）→ ローカルで `docker build` と `docker run` を通す（D-5）
2. `APP_ENV` の導入と `/demo`・`/ops/*` の条件付き登録（D-3・D-4）
3. `STRATEGY` によるレジストリ化（[ADR 0007](../decisions/adrs/0007-戦略の選択を環境変数で行う.md)）
4. `cloudbuild.yaml`（§1・§3）
5. `.env.example` と [08-architecture.md](08-architecture.md) §4 の更新
6. デプロイ → §7 の確認 → [07-testing.md](07-testing.md) §12 へ結果を反映

**1 と 2 はデプロイの前提。3 は独立しているので並行してよい。**
