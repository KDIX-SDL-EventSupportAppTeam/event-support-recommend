"""性能 (docs/specs/07-testing.md §9)。

実装するのは R-1・R-2 のみ。R-3（予算超過で COVERAGE へ退避）と
R-4（再生成中も前回規則で応答）は段3が未結線で退避先も再生成も存在しないため
実装しない（仕様に「未実装」と分かる形で残す）。

- **R-2 が最重要。** 時間ではなく「呼ばれていないこと」を固定するので
  マシン性能に左右されない。段3を結線する人が誤ってリクエスト経路に
  規則生成・スナップショット取得を持ち込んだら、ここで落ちる。
- R-1 は測定時間依存で壊れやすい。予算 600ms をそのまま閾値にせず、
  桁違いの異常だけを捕まえる緩い上限にする（下記コメント参照）。
"""

from __future__ import annotations

import time

import pytest

import event_support_recommend.drsa as drsa_pkg
import event_support_recommend.drsa.rules as drsa_rules
from event_support_recommend.api.schemas import RecommendRequest
from event_support_recommend.cache import RuleCache
from event_support_recommend.data import repository as repo_mod
from event_support_recommend.drsa.rules import CONCLUSION_DOWN, CONCLUSION_UP, Condition, Rule, RuleSet


def _req(n_candidates: int = 100, uid: str = "perf-user") -> RecommendRequest:
    return RecommendRequest.model_validate(
        {
            "user_id": uid,
            "cell_count": 6,
            "candidate_booths": [
                {
                    "booth_id": f"b{i}",
                    "category_id": f"cat_{i % 8}",
                    "visitor_count": (i * 13) % 97,
                }
                for i in range(n_candidates)
            ],
            "visited_booths": [
                {"booth_id": f"b{i}", "order": i, "source": "FREE_VISIT",
                 "rating": (i % 4) + 1, "rating_scale": 4}
                for i in range(6)
            ],
            "pre_survey": {"interest_categories": ["cat_1", "cat_3"], "top_interest_category": "cat_1"},
        }
    )


def _ruleset_with(n_rules: int) -> RuleSet:
    """段3未結線なので RuleCache に直接積む規則集合を作る (07-testing.md §9 R-1)。

    内容は問わない（現状 engine は本数と decision_table_size しか見ない）。
    """
    rules: list[Rule] = []
    for i in range(n_rules):
        concl = CONCLUSION_UP if i % 2 == 0 else CONCLUSION_DOWN
        op = ">=" if concl == CONCLUSION_UP else "<="
        attr = "preference_match" if i % 3 else "rating_affinity"
        cond = Condition(attr, op, i % 4)
        rules.append(
            Rule(
                conclusion=concl,
                conditions=(cond,),
                support=5 + (i % 20),
                confidence=0.7 + (i % 3) * 0.1,
                id=f"R:{i:08x}",
                coverage=frozenset(),
            )
        )
    return RuleSet(rules=tuple(rules), consistency_level=0.8, min_support=5)


def _cache_with_rules(n_rules: int = 100) -> RuleCache:
    rc = RuleCache()
    rc.put(_ruleset_with(n_rules), decision_table_size=200, gamma=0.9)
    return rc


# --------------------------------------------------------------------------
# R-2（最重要）— リクエスト処理中に規則生成・スナップショット取得が走らない
# --------------------------------------------------------------------------


def test_r2_request_path_does_not_generate_rules_or_fetch_snapshot(run, monkeypatch):
    calls: list[str] = []

    def _spy_generate_rules(*a, **k):
        calls.append("generate_rules")
        raise AssertionError("generate_rules called on the request path (R-2 違反)")

    def _spy_fetch(self, *a, **k):
        calls.append("snapshot_fetch")
        raise AssertionError("snapshot fetch called on the request path (R-2 違反)")

    # 規則生成: 実体と drsa パッケージ再エクスポートの両方を差し替える
    #（段3の実装者が from ..drsa import generate_rules と書いても捕まえる）。
    monkeypatch.setattr(drsa_rules, "generate_rules", _spy_generate_rules)
    monkeypatch.setattr(drsa_pkg, "generate_rules", _spy_generate_rules, raising=False)
    # スナップショット取得（ADR 0002 未決のあいだの既定実装）。
    monkeypatch.setattr(repo_mod.UnavailableRepository, "fetch", _spy_fetch)

    # 規則キャッシュへの書き込みもリクエスト経路では起きてはならない（読むだけ）。
    def _spy_put(self, *a, **k):
        calls.append("rule_cache_put")
        raise AssertionError("RuleCache.put called on the request path (R-2 違反)")

    monkeypatch.setattr(RuleCache, "put", _spy_put)

    rc = RuleCache()
    rc._ruleset = _ruleset_with(100)  # put を潰したので直接セット（テスト都合）
    rc._decision_table_size = 200
    rc._gamma = 0.9

    resp = run(_req(100), rule_cache=rc)

    assert calls == []
    assert len(resp.scores) == 100  # 契約 C-2 も一応確認


def test_r2_also_holds_through_http_route(client, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("regeneration/fetch on request path (R-2 違反)")

    monkeypatch.setattr(drsa_rules, "generate_rules", _boom)
    monkeypatch.setattr(drsa_pkg, "generate_rules", _boom, raising=False)
    monkeypatch.setattr(repo_mod.UnavailableRepository, "fetch", _boom)

    # lifespan が張った rule_cache に規則を積んでからリクエストする。
    client.app.state.rule_cache.put(_ruleset_with(100), decision_table_size=200, gamma=0.9)

    r = client.post(
        "/recommend/cells",
        json={
            "user_id": "u-http",
            "cell_count": 6,
            "candidate_booths": [
                {"booth_id": f"b{i}", "category_id": f"cat_{i % 8}", "visitor_count": i}
                for i in range(100)
            ],
            "pre_survey": {"interest_categories": ["cat_1"], "top_interest_category": "cat_1"},
        },
    )
    assert r.status_code == 200
    assert len(r.json()["scores"]) == 100


# --------------------------------------------------------------------------
# R-1 — 候補100件・規則100本で応答が予算内に収まる
# --------------------------------------------------------------------------

# 予算は RESPONSE_BUDGET_MS = 600ms（08-architecture §4）。ただしそれを
# そのまま CI の閾値にすると、共有ランナーの負荷で容易に赤くなり、
# 「落ちるテストは無視される」最悪の状態を招く（07-testing.md B-5）。
# ここでは予算超過そのものではなく「桁違いに遅い」= アルゴリズムの
# 事故（同期 DB 呼び出しの混入・候補数に対する二乗計算など）だけを捕まえる。
# サーバー側タイムアウト 1000ms の 5 倍を上限に置く。予算の厳密な担保は
# 実行時ガード（R-3。段3結線後に実装）の役目であって単体テストではない。
_ABSURDLY_SLOW_SEC = 5.0


def test_r1_response_within_sane_upper_bound(run):
    rc = _cache_with_rules(100)
    req = _req(100)

    run(req, rule_cache=rc)  # ウォームアップ（初回の import / JIT 的コストを除く）

    best = min(_timed(lambda: run(req, rule_cache=rc)) for _ in range(5))
    assert best < _ABSURDLY_SLOW_SEC, f"候補100件の推薦に {best:.3f}s（明らかに異常）"


def _timed(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


@pytest.mark.skip(reason="R-3: 段3未結線。予算超過時の COVERAGE 退避先が存在しない (07-testing.md §9)")
def test_r3_budget_overrun_falls_back_to_coverage():
    ...


@pytest.mark.skip(reason="R-4: 段3未結線。規則の定期再生成が存在しない (07-testing.md §9)")
def test_r4_serves_previous_rules_during_regeneration():
    ...
