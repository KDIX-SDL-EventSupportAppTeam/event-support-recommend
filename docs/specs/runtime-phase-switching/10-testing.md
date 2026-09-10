---
状態: 確定
最終更新: 2026-09-05
---

# テスト項目

**方針は [07-testing.md](../07-testing.md) が正本。** ここには段3・段4 で追加する項目だけを書く。
チェックボックスを埋めながら実装する。

## 1. データ取得（段3）

- [x] T-1 `READONLY_PROXY_URL` が空のとき、**取得を試みずに** `EventSnapshot.unavailable()` を返す → tests/test_snapshot_source.py::test_t1_no_url_returns_unavailable_without_calling, ::test_t1_none_client_returns_unavailable
- [x] T-2 プロキシが 500 を返しても**例外を投げず**、前回のキャッシュが保持される → tests/test_snapshot_source.py::test_t2_t3_proxy_failure_does_not_raise, tests/test_decision_table_build.py::test_unavailable_snapshot_keeps_previous_cache
- [x] T-3 プロキシがタイムアウトしても同上 → tests/test_snapshot_source.py::test_t2_t3_proxy_failure_does_not_raise, ::test_t3_timeout_is_wrapped_not_raised
- [x] T-4 応答に `rows` が無い／JSON でない場合も同上 → tests/test_snapshot_source.py::test_t4_broken_response_raises_proxyerror, ::test_t4_repository_absorbs_broken_response
- [x] T-5 例外メッセージに **SQL 本文と鍵が含まれない**（テーブル名だけが載る） → tests/test_snapshot_source.py::test_t5_error_message_has_no_sql_or_key
- [x] T-6 組み立てた SQL が `SELECT` で始まらなければ**送信前に**拒否される → tests/test_snapshot_source.py::test_t6_non_select_rejected_before_send
- [x] T-7 `LIVE_TABLES` に無いテーブル名を要求すると拒否される → tests/test_snapshot_source.py::test_t7_unknown_table_rejected
- [x] T-8 `users` の要求列に `email` / `password_hash` が**構造的に入りえない** → tests/test_snapshot_source.py::test_t8_users_columns_cannot_include_secrets
- [x] T-9 バックグラウンドタスクは**起動直後に1回**走り、以後 `SNAPSHOT_TTL_SEC` ごとに回る → tests/test_snapshot_source.py::test_t9_refresher_runs_once_immediately, ::test_t9_run_once_invokes_build_callback, ::test_t9_build_failure_keeps_previous_cache
- [x] T-10 取得中でも `POST /recommend/cells` が `COVERAGE` で 200 を返す → tests/test_snapshot_source.py::test_t10_recommend_stays_200_without_proxy, ::test_t10_recommend_ok_while_refresher_fails

## 2. 決定表の組み立て

- [x] T-11 **評価が無い訪問が行にならない**（未評価を評価0として扱わない） → tests/test_decision_table_build.py::test_t11_unrated_visit_is_not_a_row
- [x] T-12 `decision_table_size` が「評価済みチェックイン件数」と一致する → tests/test_decision_table_build.py::test_t12_size_matches_rated_checkins
- [x] T-13 `role <> 'participant'` の行が除外される → tests/test_decision_table_build.py::test_t13_non_participant_excluded
- [x] T-14 `is_active = 0` のブースの行が除外される → tests/test_decision_table_build.py::test_t14_inactive_booth_excluded
- [x] T-15 **他イベントの行が1件も混ざらない**（2イベント分を入れて確認する） → tests/test_decision_table_build.py::test_t15_other_event_rows_do_not_leak
- [x] T-16 同一参加者×同一ブースの重複が畳まれる → tests/test_decision_table_build.py::test_t16_duplicate_pair_folded
- [x] T-17 条件属性が `ENABLED_ATTRIBUTES` の2個だけで構成される → tests/test_decision_table_build.py::test_t17_condition_attributes_are_exactly_enabled_two
- [x] T-18 決定表 → `generate_rules` が既存の `drsa/` テストと矛盾しない結果を返す → tests/test_decision_table_build.py::test_t18_generate_rules_is_order_independent

## 3. フェーズ切り替え（段4）— **本仕様の核心**

- [x] T-19 決定表 0 件 → `phase = COVERAGE` → tests/test_phase_switching.py::test_t19_zero_table_is_coverage, tests/test_phases.py::test_boundary_values
- [x] T-20 決定表 `PHASE_SIMILARITY_MIN - 1` 件 → `COVERAGE` → tests/test_phase_switching.py::test_t20_below_similarity_min_is_coverage, tests/test_phases.py::test_boundary_values
- [x] T-21 決定表 `PHASE_SIMILARITY_MIN` 件ちょうど → `SIMILARITY` → tests/test_phase_switching.py::test_t21_exactly_similarity_min_is_similarity, tests/test_phases.py::test_boundary_values
- [x] T-22 決定表 `PHASE_DRSA_MIN` 件以上 かつ 品質ゲート通過 → `DRSA` → tests/test_phase_switching.py::test_t22_drsa_min_with_gate_passed_is_drsa
- [x] T-23 決定表 `PHASE_DRSA_MIN` 件以上 だが ゲート不通過 → `SIMILARITY` → tests/test_phase_switching.py::test_t23_drsa_min_but_gate_failed_is_similarity
- [x] T-24 **件数が増えるにつれて `COVERAGE → SIMILARITY → DRSA` と実際に切り替わる**（1つのテストで決定表を育てながら連続して確認する。**これが「当日切り替わる」ことの証明**） → tests/test_phase_switching.py::test_t24_grows_through_all_three_phases
- [x] T-25 決定表が縮んだ場合（起きないはずだが）にも例外を投げない → tests/test_phase_switching.py::test_t25_shrinking_table_does_not_raise

## 4. 退避

- [x] T-26 規則キャッシュが空 → 判定 DRSA でも `phase = SIMILARITY` → tests/test_phase_switching.py::test_t26_drsa_pinned_falls_to_similarity_when_no_rules
- [x] T-27 スナップショット未構築 → 判定 SIMILARITY でも `phase = COVERAGE` → tests/test_phase_switching.py::test_t27_similarity_pinned_falls_to_coverage_without_snapshot
- [x] T-28 事前アンケート未回答の参加者 → `SIMILARITY` を要求されても `COVERAGE` へ退避 → tests/test_phase_switching.py::test_t28_unanswered_participant_falls_to_coverage
- [x] T-29 近傍が1人も作れない → `COVERAGE` へ退避 → tests/test_phase_switching.py::test_t29_no_neighbours_falls_to_coverage
- [x] T-30 戦略の実行中に例外 → 落ちずに1段下へ退避し、200 を返す → tests/test_phase_switching.py::test_t30_strategy_exception_falls_back
- [x] T-31 実行が `RESPONSE_BUDGET_MS` を超える → 打ち切って `COVERAGE`、200 を返す → tests/test_phase_switching.py::test_t31_budget_exceeded_falls_to_coverage, ::test_t31_recommend_returns_200_even_with_zero_budget
- [x] T-32 **退避したとき `phase` に実際の戦略が入る**（判定結果が入っていないこと） → tests/test_phase_switching.py::test_t32_phase_reflects_actual_not_judged
- [x] T-33 退避の理由が JSONL ログに出る → tests/test_phase_switching.py::test_t33_fallback_reason_in_log

## 5. 戦略の中身

- [x] T-34 `SIMILARITY`: 近傍1人が高評価しただけのブースが最上位に来ない（**ベイズ縮約の確認**） → tests/test_similarity_strategy.py::test_t34_single_high_rating_does_not_top
- [x] T-35 `SIMILARITY`: `gender` が近傍の距離に影響しない → tests/test_similarity_strategy.py::test_t35_gender_does_not_affect_neighbours
- [x] T-36 `SIMILARITY`: `visitor_count` を変えてもスコア順位が変わらない（**人気度が混ざらない**） → tests/test_similarity_strategy.py::test_t36_visitor_count_does_not_change_ranking
- [x] T-37 `DRSA`: 適合規則0本の候補が `score = 0.5` 付近に落ち、順序が付く → tests/test_drsa_strategy.py::test_t37_unmatched_candidates_near_half_and_ordered
- [x] T-38 `DRSA`: `reason.rules` に規則本体が入らない（id と要約のみ） → tests/test_drsa_strategy.py::test_t38_reason_rules_are_summaries_only
- [x] T-39 全戦略: **全候補にスコアが付く**（S-1） → tests/test_similarity_strategy.py::test_t39_every_candidate_scored, tests/test_drsa_strategy.py::test_t39_every_candidate_scored, tests/test_api_contract.py::test_c2_scores_count_matches_candidates
- [x] T-40 全戦略: `interest_match = MISMATCH` の候補がスコア0にならない（P-5。セレンディピティが観測不能になる） → tests/test_similarity_strategy.py::test_t40_mismatch_is_not_zero, tests/test_drsa_strategy.py::test_t40_mismatch_not_zero_even_with_down_rule, tests/test_popularity_guard.py::test_p5_mismatch_interest_term_is_not_zero

## 6. 「起きてはいけないこと」（07-testing.md の不変条件を段4でも確認する）

- [x] T-41 **推薦結果が人気度ランキングに退化しない。** `visitor_count` と最終順位の相関を測り、COVERAGE / SIMILARITY / DRSA の**全フェーズで**しきい値以内であること → tests/test_phase_switching.py::test_t41_no_popularity_regression_in_similarity_and_drsa, tests/test_popularity_guard.py::test_p1_p2_score_visitor_correlation_is_negative
- [ ] T-42 どんな入力でも 500 を返さない（壊れた JSON・巨大な候補数・空の候補） → 部分担保（§9参照）: tests/test_abnormal_inputs.py::test_always_200_and_scores_match_candidates, ::test_broken_json_body_still_200（「巨大な候補数」のパラメータが無い）
- [x] T-43 同一入力で同一出力（乱数を使う箇所はシードが固定されている） → tests/test_reproducibility.py::test_e1_same_input_same_output, tests/test_random_strategy.py::test_e1_reproducible

## 7. 観測

- [x] T-44 `/ops/state` の `phase.current` が、実際に返した `phase` と一致する → tests/test_observability.py::test_t44_ops_state_current_matches_returned_phase
- [x] T-45 `/ops/state` の `notes` に「未結線」の記述が残っていない → tests/test_observability.py::test_t45_no_not_wired_notes
- [x] T-46 `/ops/rebuild` が実際に1周させ、`decision_table_size` が更新される → tests/test_observability.py::test_t46_ops_rebuild_updates_size, ::test_t46_ops_rebuild_noop_when_unavailable
- [x] T-47 `/ready` がスナップショットの状態を正しく表す（**プローブには使わない**ことをコメントで残す） → tests/test_observability.py::test_t47_ready_is_not_a_probe
- [x] T-48 フェーズが変わった回のログに `phase_changed` が出る → tests/test_observability.py::test_t48_phase_changed_emitted_on_transition, ::test_t48_no_phase_changed_when_stable

## 8. 統合（実データに近い形で）

- [ ] T-49 合成シナリオを時系列に流し、**フェーズが3段階とも実際に発火する**ことを確認する
      （`tools/build_report.py` の拡張でよい） → 未実施（§9参照）
- [ ] T-50 さくらプロキシのモック（1リクエスト1SQL・エラーは500に潰れる）に対して段3が動く → 未実施（#15待ち。§9参照）
- [x] T-51 本番相当の決定表サイズ（数百行）で、1周の所要時間が `SNAPSHOT_TTL_SEC` を超えない → tests/test_decision_table_build.py::test_t51_full_cycle_well_within_ttl

## 事前検証（本番前に必ず1回）

- [ ] V-1 **デプロイ後、`/ops/state` が `snapshot.ok = true` を返す**（鍵と URL が通っている） → 未実施（#15待ち。§9参照）
- [ ] V-2 リハーサルイベントで評価を30件入れ、**`SIMILARITY` に上がることを目視で確認する** → 未実施（#15待ち。§9参照）
- [ ] V-3 その状態で解放を起こし、`card_unlock_events.phase` に `SIMILARITY` が記録される → 未実施（#15待ち。§9参照）
- [ ] V-4 プロキシを止めても解放が成功し、`FALLBACK_COVERAGE` になる → 未実施（#15待ち。§9参照）

## 9. 担保状況の一覧（2026-09-05）

recommend #29 の対応表確定作業（テスト本文を実際に読んで確認）の記録。根拠は「テスト関数のアサーションが当該 T 項目の文言を満たすか」を1行で書く。

### 9-1. 確認した根拠（T番号順。チェックの有無は上の§1〜§8の各行を正とする）

| T | 担保先 | 確認した根拠 |
|---|---|---|
| T-1 | test_snapshot_source.py::test_t1_* | `client.calls == []` で「取得を試みない」を直接検証、`ProxySnapshotRepository(None, ...)` でも `built is False` |
| T-2 | test_snapshot_source.py::test_t2_t3_proxy_failure_does_not_raise ＋ test_decision_table_build.py::test_unavailable_snapshot_keeps_previous_cache | 前者は 500 相当（`ProxyError`）で例外が外に出ないことを、後者は `rc.decision_table_size == 42`（変わらない）でキャッシュ保持を検証 |
| T-3 | test_snapshot_source.py::test_t2_t3_proxy_failure_does_not_raise（timeout パラメータ）, ::test_t3_timeout_is_wrapped_not_raised | `TimeoutError` を `urlopen` に発生させても `ProxyError` に包まれ外へ漏れないことを検証 |
| T-4 | test_snapshot_source.py::test_t4_* | 壊れた JSON（4パターン）・`rows` 欠落で `ProxyError`。リポジトリ層では `built is False` |
| T-5 | test_snapshot_source.py::test_t5_error_message_has_no_sql_or_key（新規追加） | `urlopen` に `HTTPError(500)` を発生させ、例外メッセージに `"booths"` は含むが `SELECT`・鍵・URL を含まないことを直接アサート |
| T-6 | test_snapshot_source.py::test_t6_non_select_rejected_before_send | `DROP TABLE` 等3パターンで `urlopen` が一度も呼ばれない（`called is False`）ことを確認 |
| T-7 | test_snapshot_source.py::test_t7_unknown_table_rejected | 未知テーブル名・SQL インジェクション風の文字列いずれも `build_select` が `ProxyError` |
| T-8 | test_snapshot_source.py::test_t8_users_columns_cannot_include_secrets | `LIVE_TABLES["users"] == {"id","role"}` と `build_select` の SQL 本文に `email`/`password_hash` が無いことを両方確認 |
| T-9 | test_snapshot_source.py::test_t9_* | 起動直後1回（`repo.n == 1`）、`run_once` が callback を1回呼ぶ、build 失敗時も前回キャッシュ維持（ログの `ok:false` のみ） |
| T-10 | test_snapshot_source.py::test_t10_* | プロキシ未設定・リフレッシャ失敗のいずれでも `/recommend/cells` が 200・`phase=="COVERAGE"` |
| T-11〜T-18 | test_decision_table_build.py::test_t11_〜test_t18_ | 各テストが件名どおりの1条件（未評価除外・件数一致・role 除外・非アクティブ除外・イベント越境無し・重複畳み込み・条件属性2個・生成順序不変）を直接アサート |
| T-19〜T-21 | test_phase_switching.py::test_t19_〜t21_ ＋ test_phases.py::test_boundary_values | `decide_phase` の境界値（29/30/59/60/0/-5/None/NaN）をパラメータ化して直接確認 |
| T-22・T-23 | test_phase_switching.py::test_t22_・test_t23_ | `gamma` の合否だけを変えて `DRSA`/`SIMILARITY` が切り替わることを確認 |
| T-24 | test_phase_switching.py::test_t24_grows_through_all_three_phases | `decision_table_size` を 0→10→30→45→60→90 と育てながら `COVERAGE→SIMILARITY→DRSA` の単調増加を確認（M-1 で破壊されることも実測。§9-3） |
| T-25 | test_phase_switching.py::test_t25_shrinking_table_does_not_raise | 90→5 に縮めても例外なく `DRSA→COVERAGE` |
| T-26〜T-29 | test_phase_switching.py::test_t26_〜t29_ | 規則キャッシュ空・スナップショット無し・未回答・近傍0人の4条件それぞれで退避先フェーズを直接確認 |
| T-30〜T-32 | test_phase_switching.py::test_t30_・test_t31_（2本）・test_t32_ | 戦略例外時・予算超過時に `COVERAGE` へ退避し 200 を返すこと、`phase` が判定結果でなく実際の戦略であることを確認 |
| T-33 | test_phase_switching.py::test_t33_fallback_reason_in_log | JSONL の `judged_phase`/`phase`/`fell_back`/`fallback_reason` を直接パースして確認 |
| T-34〜T-36 | test_similarity_strategy.py::test_t34_〜t36_ | ベイズ縮約で1人だけの絶賛が勝たないこと、`gender` を含む/含まないでスコアが同一なこと、`visitor_count` を変えても順位・スコアが不変なことを確認（M-3 で破壊されることも実測） |
| T-37・T-38 | test_drsa_strategy.py::test_t37_・test_t38_ | 適合規則0本が 0.4〜0.6 に収まり関心度で順序が付くこと、`reason.rules` が `{id,class,support,confidence}` のみで規則本体を含まないことを確認 |
| T-39 | similarity/drsa 各 test_t39_every_candidate_scored ＋ test_api_contract.py::test_c2_scores_count_matches_candidates | 各戦略で候補全件にスコアが付くこと、API 契約でも件数一致を確認 |
| T-40 | similarity/drsa の test_t40_ ＋ test_popularity_guard.py::test_p5_ | MISMATCH でもスコアが 0 にならないことを3経路で確認 |
| T-41 | test_phase_switching.py::test_t41_no_popularity_regression_in_similarity_and_drsa ＋ test_popularity_guard.py::test_p1_p2_ | SIMILARITY/DRSA で `spearman(vc, score) <= 0.2`、COVERAGE で `rho < -0.3`（負）を実測（M-3 で破壊されることも実測） |
| T-42 | test_abnormal_inputs.py::test_always_200_・test_broken_json_body_ | 空 payload・型崩れ・壊れた JSON では 200 を確認したが、**「巨大な候補数」のパラメータが無い** → 部分担保 |
| T-43 | test_reproducibility.py::test_e1_・test_random_strategy.py::test_e1_ | 同一入力を2回実行し scores/assigned が完全一致することを直接比較 |
| T-44〜T-48 | test_observability.py::test_t44_〜t48_ | `/ops/state` の一致・「未結線」文言の不在・`/ops/rebuild` の実測・`/ready` の 503+`probe`表記・`phase_changed` ログの発火/非発火を直接確認 |
| T-49 | （該当なし） | `tools/build_report.py` の拡張は今回スコープ外（§3-3 の設計判断どおり） |
| T-50 | （該当なし） | プロキシのモックは #15 待ち。実環境検証 V-1/V-4 に委ねる（設計判断どおり） |
| T-51 | test_decision_table_build.py::test_t51_full_cycle_well_within_ttl | 60参加者×300件超の決定表で1周が30秒未満・`sc.ready`成立を実測 |
| V-1〜V-4 | （該当なし） | 自動テストでは担保不可。#15（Cloud Run 事前検証。OPEN・ブロック中）に依存するため今回は未実施 |

### 9-2. 未実施・部分担保の一覧

| # | 状態 | 方針 |
|---|---|---|
| T-42 | 部分担保 | 「巨大な候補数」のテストパラメータが無い。500 を返さない主経路（壊れたJSON・空の候補・型崩れ）は既存2本で担保済み。不足パターンの追加は別 issue 候補とする |
| T-49 | 未実施（今年は見送り） | `tools/build_report.py` の拡張は実装作業でありスコープ外。3段階発火は T-24（単体）＋V-2（実環境リハーサル）で実質担保する設計判断による |
| T-50 | 未実施（#15待ち） | プロキシのモックより実プロキシでの `snapshot.ok=true` 確認（V-1/V-4）の方が証拠として強いという設計判断。#15（recommend #15「Cloud Run 事前検証」。OPEN・ブロック中）が解消し次第、実環境検証で代替する |
| V-1〜V-4 | 未実施（#15待ち） | #15 がブロック中のため当issue の作業では実施できない。実施可否は須藤先生の返事（プロキシ設置）次第。V-1が未達のままだと当日 `COVERAGE` 固定に留まり研究デザインが1本に縮む影響がある（A-1） |

### 9-3. V-3 変異テストの実施記録（2026-09-05実測）

各変異は `git diff <file> > /tmp/mN.diff` で保存し、`pytest -q` で失敗を確認したのち `git apply -R /tmp/mN.diff` で復元した（`git stash` / `git checkout --` は不使用）。復元後 `git status --short` で対象ファイルの差分が消えていることを確認済み。

| # | 対象 T | 変異内容 | 実行コマンド | 結果（実測） |
|---|---|---|---|---|
| M-1 | T-24 | `src/event_support_recommend/phases.py:74` `decide_phase(` の本体先頭に `return Phase.COVERAGE` を追加 | `pytest -q tests/test_phase_switching.py::test_t24_grows_through_all_three_phases` | `FAILED tests/test_phase_switching.py::test_t24_grows_through_all_three_phases`（1 failed）。追加確認で T-21〜T-23 も連動して落ちること（`-k "t21 or t22 or t23"` で 3 failed）を実測 |
| M-2 | T-8 | `src/event_support_recommend/data/live_tables.py:30` `"users": ("id", "role")` を `("id", "role", "email")` に変更 | `pytest -q tests/test_snapshot_source.py::test_t8_users_columns_cannot_include_secrets` | `FAILED tests/test_snapshot_source.py::test_t8_users_columns_cannot_include_secrets`（1 failed） |
| M-3 | T-41 | `src/event_support_recommend/strategies/similarity.py:149` のスコア計算末尾に `+ 0.01 * cand.visitor_count` を追加 | `pytest -q tests/test_phase_switching.py::test_t41_no_popularity_regression_in_similarity_and_drsa tests/test_similarity_strategy.py::test_t36_visitor_count_does_not_change_ranking` | 両方 `FAILED`（2 failed）。T-41（全フェーズでの人気度相関）・T-36（visitor_count 不変性）がともに壊れることを実測 |

3項目とも「落ちるべきテストが実際に落ちる」ことを確認したため、対応表（§1〜§8）の該当行を「担保」のまま維持してよいと判断した。
