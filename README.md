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

**段1（`COVERAGE` ＋ `features/`）と段2（`drsa/` コア）を実装済み**
（[ADR 0005](docs/decisions/adrs/0005-段1と段2のみ実装する.md)）。

| 項目 | 状態 |
|---|---|
| 仕様書 | 一通り揃った（[docs/README.md](docs/README.md)） |
| 実装 段1・段2 | **済**。`POST /recommend/cells` は `COVERAGE` を返す。`drsa/` は純粋・テスト済み |
| 実装 段3・段4（`data/`・規則キャッシュ・`SIMILARITY` / `DRSA` 結線） | **未着手**（[ADR 0002](docs/decisions/adrs/0002-決定表のデータ入手経路.md) の決着待ち） |
| データ入手経路（[ADR 0002](docs/decisions/adrs/0002-決定表のデータ入手経路.md)） | **未決定。`SIMILARITY` / `DRSA` はこれが決まるまで実装しない** |
| 事前アンケートの設問要求（[06](docs/specs/06-pre-survey-requirements.md)） | サーバー側の承認待ち。**締切はアンケート配布日** |

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

**`/demo` は `APP_ENV=production` では登録されない（404）。** 本番のサービスは未認証公開なので、
URL を知る誰でもパラメータを試せる状態にしない（[11-deployment.md](docs/specs/11-deployment.md) D-3）。

### デプロイ（Cloud Run）

構成・修正点・確認項目は [docs/specs/11-deployment.md](docs/specs/11-deployment.md) が正本。

```bash
docker build -t event-support-recommend:local .
docker run --rm -p 8080:8080 -e APP_ENV=production -e OPS_TOKEN=secret event-support-recommend:local
gcloud builds submit --config cloudbuild.yaml    # Cloud Build → Artifact Registry → Cloud Run
```

- **Cloud Run のプローブに `/ready` を使わない。** 段3 未結線のあいだ `/ready` は常に 503 が正常であり、
  プローブに指定するとリビジョンが永久に起動しない（[11-deployment.md](docs/specs/11-deployment.md) X-1）。
  プローブは依存ゼロの `/health`
- `APP_ENV=production` かつ `OPS_TOKEN` 未設定なら `/ops/*` も登録されない（無認証で素通しにしない・D-4）
- `STRATEGY=auto|coverage|random` で戦略を選ぶ（[ADR 0007](docs/decisions/adrs/0007-戦略の選択を環境変数で行う.md)）。
  `random` は**対照群・下限ベースライン**で、本番では `auto` に落ちる

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
