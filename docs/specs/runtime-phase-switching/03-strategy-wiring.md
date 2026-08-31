---
状態: 確定
最終更新: 2026-09-01
---

# 戦略の結線と退避（段4）

**中身の仕様は [04-strategies.md](../04-strategies.md) が正本。ここには書かない。**
このファイルが決めるのは「どう選び、どう落ちるか」だけである。

## 選ぶ手順

```
1. judged_phase = decide_phase(decision_table_size, settings, gate=品質ゲート)
2. STRATEGY が固定値（coverage / similarity / drsa / random）なら、それを試す
3. judged_phase の戦略を試す
4. 実行できなければ退避ラダーを下る
5. actual_phase = 実際に実行できた戦略  ← これを phase として返す
```

**`engine.py` の `actual_phase = Phase.COVERAGE` というハードコードを外す。**
これが本仕様の中心にある1行である。

## 退避ラダー

[04-strategies.md](../04-strategies.md) §5 の定義をそのまま実装する。

```
DRSA       ──規則が0本 / 規則キャッシュが未構築──▶ SIMILARITY
SIMILARITY ──近傍が作れない / スナップショット無し──▶ COVERAGE
COVERAGE   ────────────────────────────────────▶ 必ず成功する
```

退避の条件を具体化する。

| 判定 | 退避する条件 |
|---|---|
| DRSA → SIMILARITY | `RuleCache.ready` が false／規則0本／品質ゲート不通過／規則適用中の例外 |
| SIMILARITY → COVERAGE | `SnapshotCache` 未構築／近傍が1人も作れない／事前アンケート未回答／実行中の例外 |
| いずれ → COVERAGE | 実行時間が `RESPONSE_BUDGET_MS` を超えた（下記） |

**退避したことは隠さない。** `phase` に実際の戦略が出るほか、JSONL ログに
`judged_phase` と `actual_phase` の両方と、退避した理由を残す。

## 実行時間の予算

`RESPONSE_BUDGET_MS`（既定600）は現在どこからも読まれていない。**ここで結線する。**

- 戦略の実行が予算を超えたら、その場で打ち切って `COVERAGE` へ退避する
- サーバー側のタイムアウトは1000ms。600msで切ればネットワークぶんの余裕が残る
- **打ち切りは失敗ではない。** `phase=COVERAGE` として正常に200を返す

## `STRATEGY` による手動固定

[ADR 0007](../../decisions/adrs/0007-戦略の選択を環境変数で行う.md) の表に2行足す。

| 値 | 意味 |
|---|---|
| `auto`（既定） | フェーズ判定に従う。**本仕様の完了後、これが実際に機能する** |
| `coverage` | COVERAGE に固定 |
| `similarity` | SIMILARITY に固定。実行できなければ COVERAGE へ退避 |
| `drsa` | DRSA に固定。実行できなければラダーを下る |
| `random` | RANDOM に固定。`APP_ENV=production` では `auto` に落とす（従来どおり） |

**当日はこれを触らない**（[ADR 0009](../../decisions/adrs/0009-当日の切り替えは既定値のまま走らせ調整は事後に行う.md)）。
障害時に COVERAGE へ固定するための逃げ道として用意しておく。

## `SIMILARITY` の実装で特に注意すること

数式は [04-strategies.md](../04-strategies.md) §3 が正本。実装時に落としやすい点だけ挙げる。

- **ベイズ縮約は必須。** 無いと「近傍1人が高評価した」だけのブースが最上位に来る
- **`gender` を近傍の軸に入れない。** 去年の分布では「その他」が3名で、個人を指しうる
- **`visitor_count` をスコアに入れない**（S-2）
- 近傍の評価が無いブースは `global_mean` に落ちる。全候補が同点になるのを避けるため
  `SIMILARITY_COVERAGE_FLOOR`（0.2）で COVERAGE のスコアを混ぜる

## `DRSA` の実装で特に注意すること

- **リクエスト経路で規則を生成しない。** キャッシュ済みの規則を当てはめるだけ
- 適合規則が1本も無い候補は `score = 0.5`（判断保留）＋ `DRSA_COVERAGE_FLOOR`(0.2) で混ぜる。
  **全候補にスコアを付ける義務（S-1）を満たすため**
- `reason.rules` には**規則 id と要約だけ**を入れる。規則本体を入れると3万行になる

## 変わらないもの

- 応答の形（`01-io-contract.md`）は変えない。`phase` の値が実際に動くようになるだけ
- サーバー側は無改修で追随する（`card_unlock_events.phase` にそのまま入る）
- `COVERAGE` の実装は一切変更しない。**最後の砦なので触らない**

## 「起きてはいけないこと」

- **判定が DRSA なのに中身が COVERAGE の行が、`phase=DRSA` として記録されること。**
  これが起きると事後分析が全て無意味になる
- **退避で例外が外へ漏れること。** 500 を返さない（O-6）
- **キャッシュ未構築を理由に 503 を返すこと。** `COVERAGE` で200を返す
