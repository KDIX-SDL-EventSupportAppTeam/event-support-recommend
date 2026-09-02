# event-support-recommend

第4回プロトフェス（2026-10-16 金）向け、イベント支援アプリの**推薦マイクロサービス**。

ビンゴカードの外周マスが解放される瞬間に `event-support-server` から HTTP で呼ばれ、
**参加者が自分では選ばなかったであろうブース**を提示する。
中核はラフ集合理論（**DRSA** — Dominance-based Rough Set Approach）による規則抽出。

## 成功の定義

> **踏破率でも訪問数でもない。**
> 参加者が自分では選ばなかったであろうブースを訪問し、
> それが本当に新しい興味・マッチングになること。

**人気ブースを全員に薦めれば踏破率は上がる。それは失敗である。**
去年、フォールバックの解除が早すぎた結果、推薦は事実上ブース人気度ランキングに退化した。
その反省を仕組みにしたのがこのサービスである。

詳細は [docs/PURPOSE.md](docs/PURPOSE.md)。

## 現在の状態

**段1〜段4 をすべて実装し、Cloud Run へデプロイ済み。**
残るリスクは実装ではなく**設定**（下記 A-1）である。

| 項目 | 状態 |
|---|---|
| 仕様書 | 一通り揃った（[docs/README.md](docs/README.md)） |
| 実装 段1・段2（`COVERAGE` ＋ `features/`・`drsa/` コア） | **済**。`drsa/` は純粋・テスト済み |
| 実装 段3・段4（`data/`・規則キャッシュ・`SIMILARITY` / `DRSA` 結線） | **済**。`data/` `strategies/` `cache/` が揃い、`tests/` は21本 |
| データ入手経路（[ADR 0002](docs/decisions/adrs/0002-決定表のデータ入手経路.md)） | **採用（案A′ 読み取り専用プロキシ）。** 設置一式は `event-support-analytics/deploy/sakura-readonly-proxy/` |
| 事前アンケートの設問要求（[06](docs/specs/06-pre-survey-requirements.md)） | サーバー側の承認待ち。**締切はアンケート配布日** |
| Cloud Run へのデプロイ | **初回デプロイ完了（2026-09-02）。** 確認 V-1〜V-8・V-14・V-15 合格（[11 §7.4](docs/specs/11-deployment.md)） |
| `READONLY_PROXY_URL` / `READONLY_PROXY_KEY` の設定 | **未。先生からの鍵の受領待ち。これが当日の最大リスク** |

> **当日サービスを預かる人は [docs/OPERATIONS.md](docs/OPERATIONS.md) を読むこと。**
> とくに **A-1: `READONLY_PROXY_URL` が未設定だと一日中 `COVERAGE` のまま動く**
> （段3・段4 は結線済みだが、設定が無いとスナップショットを取りに行かない）。
>
> **「実装が未着手だから `COVERAGE` のまま」ではない。** 実装は動いている。
> 設定だけで研究が1本に縮むという、最も気づきにくい壊れ方をする。

```bash
pip install -e ".[dev]" && pytest
uvicorn event_support_recommend.api.app:app --reload
```

### 推薦結果を目で見て確認する

合成シナリオを流し、候補ごとのスコア・`interest_match`・散布図（人気順に退化していないか）・
自動チェック（`docs/specs/07-testing.md` の「起きてはいけないこと」）と、DRSA コアが抽出した規則を可視化する。
**精度の最適化ではなく**、不変条件がどのパラメータ範囲まで崩れないかを見るためのもの。

```bash
python tools/build_report.py        # 既定パラメータの静的レポートを tools/out/index.html に生成
uvicorn event_support_recommend.api.app:app --port 8077   # 起動して…
```

- `GET /demo` … スライダーで `w_coverage` / `w_interest` / interest 重み / `l` / `min_support` などを
  動かすと、本物の Python エンジンで再計算する対話ページ
- `POST /demo/run` … `{"overrides": {...}}` を投げると再計算結果を JSON で返す（キーは既知・範囲内に丸め）

**本番の `/demo` は `/ops/*` と同じ `OPS_TOKEN` で保護される（トークン無しなら 401）。**
`APP_ENV=production` かつ `OPS_TOKEN` 未設定なら、`/ops/*` ともども**登録されない（404）**
（[ADR 0008](docs/decisions/adrs/0008-パラメータ調整画面の置き場所とデモログの分離.md)・[11-deployment.md](docs/specs/11-deployment.md) D-3）。
本番のサービスは未認証公開なので、URL を知る誰でもパラメータを試せる状態にしない。

**ただしブラウザのアドレス欄から `X-Ops-Token` ヘッダは付けられない。**
本番の `/demo` をどう開くかは**未決定**（[11 D-7](docs/specs/11-deployment.md)）。

### デプロイ（Cloud Run）

構成・修正点・確認項目は [docs/specs/11-deployment.md](docs/specs/11-deployment.md) が正本。

```bash
docker build -t event-support-recommend:local .
docker run --rm -p 8080:8080 -e APP_ENV=production -e OPS_TOKEN=secret event-support-recommend:local
# Cloud Build → Artifact Registry → Cloud Run
# 手動実行では $COMMIT_SHA が空になるため必ず明示する（空だとタグが壊れてビルドが失敗し、
# 通ってしまった場合は「どのコードが出した推薦か」が復元できなくなる・X-6）
gcloud builds submit --config cloudbuild.yaml \
  --substitutions=COMMIT_SHA="$(git rev-parse --short HEAD)"
```

- **Cloud Run のプローブに `/ready` を使わない。** `/ready` はスナップショットの状態を表すため、
  `READONLY_PROXY_URL` 未設定時やスナップショット未構築のあいだは 503 を返すのが正常である。
  プローブに指定するとリビジョンが永久に起動しない（[11-deployment.md](docs/specs/11-deployment.md) X-1）。
  プローブは依存ゼロの `/health`
- `APP_ENV=production` かつ `OPS_TOKEN` 未設定なら `/ops/*` も登録されない（無認証で素通しにしない・D-4）
- `STRATEGY=auto|coverage|random` で戦略を選ぶ（[ADR 0007](docs/decisions/adrs/0007-戦略の選択を環境変数で行う.md)）。
  `random` は**対照群・下限ベースライン**で、本番では `auto` に落ちる
- **`READONLY_PROXY_URL` を渡さないと `COVERAGE` 固定になる。**
  段3・段4 は結線済みだが、設定が無いと `UnavailableRepository` が選ばれ決定表が育たない
  （[11 D-6](docs/specs/11-deployment.md)）。**当日までに必ず設定する**

**当日の運用は [docs/OPERATIONS.md](docs/OPERATIONS.md)。**
デプロイ後の確認項目と初回デプロイの結果は [11-deployment.md](docs/specs/11-deployment.md) §7。

## API

実装するのは1本だけ（＋運用用のいくつか）。

```
POST /recommend/cells
```

**返すもの**

| フィールド | 中身 | 件数 |
|---|---|---|
| `phase` | 実際に使った戦略（`COVERAGE` / `SIMILARITY` / `DRSA`） | 1 |
| `decision_table_size` | その時点の決定表の件数 | 1 |
| `assigned` | **マスに載せるブース**（アプリのため） | `cell_count` 以下（2/4/6） |
| `scores` | **候補全件のスコア**（研究のため。セレンディピティ率の分母） | 候補と同数（約40件） |

**HTTP 契約の正本はこのリポジトリには無い。**
`event-support-server` の `docs/specs/bingo-dynamic-unlock/05-recommender/contract.md` が正本で、
本リポジトリは**リンクで参照し、内容をコピーしない**。

入出力の詳細と、アルゴリズムを差し替えても壊れない境界は
[docs/specs/01-io-contract.md](docs/specs/01-io-contract.md)。

## フェーズ

決定表（評価付きの (参加者, ブース) ペア）の件数で戦略を切り替える。

| フェーズ | 条件 | 戦略 |
|---|---|---|
| `COVERAGE` | < 30 | 訪問者数が少ない順 ＋ 関心分野一致を優先 |
| `SIMILARITY` | 30 以上 60 未満 | 属性が似た参加者が高評価したブース |
| `DRSA` | 60 以上 | DRSA 規則による推薦 |

しきい値は**設定値**であり、条件属性の個数に連動する
（[docs/specs/03-phases.md](docs/specs/03-phases.md)）。

## 技術

| 項目 | 選定 | 理由 |
|---|---|---|
| 言語 | Python | ラフ集合の実装と、`event-support-analytics` との特徴量共有 |
| フレームワーク | FastAPI + Uvicorn | 契約を型で守れる（[ADR 0004](docs/decisions/adrs/0004-fastapiを採用する.md)） |
| DRSA | **自前実装** | 使える Python 実装が存在しない（[ADR 0001](docs/decisions/adrs/0001-drsaを自前実装する.md)） |
| デプロイ | Cloud Run | ステートレス。**書き込みを一切行わない** |

## ドキュメント

| 読むもの | 内容 |
|---|---|
| [docs/PURPOSE.md](docs/PURPOSE.md) | **存在意義。最初に読む** |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | **当日の運用手引き。気を付けること・触ってはいけないもの** |
| [docs/README.md](docs/README.md) | ドキュメント索引と未決定事項の一覧 |
| [docs/specs/](docs/specs/) | 仕様 |
| [docs/decisions/adrs/](docs/decisions/adrs/) | 判断の記録 |
| [docs/rules/](docs/rules/README.md) | 守ること |
| [AGENTS.md](AGENTS.md) | エージェント向けの作業指針 |

## 関連リポジトリ

| リポジトリ | 関係 |
|---|---|
| [event-support-server](https://github.com/KDIX-SDL-EventSupportAppTeam/event-support-server) | **契約の正本。** ここから呼ばれる |
| [event-support-frontend](https://github.com/KDIX-SDL-EventSupportAppTeam/event-support-frontend) | 参加者・運営 UI |
| `event-support-analytics` | 指標の計算。**`features/` を import する** |
