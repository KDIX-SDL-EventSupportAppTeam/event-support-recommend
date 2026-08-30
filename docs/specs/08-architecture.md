---
状態: 草案
最終更新: 2026-08-29
---

# 構成と設定値

## 1. モジュールの境界

**依存は上から下への一方向のみ。** 下の層は上の層を知らない。

```
api/          FastAPI・Pydantic・HTTP          ← ここだけが HTTP を知る
  ↓
strategies/   COVERAGE / SIMILARITY / DRSA / FALLBACK
  ↓
features/     条件属性の計算（★分析側が import する公開 API）
drsa/         優越関係・近似・規則生成（純粋）
  ↓
data/         スナップショットの取得          ← ここだけが SQL / 外部 I/O を知る
```

| 層 | 依存してよいもの | 依存してはいけないもの |
|---|---|---|
| `api/` | 下位すべて | — |
| `strategies/` | `features/` `drsa/` | **FastAPI、SQL、HTTP** |
| `features/` | 標準ライブラリ・numpy | **FastAPI、SQL、`drsa/`、`strategies/`** |
| `drsa/` | 標準ライブラリ・numpy | **FastAPI、SQL、`features/`** |
| `data/` | DB ドライバ / HTTP クライアント | `strategies/` `api/` |

### なぜこの制約か

- **`features/` は `event-support-analytics` が import する。**
  FastAPI や DB ドライバに依存していると、分析側にサーバーの依存が伝染する
  （[ADR 0004（サーバー側）](https://github.com/KDIX-SDL-EventSupportAppTeam/event-support-server/blob/develop/docs/decisions/adrs/0004-split-recommender-repository.md)）
- **`data/` を1点に閉じ込めることで、取得経路の未決（[ADR 0002](../decisions/adrs/0002-決定表のデータ入手経路.md)）が
  他の層をブロックしない。** 経路が変わっても上位のコードは変わらない
- `drsa/` が純粋なので、手計算できる決定表でテストできる

---

## 2. ディレクトリ構成

```
event-support-recommend/
├── README.md
├── AGENTS.md / CLAUDE.md
├── pyproject.toml                  # パッケージ名 event_support_recommend
├── Dockerfile                      # Cloud Run
├── .dockerignore                   # ビルドコンテキストから .venv/.env/tests を除外
├── cloudbuild.yaml                 # Cloud Build → Cloud Run（11-deployment.md §1・§3）
├── .env.example
├── docs/
│   ├── README.md                   # ドキュメント索引
│   ├── PURPOSE.md                  # 存在意義。最初に読む
│   ├── specs/                      # 仕様（本ディレクトリ）
│   ├── decisions/adrs/             # 判断の記録
│   └── rules/                      # 作業規約
├── src/event_support_recommend/
│   ├── api/
│   │   ├── app.py                  # FastAPI 本体・lifespan
│   │   ├── schemas.py              # 契約の写像（Pydantic）
│   │   ├── routes_recommend.py     # POST /recommend/cells
│   │   └── routes_ops.py           # /health /ready /ops/*
│   ├── strategies/
│   │   ├── base.py                 # Strategy インターフェース
│   │   ├── registry.py             # STRATEGY による選択口（ADR 0007）
│   │   ├── coverage.py
│   │   ├── random.py               # 下限ベースライン（対照群・ADR 0007）
│   │   ├── similarity.py
│   │   └── drsa.py
│   ├── features/                   # ★公開 API
│   │   ├── attributes.py           # preference_match / rating_affinity / exploration
│   │   ├── interest_match.py       # MATCH / PARTIAL / MISMATCH / UNKNOWN
│   │   └── rating.py               # 正規化とクラス分け
│   ├── drsa/
│   │   ├── decision_table.py
│   │   ├── dominance.py            # 優越集合
│   │   ├── approximation.py        # 上下近似・境界・VC-DRSA
│   │   └── rules.py                # 全列挙による規則生成
│   ├── data/
│   │   └── repository.py           # スナップショット取得（経路は未決）
│   ├── cache/
│   │   └── rule_cache.py           # 5分ごとの再生成
│   ├── models.py                   # 層3の内部ドメインモデル
│   ├── phases.py                   # フェーズ判定（純関数）
│   ├── logging.py                  # JSONL 出力
│   └── settings.py                 # pydantic-settings
└── tests/
```

---

## 3. リクエストの流れ

```
起動時 & SNAPSHOT_TTL_SEC ごと（バックグラウンド）
  スナップショット取得 → 決定表を構築 → 件数を記録 → 規則を生成 → メモリに置く
        ↑ 唯一の重い処理。リクエスト経路の外

リクエスト（目標 50ms、予算超過で COVERAGE へ退避）
  1. Pydantic で受ける（壊れていても 200 を返す方針で寛容に）
  2. 決定表の件数 → phase 判定（設定値のしきい値）
  3. 全候補の条件属性と interest_match を計算       ← features/
  4. phase に応じた Strategy でスコアリング         ← strategies/
  5. 同スコアは visitor_count 昇順 → シード付き乱数
  6. 上位 cell_count 件を assigned、全件を scores にして返す
  7. 入出力を JSONL で1行ログに出す
```

**例外は握りつぶして COVERAGE 相当を返す。500 を返さない。**

---

## 4. 設定値

**しきい値をコードに直書きしない**（Q-2 は未確定）。

### フェーズ

```
PHASE_SIMILARITY_MIN=30
PHASE_DRSA_MIN=60                 # 属性2個での既定。3個にしたら 180
ENABLED_ATTRIBUTES=preference_match,rating_affinity
```

### 品質ゲート（DRSA 昇格の追加条件・[03-phases.md](03-phases.md) §3.3）

```
DRSA_MIN_RULES=3                  # 確実規則の本数
DRSA_MIN_GAMMA=0.5                # 近似の質
DRSA_MIN_COVERAGE=0.5             # 規則が候補を覆う割合
```

**`PHASE_DRSA_MIN` を極端に大きくすることが当日のキルスイッチになる**
（[10-observability.md](10-observability.md) §4）。

### 実験（参加者内ランダム化・[09-research-design.md](09-research-design.md)）

```
EXPERIMENT_SPLIT_ENABLED=true     # 品質ゲート通過後にのみ発動する
EXPERIMENT_ARM_A=DRSA
EXPERIMENT_ARM_B=COVERAGE
```

### 評価

```
RATING_SCALE_DEFAULT=4            # リクエストに rating_scale が無い場合のみ使う
HIGH_RATING_RATIO=0.75
LOW_RATING_RATIO=0.25
```

### 戦略

```
W_COVERAGE=0.5
W_INTEREST=0.5
SIMILARITY_NEIGHBORS=20
SIMILARITY_SHRINKAGE=5
SIMILARITY_COVERAGE_FLOOR=0.2
DRSA_COVERAGE_FLOOR=0.2
MAX_PER_CATEGORY=0                # 0 = 無効（既定）
```

### DRSA

```
DRSA_CONSISTENCY=0.8              # VC-DRSA の一貫性水準。1.0 で厳密版
MIN_SUPPORT=5                     # 正本の「各パターン最低5件」と同じ値
```

### 性能・キャッシュ

```
SNAPSHOT_TTL_SEC=300
RULE_CACHE_TTL_SEC=300
RESPONSE_BUDGET_MS=600            # サーバー側タイムアウト 1000ms に対する予算
```

### 運用

```
APP_ENV=development               # production で /demo を無効化し、OPS_TOKEN 未設定時は /ops/* も無効化する
LOG_RAW_REQUEST=false             # 当日の契約ズレ調査用
LOG_LEVEL=info
OPS_TOKEN=                        # /ops/* の保護。production では必須（11-deployment.md D-4）
ENGINE_VERSION=                   # 未設定ならパッケージ版を使う。本番はコミット SHA を入れる
```

### 戦略の選択（[ADR 0007](../decisions/adrs/0007-戦略の選択を環境変数で行う.md)）

```
STRATEGY=auto                     # auto | coverage | random
                                  # auto = フェーズ判定に従う（現状は必ず COVERAGE）
                                  # random は下限ベースライン。APP_ENV=production では auto に落とす
```

### データ取得（[ADR 0002](../decisions/adrs/0002-決定表のデータ入手経路.md) 決定後に確定）

```
# DATABASE_URL=
# SAKURA_PROXY_URL=
# SAKURA_PROXY_KEY=
# SERVER_INTERNAL_URL=
```

---

## 5. デプロイ

**詳細は [11-deployment.md](11-deployment.md)（構成・修正すべき点・確認項目）。ここでは前提だけ書く。**

- **Cloud Run**（サーバー・分析と同じ）
- **ステートレス。書き込みを一切行わない**
- 単一インスタンスで足りる規模。複数インスタンスでも動くが、
  規則キャッシュの生成タイミングがずれる（`rules_built_at` で識別可能）
- `RECOMMENDER_URL` が未設定でもサーバーは正常に動く。
  **こちらが落ちてもアプリ本体は止まらない**

---

## 6. 実装の順序

| 段 | 内容 | [ADR 0002](../decisions/adrs/0002-決定表のデータ入手経路.md) の決着が要るか |
|---|---|:-:|
| 1 | スケルトン（FastAPI・schemas・health）＋ **`COVERAGE`** ＋ `features/` | 不要 |
| 2 | `drsa/`（純粋部分）とそのテスト | 不要 |
| 3 | `data/`（スナップショット取得）＋ 規則キャッシュ | **必要** |
| 4 | `SIMILARITY` / `DRSA` の結線 | **必要** |

**段1だけでも本番投入する価値がある。**
サーバー側のフォールバックは関心分野を考慮しない「訪問者数が少ない順」だけなので、
`COVERAGE`（訪問者数が少ない順＋関心分野一致を優先）を返せるだけで、
去年の失敗に対する主要な対策は成立する。
