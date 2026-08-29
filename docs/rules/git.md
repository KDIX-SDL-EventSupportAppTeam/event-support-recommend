# Git

## ブランチ

他リポジトリ（`event-support-server` / `event-support-frontend` / `event-support-analytics`）に揃える。

```
main ← develop ← 作業ブランチ
```

- **`main` を直接触らない**
- 作業ブランチから `develop` へ PR を出す
- ブランチ名は `feat/…` `fix/…` `docs/…` `chore/…` `test/…`

> **現状このリポジトリには `main` しか無い。**
> 実装に入る前に `develop` を作る（初期のドキュメント整備が終わった時点で分岐させる）。

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

- **`git push` をしない。** 人間がレビュー後に実行する
- **`.env` をコミットしない**
- **`data/` をコミットしない**（[data-handling.md](data-handling.md)）
- **契約の本文をコピーして持ち込まない**（[contract.md](contract.md)）
