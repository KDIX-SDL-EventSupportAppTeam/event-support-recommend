# event-support-recommend — エージェント向けメモ

イベント支援アプリの**推薦マイクロサービス**。ラフ集合（DRSA）でブースを推薦する。

## 作業前に読む（この順）

1. [docs/PURPOSE.md](docs/PURPOSE.md) — **成功の定義。外すと作業が無意味になる**
2. [docs/specs/01-io-contract.md](docs/specs/01-io-contract.md) — 何を受け取り何を返すか
3. [docs/rules/](docs/rules/README.md) — [契約](docs/rules/contract.md)・[実装](docs/rules/coding.md)・[データ](docs/rules/data-handling.md)・[文書](docs/rules/documentation.md)・[Git](docs/rules/git.md)
4. [docs/README.md](docs/README.md) — 索引と**未決定事項の一覧**

契約の正本は**このリポジトリに無い**。`event-support-server` の
`docs/specs/bingo-dynamic-unlock/05-recommender/contract.md` を読むこと。

## 絶対に外さないこと

> **成功の定義は踏破率ではない。**「参加者が自分では選ばなかったであろうブースを訪問し、
> それが新しい興味になること」。**人気ブースを全員に薦めて踏破率が上がるのは失敗である。**

去年これが崩れた（フォールバックの解除が早すぎ、推薦が人気度ランキングに退化した）。
**その再現を構造で禁じるのが本リポジトリ。評価指標を「当たった率」に置かない。**

## 禁止

| # | 禁止 | 壊れるもの |
|---|---|---|
| 1 | 人気順（`visitor_count` 降順）の推薦 | 上記。全戦略でテストにより禁止 |
| 2 | 契約の本文をこのリポジトリにコピーする | 二重管理になり必ずずれる |
| 3 | 500 を返す | 障害だった事実が研究データから消える |
| 4 | `scores` を一部しか返さない／判定できるのに `UNKNOWN` を返す | セレンディピティ率の分母 |
| 5 | DB へ書き込む | このサービスは読むだけ |
| 6 | 未決定事項を勝手に決めて実装する | [docs/README.md](docs/README.md) の一覧を参照 |
| 7 | `git push` / `.env`・`data/` のコミット | — |

## 構成と進め方

- 依存は `api/ → strategies/ → features/・drsa/ → data/` の一方向のみ（[08](docs/specs/08-architecture.md)）
- **`features/` は分析リポジトリが import する公開 API。** FastAPI・DB に依存させない
- 段1（`COVERAGE` ＋ `features/`）→ 段2（`drsa/`）→ 段3・4（`SIMILARITY` / `DRSA`）の順。
  **段3以降は [ADR 0002](docs/decisions/adrs/0002-決定表のデータ入手経路.md)（データ入手経路）の決着が必要。着手しない**
- テストは「起きてはいけないこと」から書く（[07](docs/specs/07-testing.md)）。
  最優先は**人気順になっていないこと**、次にフェーズ判定の境界値
- コミット・PR は**日本語**（`種別(範囲): 要約`）。`main` を直接触らず作業ブランチ → `develop` へ PR
  （`develop` は未作成。実装着手時に作る）
