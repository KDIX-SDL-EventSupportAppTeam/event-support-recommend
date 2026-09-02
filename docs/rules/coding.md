# 実装の規約

## モジュール境界

依存は**上から下への一方向のみ**（[08-architecture.md](../specs/08-architecture.md) §1）。

```
api/  →  strategies/  →  features/ · drsa/  →  data/
```

| 禁止 | 理由 |
|---|---|
| `features/` が FastAPI・DB ドライバに依存する | **分析リポジトリが import する。** 依存が伝染する |
| `strategies/` が SQL・HTTP を知る | 取得経路が変わるたびに戦略を書き直すことになる |
| `drsa/` が `features/` を知る | 手計算できる決定表でテストできなくなる |
| リクエスト処理中に規則生成・スナップショット取得を走らせる | 当日のピーク（約9.3チェックイン/分）で全滅する |

## 例外の方針

**500 を返さない。** これは UX ではなく研究データの問題である
（[rules/contract.md](contract.md) §3）。

```
例外 → 握りつぶす → COVERAGE 相当を返す → WARN でログに残す
```

- `phase` には**実際に使えた戦略**を返す。判定結果ではない
- 情報が無いときは「無関係」側へ倒す（`rating_affinity = 2` など）。
  無根拠に順位を動かさない
- **`pre_survey = null`、`visited_booths = []`、`rating` が全 `null` は
  異常ではなく通常の入力。** 開場直後は全部そうなる

## 設定値

**しきい値をコードに直書きしない**（Q-2 が未確定のため）。

- `settings.py`（pydantic-settings）に集約する
- 既定値はドキュメント（[08-architecture.md](../specs/08-architecture.md) §4）と一致させる
- **`ENABLED_ATTRIBUTES` と `PHASE_DRSA_MIN` は連動する。片方だけ変えない**

## 決定性

アルゴリズムを何度も作り替えるので、**同じ入力から同じ出力**が出ることを保てる形にする。

- 乱数のシードは `hash(user_id, unlock 文脈)`。グローバルな乱数を使わない
- 規則生成は入力の順序に依存しない（全列挙を採る理由のひとつ）
- 規則 id は内容から決まる安定 id。連番にしない

## 名前

- ドメイン用語はサーバー側の `docs/ubiquitous-language.md` に合わせる
- **`preference_match`（条件属性）と `interest_match`（契約の出力）を混同しない。** 別物である
- 契約に現れる名前（`decision_table_size` など）は**契約の綴りをそのまま使う**

## 依存

FastAPI / Uvicorn / Pydantic / pydantic-settings / numpy 程度に留める。
**ラフ集合のライブラリは使わない**（[ADR 0001](../decisions/adrs/0001-drsaを自前実装する.md)）。
