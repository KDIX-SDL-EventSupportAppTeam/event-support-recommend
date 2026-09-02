---
状態: 確定
最終更新: 2026-09-01
---

# 観測と連携

**当日、フェーズが本当に切り替わったことを、外から確認できるようにする。**
`/ops/state` は既に実装されている。中身が空なので `COVERAGE` を返しているだけである。
段3・段4 が入れば**自動的に真実を語るようになる**。ここではその前提と、外側との連携を定める。

## `/ops/state` に出るもの（既存。形は変えない）

| フィールド | 段3・段4 後の意味 |
|---|---|
| `snapshot.built_at` / `snapshot.ok` | 最後にスナップショットを作れた時刻 |
| `snapshot.decision_table_size` | **評価済み件数。フェーズが上がるまでの距離** |
| `rules.count_certain_up` / `count_certain_down` | 抽出できた確実規則の本数 |
| `rules.gamma` | 近似の質 |
| `phase.current` | **実際に使っている戦略**（`actual_phase`） |
| `phase.quality_gate_passed` / `gate_detail` | DRSA へ上がれない理由の内訳（4項目のどれが欠けているか） |
| `config.phase_similarity_min` / `phase_drsa_min` | 判定に使っているしきい値 |

**`notes` の `"SIMILARITY/DRSA not wired: ADR 0002 undecided"` を削除する。**
実装後もこれが残っていると、当日の判断を誤らせる。

## `/ready` の意味が変わる

段3 の結線により、**スナップショットと規則が温まったかを正しく表す**ようになった。
`READONLY_PROXY_URL` 未設定のあいだは定期取得が起動しないため 503 のままである。

- **それでも Cloud Run のプローブに使ってはならない**（[11-deployment.md](../11-deployment.md) D-2）。
  規則が0本でもサービスは正常（`COVERAGE` で応答できる）であり、
  プローブにすると正常なリビジョンが起動しない
- 監視用途に限る。プローブは依存ゼロで即答する `/health` を使う

## `/ops/rebuild` を実装する

現在は `{"rebuilt": false, "reason": "snapshot path not wired"}` を返すだけ。
段3 の後は**その場でスナップショット取得と規則再生成を1周させる**。

- 当日「フェーズが上がるはずなのに上がらない」ときの調査手段になる
- **通常は使わない。** 5分ごとの自動更新で足りる

## ログ（JSONL）

[10-observability.md](../10-observability.md) の形に従う。段3・段4 で足すもの。

| kind | いつ | 中身 |
|---|---|---|
| `snapshot` | 取得のたび（5分ごと） | 成否・所要時間・決定表の件数・規則本数・γ・失敗理由 |
| `recommend` | 既存 | **`judged_phase` と `actual_phase` の両方**、退避した場合はその理由 |

**フェーズが変わった瞬間は特に重要である。** 前回と `actual_phase` が変わったときは
`phase_changed` を真にして出す。事後分析で切り替わり時刻を1行で特定できるようにするため。

## 外側との連携

```
[event-support-recommend]  /ops/state  ← 唯一の正本
        ├──────────────────────────────┐
        ▼                              ▼
[event-support-server]            [event-support-analytics]
  中継して運営ダッシュボードへ        当日監視画面（直接読む・実装済み）
```

- **サーバーは自分でフェーズを計算してはならない。** 現在 `dashboard.ts` が
  `determinePhase(評価件数)` を独自に計算しており、推薦エンジンの実挙動と食い違う。
  仕様は `event-support-server/docs/specs/recommender-phase-linkage/` に書いた
- 分析リポジトリは既に `/ops/state` を `X-Ops-Token` 付きで読む実装になっている。**変更は不要**
- 認証ヘッダは `X-Ops-Token` を使う。`Authorization` は使わない
  （Cloud Run の IAM 認証と層が衝突するため。[ADR 0008](../../decisions/adrs/0008-パラメータ調整画面の置き場所とデモログの分離.md) Q-1）

## 当日の対応行動

| 見えたもの | 意味 | すること |
|---|---|---|
| `phase.current` が `COVERAGE` のまま | 評価が溜まっていない | **正常。** 評価回収率を見る |
| `snapshot.ok` が false | プロキシに届いていない | 鍵・URL・さくらの状態を確認。**推薦自体は動いている** |
| `gate_detail` の `rules` だけ false | 規則が出ていない | **正常な退避。** 触らない |
| `phase.current` が下がった | 退避が起きた | ログの退避理由を見る。**パラメータは触らない** |
