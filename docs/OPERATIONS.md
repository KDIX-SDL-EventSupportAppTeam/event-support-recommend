---
状態: 確定
最終更新: 2026-09-05
---

# 当日の運用手引き — 気を付けること

**イベント当日（2026-10-16 金）にこのサービスを預かる人が読む1枚。**
仕様の根拠は [specs/11-deployment.md](specs/11-deployment.md)・[specs/10-observability.md](specs/10-observability.md)・
[ADR 0009](decisions/adrs/0009-当日の切り替えは既定値のまま走らせ調整は事後に行う.md) にある。
ここは**行動だけ**を書く。

---

## 0. 大前提 — このサービスは壊れてもアプリを止めない

推薦が落ちても遅くても、サーバー側がフォールバックして解放は完遂する
（[01-io-contract.md](specs/01-io-contract.md) §1）。**慌てて触るほうが危険である。**

> **壊しうるのは推薦の質と研究データの質だけ。**
> だから当日の判断基準は「アプリが動くか」ではなく「**研究データが汚れないか**」に置く。

---

## 1. 当日までに必ず終わらせること

| # | やること | 終わっていないと |
|---|---|---|
| **A-1** | **`READONLY_PROXY_URL` / `READONLY_PROXY_KEY` を Cloud Run に設定する** | **一日中 `COVERAGE` のまま。`SIMILARITY` / `DRSA` は一度も動かない**（下記 §2） |
| **A-0** | **前日までに `main` を凍結する**（下記 §1.1） | **当日 `main` にマージした瞬間に本番が差し替わる** |
| A-2 | サーバー側の `RECOMMENDER_URL` を Cloud Run の URL に設定する | 推薦が一度も呼ばれない |
| A-3 | イベント開始1時間前に `--min-instances=1` へ上げる（コマンドは §8） | コールドスタートで数秒かかり、サーバー側のタイムアウト（1000ms）に届かない |
| A-4 | [11-deployment.md](specs/11-deployment.md) §7 の V-9〜V-13 を通す | 結合と性能が未検証のまま本番に入る |

### A-1 が最重要である理由

段3・段4（スナップショット取得と `SIMILARITY` / `DRSA` の結線）は**実装済み**である。
しかし `READONLY_PROXY_URL` が空だと `build_repository()` が `UnavailableRepository` を返し
（[repository.py](../src/event_support_recommend/data/repository.py) `build_repository`）、
スナップショットの定期取得そのものが起動しない
（[app.py](../src/event_support_recommend/api/app.py) の `snapshot_wired`）。

結果として決定表が育たず、`decision_table_size` は永久に `null`、フェーズ判定は常に `COVERAGE` になる。
**実装は動いているのに設定だけで研究が1本に縮む**という、最も気づきにくい壊れ方をする。

**確認方法**（当日朝、参加者の訪問が始まってから）:

```
GET /ops/state   （X-Ops-Token ヘッダが要る）
→ snapshot.ok が true になっているか
→ snapshot.decision_table_size が数字になっているか（null ならデータが来ていない）
```

`snapshot.ok` が `false` のままなら A-1 が未実施か、プロキシが応答していない。

### 1.1 当日は `main` を凍結する

**`main` への push は Cloud Build トリガー `deploy-event-support-recommend` を発火させ、
そのまま Cloud Run のリビジョンが差し替わる**（`asia-northeast1`・`cloudbuild.yaml`）。

つまり当日、**誰かが良かれと思って PR をマージしただけで本番が入れ替わる。**
[ADR 0009](decisions/adrs/0009-当日の切り替えは既定値のまま走らせ調整は事後に行う.md) の
「当日はリビジョンを差し替えない」は、CD がある以上**運用で守るしかない**。

| いつ | やること |
|---|---|
| 前日まで | チームに **「当日は `main` にマージしない」** を周知する。GitHub の branch protection で `main` を一時的にロックできるならそれが確実 |
| 当日 | `main` へのマージを一切行わない。**障害対応で差し替えた場合は時刻を記録する**（§4） |
| 終了後 | 凍結を解除する |

**`develop` への push・PR は自由でよい。** 発火するのは `main` だけである。

---

## 2. 当日、パラメータは触らない

**[ADR 0009](decisions/adrs/0009-当日の切り替えは既定値のまま走らせ調整は事後に行う.md) の決定である。触らないことが既定。**

- しきい値（`PHASE_*`）・重み（`W_*`）・DRSA の設定（`DRSA_*`）を**当日変更しない**
- 変更すると前半と後半で別のアルゴリズムが走ったことになり、**比較の土台が消える**
- 値の妥当性の検証・調整は、イベント後のデータで行う

**例外は障害対応のみ**（§4 のキルスイッチ）。**行った場合は必ず変更時刻を記録する。**

---

## 3. 見るもの・見てはいけないもの

### 見るもの

| 指標 | どこで | 異常 | やること |
|---|---|---|---|
| **フォールバック率**（直近30分） | ダッシュボード（DB） | 30%超 | **最優先。** ログ確認・再起動 |
| **評価回収率** | ダッシュボード（DB） | 25%未満 | 運営に声かけを依頼（**当日打てる数少ない手**） |
| 現在フェーズ | `/ops/state` | 15時でも `COVERAGE` | 決定表が育っていない。**まず §1 A-1 を疑う** |
| γ・確実規則の本数 | `/ops/state` | γ が低い / 規則0〜1本 | 品質ゲートが正しく止めている。**それでよい** |
| 応答時間 p95 | ダッシュボード | 600ms 超 | タイムアウト（1000ms）が近い |
| **割当ブースの集中度** | ダッシュボード（DB） | 特定ブースに集中 | **人気順への退化。去年の失敗の再来** |

### 見てはいけないもの

> **A/B の効果（DRSA 枠と COVERAGE 枠の訪問率の差）を当日に見ない。**

見ると「DRSA のほうが悪いから設定を変えよう」と判断したくなる。**それをやると実験が壊れる。**
効果は当日の行動に何も影響しないので、見なくても失うものは無い
（[10-observability.md](specs/10-observability.md) §5）。

---

## 4. キルスイッチ（障害時のみ）

上から順に試す。**下へ行くほど失うものが大きい。**

| # | 手段 | 効果 | 失うもの |
|---|---|---|---|
| 1 | `PHASE_DRSA_MIN` を極端に大きくする | DRSA を止めて `SIMILARITY` へ落とす | DRSA のデータ |
| 2 | `PHASE_SIMILARITY_MIN` も上げる | `COVERAGE` のみで運用 | 段3・段4 のデータ |
| 3 | サーバー側の `RECOMMENDER_URL` を空にする | 推薦を完全に切り離す。**最終手段** | 研究データのすべて |

**どれを使っても、変更時刻を記録する。** 前後を別データとして扱わないと分析が壊れる
（[09-research-design.md](specs/09-research-design.md) R-2）。

なお、退避は設計に組み込まれている。規則が出なければ `SIMILARITY` へ、
近傍が作れなければ `COVERAGE` へ**自動で静かに落ちる**（[04-strategies.md](specs/04-strategies.md) §5）。
**「フェーズが下がった」こと自体は障害ではない。**

---

## 5. やってはいけないこと

| # | 禁止 | 破ると |
|---|---|---|
| O-1 | 当日パラメータを変えてリビジョンを差し替える（障害対応を除く） | 研究データの前後が繋がらなくなる（[ADR 0009](decisions/adrs/0009-当日の切り替えは既定値のまま走らせ調整は事後に行う.md)） |
| O-2 | A/B の効果の差を当日見る | 無意識に実験を壊す（[10 §5](specs/10-observability.md)） |
| O-3 | `/ready` の 503 を障害として扱う | 誤った再起動を招く。**スナップショット未取得なら 503 が正常**（[11 X-1](specs/11-deployment.md)） |
| O-4 | サービス URL を公開資料・スライド・リポジトリに載せる | 未認証公開なので、偽リクエストで研究データが汚れる（[11 §5](specs/11-deployment.md)） |
| O-5 | `OPS_TOKEN` をチャット・コミットに貼る | `/ops/*` と `/demo` が誰でも触れる |
| O-6 | イベント後に `--min-instances=1` と当日の env 上書きを戻し忘れる | 課金が続く。上書きが「既定」になり当日の変更点が見分けられなくなる（[11 X-5](specs/11-deployment.md)） |
| O-7 | 当日 `main` へマージする（障害対応を除く） | **CD が発火して本番が差し替わる。** `engine_version` が変わり、研究データの前後が繋がらなくなる（§1.1） |

---

## 6. イベント終了後

1. `--min-instances` を **0 に戻す**（§8 の「終了後」）
2. 当日 env を上書きしていたら**手で消す**（`--update-env-vars` は既存の env を消さない）
3. JSONL ログを Cloud Logging から回収する（下記）
4. `user_id` を DB の参加者一覧と突合し、**参加者以外のリクエストを除外する**
   （未認証公開なので偽リクエストが混じりうる。動作確認で流した `verify-*` も含む）
5. パラメータの妥当性検証を `event-support-analytics` の事後分析へ引き継ぐ（[ADR 0009](decisions/adrs/0009-当日の切り替えは既定値のまま走らせ調整は事後に行う.md)）

ログの回収（`jsonPayload` として構造化済み。V-14 で確認済み）:

```
gcloud logging read 'resource.type="cloud_run_revision"
  AND resource.labels.service_name="event-support-recommend"
  AND jsonPayload.kind="recommend"' --limit=10000 --format=json --project=event-support-app
```

**`engine_version` でリビジョンを分けられる。** 当日設定を変えた場合の前後の切り分けはこれで行う。

---

## 7. 困ったときの参照先

| 症状 | 見る場所 |
|---|---|
| フェーズが上がらない | §1 A-1 → [runtime-phase-switching](specs/runtime-phase-switching/README.md) |
| デプロイし直したい | [11-deployment.md](specs/11-deployment.md) §9 |
| 何を監視するのか詳しく | [10-observability.md](specs/10-observability.md) §4 |
| そもそも何が成功なのか | [PURPOSE.md](PURPOSE.md) |

---

## 8. 当日担当者チェックリスト（2026-10-16 金）

担当者: ＿＿＿＿（副: ＿＿＿＿）。このサービスを当日預かる人。§0〜§5 を前日までに読む。
共通の変数（コマンドで使う。値は repo に既出のもののみ。**鍵・トークンは書かない**）:

    PROJECT_ID=event-support-app  REGION=asia-northeast1  SERVICE=event-support-recommend

| 時刻 | やること | コマンド／確認 | 記録（時刻・結果） |
|---|---|---|---|
| 前日まで | `main` の branch protection が有効（§9） | `gh api repos/KDIX-SDL-EventSupportAppTeam/event-support-recommend/branches/main/protection --jq .url` が 404 でない | |
| 前日まで | チームに「当日 `main` にマージしない」を周知（server / frontend も同じ） | 周知先と時刻 | |
| 前日まで | A-1・A-2 が済んでいる（`/ops/state` の `snapshot.ok`） | `curl -s -H "X-Ops-Token: $OPS_TOKEN" $SERVICE_URL/ops/state \| jq .snapshot.ok`（`OPS_TOKEN`・`SERVICE_URL` はシェル変数で渡し、ここに値を書かない） | |
| 開始 1 時間前 | `--min-instances=1` へ上げる（A-3） | `gcloud run services update $SERVICE --project=$PROJECT_ID --region=$REGION --min-instances=1` | |
| 上げた直後 | 反映確認 | `gcloud run services describe $SERVICE --project=$PROJECT_ID --region=$REGION --format="value(spec.template.metadata.annotations.autoscaling\.knative\.dev/minScale)"` → `1` | |
| 上げて 10 分後 | T-6/T-7: 放置後の応答時間 | `curl -s -o /dev/null -w "%{time_total}\n" -H "Content-Type: application/json" -d @docs/tests/runs/sample-request.json $SERVICE_URL/recommend/cells` → **1.000 未満** | |
| 同時 | T-8: `/ops/state` が従来どおり応答 | 上の `/ops/state` が 200 | |
| 開場後 | §3「見るもの」を 1 時間ごとに確認。`phase` が 15 時でも `COVERAGE` なら **A-1 を疑う。main は触らない** | `/ops/state` の `phase.current` | |
| 終了後 | `--min-instances=0` に戻す（O-6） | `gcloud run services update $SERVICE --project=$PROJECT_ID --region=$REGION --min-instances=0` | |
| 終了後 | 当日 env の上書きがあれば手で消す。凍結解除（§9） | `gcloud run services describe … --format=yaml \| grep -n PHASE_` → 0 行 | |
| 翌日 | 凍結が守られたかを機械判定 | `gh pr list --repo KDIX-SDL-EventSupportAppTeam/event-support-recommend --base main --state merged --search "merged:2026-10-16..2026-10-16" --json number` → `[]` | |

`sample-request.json` は `docs/specs/01-io-contract.md` の正常ボディ例をそのまま置く（候補 5 件・`cell_count` 4。V-6 と同じもの）。**実 `user_id` を使わず `verify-` 接頭辞**にする（§6 の 4 で除外できるように）。

---

## 9. `main` の branch protection（前日までに設定・終了後に解除）

設定できるのは repo の admin だけ。**当日の朝に設定しない**（触る回数を増やさない）。

| 項目 | 値 |
|---|---|
| 対象 | `main` |
| Require a pull request before merging | ON（approvals 1） |
| Lock branch（read-only） | **ON**（当日はこれで PR のマージも止まる。T-3 の答え: 「マージも保護対象」） |
| Do not allow bypassing the above settings | ON |

設定コマンド（GitHub CLI・admin 権限）:

    gh api -X PUT repos/KDIX-SDL-EventSupportAppTeam/event-support-recommend/branches/main/protection \
      --input - <<'JSON'
    {"required_status_checks":null,"enforce_admins":true,
     "required_pull_request_reviews":{"required_approving_review_count":1},
     "restrictions":null,"lock_branch":true,"allow_force_pushes":false,"allow_deletions":false}
    JSON

T-2 の確認（**設定画面を見るだけにしない**）: 一時ブランチから `git push origin HEAD:main --dry-run` を実行し、
`remote: error: GH006: Protected branch update failed` 系の拒否が出ることを `docs/tests/runs/2026-10-16-run-day.md` に貼る。
`--dry-run` でも保護は評価される。万一通ってしまう場合に備え、内容が `main` と同一のコミット（`git rev-parse origin/main`）を push 対象にする。

解除（終了後・T-4）: `gh api -X DELETE repos/KDIX-SDL-EventSupportAppTeam/event-support-recommend/branches/main/protection`。
解除できる人: ＿＿＿＿（admin）。解除時刻を §8 の記録欄に書く。
