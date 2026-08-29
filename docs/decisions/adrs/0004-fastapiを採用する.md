# ADR 0004 — Web フレームワークに FastAPI を採用する

- **状態:** 採用
- **日付:** 2026-08-29

## 背景

言語は Python で確定している
（[サーバー側 ADR 0004](https://github.com/KDIX-SDL-EventSupportAppTeam/event-support-server/blob/develop/docs/decisions/adrs/0004-split-recommender-repository.md)。
ラフ集合の実装と、`event-support-analytics` との特徴量共有のため）。

Web フレームワークは未指定である。実装するエンドポイントは
`POST /recommend/cells` **1本**と、運用用のいくつかだけ。

## 決定

**FastAPI + Uvicorn** を採用する。

## 理由

| 観点 | 内容 |
|---|---|
| **契約を型で守れる** | Pydantic v2 のモデルが契約の写像になる。**`decision_table_size` を camelCase で返す事故が型レベルで起きなくなる。** この事故は研究データを永久に失う最悪のもので、型で殺せる価値が高い |
| **寛容な入力を宣言的に書ける** | `pre_survey: dict \| None`、`rating: int \| None`。「未回答・空・null は通常の入力」という要件と相性が良い |
| **契約のズレを機械で見つけられる** | 自動生成される OpenAPI を `recommenderClient.ts` の型と突き合わせられる（[07-testing.md](../../specs/07-testing.md) C-7） |
| **キャッシュの置き場所がある** | ASGI の lifespan + バックグラウンドタスクで、規則の再生成をリクエスト経路の外に置ける。**これは契約が明示的に要求している設計**（規則は5分キャッシュ、リクエストでは当てはめのみ） |
| 運用 | Cloud Run 前提（サーバー・分析と同じ）。ASGI なので同時リクエストを捌ける |

## 却下した案

| 案 | 却下の理由 |
|---|---|
| Flask | 上記の「型で契約を守る」が手書きバリデーションになる。バックグラウンドタスクも自前で用意することになる |
| フレームワークなし（素の ASGI） | 得るものが無い。OpenAPI も失う |

## 結果

- 依存は FastAPI / Uvicorn / Pydantic / pydantic-settings / numpy 程度に留める
- **`api/` 層はビジネスロジックを持たない。** 戦略・特徴量・DRSA コアは
  FastAPI を import しない（[08-architecture.md](../../specs/08-architecture.md)）
- `features/` は分析側が import するので、**FastAPI に依存させない**
