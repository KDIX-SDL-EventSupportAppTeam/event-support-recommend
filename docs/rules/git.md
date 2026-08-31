# Git

## ブランチ

他リポジトリ（`event-support-server` / `event-support-frontend` / `event-support-analytics`）に揃える。

```
main ← develop ← 作業ブランチ
```

- **`main` を直接触らない**
- **作業ブランチは必ず `develop` から切る。** `main` から切らない
- 作業ブランチから `develop` へ PR を出す
- ブランチ名は `feat/…` `fix/…` `docs/…` `chore/…` `test/…`

> `develop` は作成済み。**`main` は初期コミット（`README.md` のみ）で止まっている。**
> 実体のあるトランクは `develop` なので、`main` を基点にすると作業ツリーが空になる。

## コミットメッセージ

**日本語で書く。** 形式は `種別(範囲): 要約`。

```
docs(specs): 入出力の定義と条件属性の仕様を追加する
feat(coverage): 訪問者数が少ない順の戦略を実装する
fix(features): 事前推薦マスの訪問を選好信号から除外する
test(drsa): 優越集合と近似の手計算ケースを追加する
```

- 種別: `feat` / `fix` / `docs` / `test` / `refactor` / `chore`
- 要約は**現在形の動詞で終える**（「〜する」「〜を追加する」）

## Pull Request

- **タイトルと本文も日本語**
- 本文に書くこと
  - 何を変えたか
  - **どの未決定事項に触れていないか**（勝手に決めていないことの明示）
  - 契約に影響するか（原則「しない」はず。するなら理由）
  - テストの結果

## エージェントの禁止事項

- **`main` へ push しない。** `main` は人間だけが触る
- **`.env` をコミットしない**
- **`data/` をコミットしない**（[data-handling.md](data-handling.md)）
- **契約の本文をコピーして持ち込まない**（[contract.md](contract.md)）

## エージェントがしてよいこと

- **作業ブランチの push と、`develop` 宛の PR 作成は自由に行ってよい。**
  レビューは PR 上で行うので、人間の実行を待たなくてよい
- ただし作業ブランチは `develop` から切ること（上記「ブランチ」を参照）
- PR 本文には**どの未決定事項に触れていないか**を必ず書く（下記「Pull Request」）。
  `develop` へマージするのは人間の判断
