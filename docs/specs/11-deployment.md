---
状態: 確定（§9 の 1〜5 まで実装済・デプロイ未実施）
最終更新: 2026-08-31
---

# デプロイ — Cloud Run

**本ファイルは実装指示である。** これを読んで実装すれば本番に出せる状態になることを目標にする。

## 実装状況

| §9 の手順 | 状態 |
|---|---|
| 1. `.dockerignore`（D-1）＋ ローカル `docker build` / `docker run`（D-5） | **済**（`APP_ENV=production` で起動し `/health` 200・`/ready` 503・`/demo` 404・`/ops/state` 401 を確認） |
| 2. `APP_ENV` と `/demo`・`/ops/*` の条件付き登録（D-3・D-4） | **済**（`api/app.py` の `create_app()`・`tests/test_deployment.py`） |
| 3. `STRATEGY` レジストリ（ADR 0007） | **済**（`strategies/registry.py`・`strategies/random.py`） |
| 4. `cloudbuild.yaml`（§1・§3） | **済** |
| 5. `.env.example` と [08-architecture.md](08-architecture.md) §4 | **済** |
| 6. デプロイ → §7 の確認 → [07-testing.md](07-testing.md) §12 へ反映 | **未実施**（V-1〜V-16 はデプロイ後に人が行う） |

## 0. 前提の確認（ここを誤解しない）

| 誤解しやすいこと | 事実 |
|---|---|
| 「推薦エンジンが DB を読む」 | **読まない。** 判断材料はリクエストボディが全部（[01-io-contract.md](01-io-contract.md) §2）。`data/` は `UnavailableRepository` のみ（[ADR 0002](../decisions/adrs/0002-決定表のデータ入手経路.md) 未決） |
| 「デプロイに DB 接続情報が要る」 | **要らない。** MySQL・さくらプロキシへの結線は段3以降の話 |
| 「アルゴリズム切り替えの仕組みを作る必要がある」 | **継ぎ目は既にある**（`strategies/base.py` の `Strategy` Protocol）。足りないのは選択の口だけ（[ADR 0007](../decisions/adrs/0007-戦略の選択を環境変数で行う.md)） |
| 「最初はランダムで動かす」 | しない。`COVERAGE` が既に動いている。ランダムは**対照群・下限ベースライン**としてのみ持つ（[ADR 0007](../decisions/adrs/0007-戦略の選択を環境変数で行う.md)） |

**このサービスはステートレスで、外部依存が一つも無い。** だから今のコードのままデプロイできる。

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

### D-2 `/ready` は常に 503 を返す ★起動失敗の原因になる

[routes_ops.py](../../src/event_support_recommend/api/routes_ops.py) の `/ready` は規則キャッシュが温まっていなければ 503 を返す。段3が未結線である以上、**本番では永久に 503 である**（これは仕様どおりの正しい挙動）。

- **Cloud Run のヘルスチェック（startup / liveness probe）に `/ready` を使ってはいけない。** 使うとリビジョンが起動しない
- **プローブは `/health` のみ。** `/health` は依存ゼロで即答する
- `/ready` の意味は変えない。監視用として残し、**紛らわしさを消すためレスポンスと docstring に「プローブに使わないこと」を明記する**

### D-3 `/demo` と `/demo/run` が未認証で公開される

[app.py](../../src/event_support_recommend/api/app.py) の `/demo` はパラメータ調整プレイグラウンド。§4 の方針でサービスを未認証公開にする以上、**本番では URL を知る誰でも開ける。**

- `APP_ENV=production` では `/demo` `/demo/run` を**登録しない**（404）
- 「認証をかける」ではなく「存在させない」。ルーティング自体を条件付きにする

**この 404 は暫定である。** `/demo` の最終的な置き場所と、
`/demo/run` が観測ログを汚す問題（`run_recommendation()` が JSONL を出す）は
[parameter-tuning/README.md](parameter-tuning/README.md) で検討中・**未決定**。
**決着まではこの 404 で塞いでおく**（塞いだ状態がいちばん安全側であり、デプロイを止める理由にはならない）。

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
| `APP_ENV` | `development` | `production` で `/demo` を無効化し、`OPS_TOKEN` 未設定時に `/ops/*` を無効化する（D-3・D-4） |
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

**ただし `/ops/*` は `OPS_TOKEN` で保護し、`/demo` は本番で消す**（D-3・D-4）。無防備にしてよいのは `/recommend/cells` と `/health` だけ。

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

**コールドスタートは「起きるかもしれない障害」ではなく「`min-instances=0` なら確実に起きること」として扱う。**

---

## 7. デプロイ後の確認項目

**[07-testing.md](07-testing.md) §12（当日リハーサル）の実施項目に本節を加える。**
単体テストでは一つも担保できない。

### 7.1 サービス単体

| # | 確認 | 期待 |
|---|---|---|
| V-1 | `GET /health` | 200・`engine_version` がデプロイしたコミット SHA |
| V-2 | `GET /ready` | **503。これが正常**（段3未結線） |
| V-3 | `GET /ops/state` トークン無し | **401** |
| V-4 | `GET /ops/state` トークン有り | 200・`phase.current` が `COVERAGE`・`decision_table_size` が `null` |
| V-5 | `GET /demo` | **404**（`APP_ENV=production`） |
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

## 8. 起きてはいけないこと

| # | 禁止 | 破ると |
|---|---|---|
| X-1 | `/ready` を Cloud Run のプローブに指定する | **リビジョンが永久に起動しない** |
| X-2 | `APP_ENV=production` で `/demo` が生きている | 誰でもパラメータを試せる。誤解を招く画面が公開される |
| X-3 | `OPS_TOKEN` 未設定のまま本番へ出す | `/ops/*` が無認証で読める（D-4 の実装があれば 404 で止まる） |
| X-4 | サーバーの `cloudbuild.yaml` をそのままコピーする | `--session-affinity` / `--max-instances=1` / `--timeout=3600` を意味なく継承する（§1） |
| X-5 | しきい値を Cloud Run の env で上書きした状態を既定にする | **当日の変更点が見分けられなくなる**（§3） |
| X-6 | `ENGINE_VERSION` を空のままデプロイする | どのコードが出した推薦か復元できない。研究データの価値が落ちる |
| X-7 | デプロイを機に `main` へ直接 push する | [rules/git.md](../rules/git.md) |

---

## 9. 実装の順序

1. `.dockerignore`（D-1）→ ローカルで `docker build` と `docker run` を通す（D-5）
2. `APP_ENV` の導入と `/demo`・`/ops/*` の条件付き登録（D-3・D-4）
3. `STRATEGY` によるレジストリ化（[ADR 0007](../decisions/adrs/0007-戦略の選択を環境変数で行う.md)）
4. `cloudbuild.yaml`（§1・§3）
5. `.env.example` と [08-architecture.md](08-architecture.md) §4 の更新
6. デプロイ → §7 の確認 → [07-testing.md](07-testing.md) §12 へ結果を反映

**1 と 2 はデプロイの前提。3 は独立しているので並行してよい。**
