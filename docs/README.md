# ドキュメント索引

## 最初に読むもの

| 知りたいこと | 見る場所 |
|---|---|
| **このリポジトリの存在意義** | [PURPOSE.md](PURPOSE.md) ← **最初に読む** |
| 何を受け取り何を返すのか | [specs/01-io-contract.md](specs/01-io-contract.md) |
| 判断の記録 | [decisions/adrs/](decisions/adrs/) |
| 守ること | [rules/](rules/README.md) |

**HTTP 契約の正本はこのリポジトリには無い。**
`event-support-server` の `docs/specs/bingo-dynamic-unlock/05-recommender/contract.md` が正本であり、
本リポジトリは**リンクで参照し、内容をコピーしない**
（[ADR 0004（サーバー側）](https://github.com/KDIX-SDL-EventSupportAppTeam/event-support-server/blob/develop/docs/decisions/adrs/0004-split-recommender-repository.md)）。

## 仕様

読む順序に並べてある。

| # | ファイル | 内容 |
|---|---|---|
| 01 | [io-contract](specs/01-io-contract.md) | **入出力の定義。** 何が固定で、どこが唯一の拡張口か |
| 02 | [features](specs/02-features.md) | **条件属性の定義。** 分析リポジトリとの事実上の契約 |
| 03 | [phases](specs/03-phases.md) | フェーズ判定としきい値 |
| 04 | [strategies](specs/04-strategies.md) | 3つの戦略の中身と、全戦略に共通する不変条件 |
| 05 | [drsa](specs/05-drsa.md) | DRSA コア（優越関係・近似・規則生成） |
| 06 | [pre-survey-requirements](specs/06-pre-survey-requirements.md) | 事前アンケートへの要求（**サーバー側の承認待ち**） |
| 07 | [testing](specs/07-testing.md) | テスト方針。「起きてはいけないこと」と**事前検証** |
| 08 | [architecture](specs/08-architecture.md) | モジュール境界・ディレクトリ・設定値・実装順序 |
| 09 | [research-design](specs/09-research-design.md) | **実験設計。** 何と何を比べて何を主張するか |
| 10 | [observability](specs/10-observability.md) | 推薦エンジンが外へ出す観測データ。当日の対応行動 |
| 11 | [deployment](specs/11-deployment.md) | **Cloud Run へのデプロイ。** 構成・修正すべき点・デプロイ後の確認項目 |
| — | [parameter-tuning](specs/parameter-tuning/README.md) | パラメータ調整画面（`/demo`）の置き場所とデモログの分離。**確定**（[ADR 0008](decisions/adrs/0008-パラメータ調整画面の置き場所とデモログの分離.md)） |

**ダッシュボードの実装仕様は `event-support-analytics` にある**
（`docs/specs/recommendation-evaluation/`）。本リポジトリは観測データの提供までを担う。

## 判断の記録（ADR）

| # | 判断 | 状態 |
|---|---|---|
| [0001](decisions/adrs/0001-drsaを自前実装する.md) | DRSA を自前実装する | 採用 |
| [0002](decisions/adrs/0002-決定表のデータ入手経路.md) | 決定表のデータ入手経路 | **未決定** |
| [0003](decisions/adrs/0003-条件属性の構成.md) | 条件属性の構成（2個＋予備1個） | 採用（暫定） |
| [0004](decisions/adrs/0004-fastapiを採用する.md) | FastAPI を採用する | 採用 |
| [0005](decisions/adrs/0005-段1と段2のみ実装する.md) | 実装は段1・段2に限る（ADR 0002 未決のため） | 採用 |
| [0006](decisions/adrs/0006-推薦サービスを未認証で公開する.md) | 推薦サービスを未認証で公開する | 採用 |
| [0007](decisions/adrs/0007-戦略の選択を環境変数で行う.md) | 戦略の選択を環境変数（`STRATEGY`）で行う | 採用 |
| [0008](decisions/adrs/0008-パラメータ調整画面の置き場所とデモログの分離.md) | `/demo` は推薦側に残し `OPS_TOKEN` で保護。デモ・リプレイのログを `kind` で分ける | 採用 |

## 現在の状態

**段1（`COVERAGE` ＋ `features/`）と段2（`drsa/` コア）を実装済み**
（[ADR 0005](decisions/adrs/0005-段1と段2のみ実装する.md)）。

| 項目 | 状態 |
|---|---|
| 仕様書 | 一通り揃った（本ページの 01〜10） |
| 実装 段1・段2 | **済**（`src/event_support_recommend/`、`tests/`） |
| デプロイ | **実装済・未実施。** [11-deployment.md](specs/11-deployment.md) の D-1〜D-5 と `cloudbuild.yaml` は揃った。残るのは実際のデプロイと §7 の確認（V-1〜V-16） |
| 実装 段3・段4 | **未着手**（`data/` 結線・規則キャッシュ・`SIMILARITY` / `DRSA`） |
| [ADR 0002](decisions/adrs/0002-決定表のデータ入手経路.md)（データ入手経路） | **未決定。`SIMILARITY` / `DRSA` はこれが決まるまで実装しない** |
| [06 の設問要求](specs/06-pre-survey-requirements.md) | サーバー側の承認待ち。**締切はアンケート配布日** |

## 未決定事項の一覧

決まるまで実装しないもの。**勝手に決めない。**

| 出典 | 項目 |
|---|---|
| [ADR 0002](decisions/adrs/0002-決定表のデータ入手経路.md) | 決定表のデータをどこから取るか |
| [09 RD-1](specs/09-research-design.md) | **参加者内ランダム化を実施するか（実装着手前に決める）** |
| [09 RD-2](specs/09-research-design.md) | 品質ゲートのしきい値（規則3本・γ 0.5・被覆率 0.5）。測定は [07 §8.1](specs/07-testing.md) の地図（`tools/build_prevalidation_map.py`）。決定は未 |
| [09 RD-3](specs/09-research-design.md) | 去年ブースへのカテゴリ付与を行うか |
| [ADR 0007](decisions/adrs/0007-戦略の選択を環境変数で行う.md) | **`RANDOM` に P-6 を課すか、対照群としての乱択性を採るか。** ADR は両方を要求しているが両立しない（P-6 を守るとクラス内が同点になり、順位が `visitor_count` 昇順で決まって COVERAGE と同じ反人気バイアスが残る）。現状は ADR の明文どおり P-6 を優先。事前検証で下限として使う前に決める |
| [02-features F-1](specs/02-features.md) | `exploration_disposition` を有効化するか |
| [02-features F-2](specs/02-features.md) | `top_interest_category` の設問が採用されるか |
| [02-features F-3](specs/02-features.md) | `booth_tags` を使うか |
| [03-phases](specs/03-phases.md) | しきい値 30 / 60（正本は 30 / 180。属性個数に連動）。30/60 での規則の出方は [07 §8.1](specs/07-testing.md) の地図を参照。決定は未 |
| [04-strategies T-1](specs/04-strategies.md) | `MAX_PER_CATEGORY` を有効化するか |
| [05-drsa D-1](specs/05-drsa.md) | `DRSA_CONSISTENCY` の既定 0.8 |

## サーバー側へ差し戻した事項

こちらでは直せないもの（契約はコピーせずリンク参照する規約のため）。

| # | 内容 |
|---|---|
| 1 | `contract.md` のリクエスト例が `age_group`。実装は `age_range`（Q-11） |
| 2 | `admin/survey-questions.ts` が `question_key` / `answer_type` を書き込めない（[06 B-1](specs/06-pre-survey-requirements.md)） |
| 3 | 本番の事前アンケート設問セットが存在しない（[06 B-2](specs/06-pre-survey-requirements.md)） |
| 4 | `sample-data/generate.ts` の `custom_answers` が UUID キー・`age_range` が日本語ラベル（[06 B-3](specs/06-pre-survey-requirements.md)） |
| 5 | `OpsStateClient.fetch()` が認証ヘッダを送らない（[parameter-tuning Q-1](specs/parameter-tuning/README.md)）。`/ops/state` が常に 401 になる。**`event-support-analytics` 側の修正** |
