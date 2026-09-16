"""issue #34: /ops/state の品質ゲートを推薦エンジンの実判定に一致させる。

`candidate_coverage` の 0.0 ハードコードを外し、未計算時は `null` を返す。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from event_support_recommend import engine as engine_mod
from event_support_recommend.api.app import create_app
from event_support_recommend.api.schemas import RecommendRequest
from event_support_recommend.cache import RuleCache, SnapshotCache
from event_support_recommend.drsa import DecisionTable, generate_rules
from event_support_recommend.drsa.decision_table import DecisionRow
from event_support_recommend.engine import run_recommendation
from event_support_recommend.models import DecisionClass, Phase
from event_support_recommend.settings import Settings

H, L = DecisionClass.HIGH, DecisionClass.LOW
NAMES = ("preference_match", "rating_affinity")
ROWS = [DecisionRow((3, 3), H), DecisionRow((3, 2), H), DecisionRow((2, 2), H),
        DecisionRow((2, 1), L), DecisionRow((1, 1), L), DecisionRow((0, 2), L)]


def _rules():
    return generate_rules(DecisionTable(NAMES, ROWS), min_support=1, consistency_level=0.8)


def _payload():
    return {
        "user_id": "u1", "cell_count": 2,
        "candidate_booths": [
            {"booth_id": "b1", "category_id": "hi", "visitor_count": 3},
            {"booth_id": "b2", "category_id": "out", "visitor_count": 9},
        ],
        "pre_survey": {"interest_categories": ["hi"], "top_interest_category": "hi"},
    }


class _State:
    """app.state の代役。run_recommendation が last_gate を書き込む先。"""


@pytest.fixture(autouse=True)
def _clear_phase_tracker():
    engine_mod._last_phase_by_kind.clear()
    yield
    engine_mod._last_phase_by_kind.clear()


def _drsa_state():
    """size 90・決定表が育った状態で1件推薦し、last_gate を得る。"""
    s = Settings(_env_file=None, enabled_attributes=list(NAMES), drsa_min_rules=1)
    rc = RuleCache()
    sc = SnapshotCache()
    axes = {"interest_categories": ["hi"], "age_range": "20s", "occupation": "x"}
    surveys = {"u1": dict(axes), **{f"n{i}": dict(axes) for i in range(5)}}
    ratings = {f"n{i}": {"b1": 0.8, "b2": 0.3} for i in range(5)}
    sc.put(decision_table_size=90, surveys=surveys, ratings_by_user=ratings,
           booth_category={}, global_mean=0.5)
    rc.put(_rules(), decision_table_size=90, gamma=1.0)
    st = _State()
    resp = run_recommendation(
        RecommendRequest.model_validate(_payload()),
        settings=s, rule_cache=rc, snapshot_cache=sc, app_state=st,
    )
    return st, resp


# --------------------------------------------------------------------------- #
# T-1: 推薦を1件も処理していない → gate_detail が null
# --------------------------------------------------------------------------- #
def test_t1_ops_state_null_before_any_recommendation(client):
    body = client.get("/ops/state").json()
    assert body["phase"]["gate_detail"] is None
    assert body["phase"]["quality_gate_passed"] is None
    assert body["phase"]["judged"] is None
    assert body["rules"]["candidate_coverage"] is None


# --------------------------------------------------------------------------- #
# T-2: 推薦を1件処理したあと、gate_detail がその回の判定と一致
# --------------------------------------------------------------------------- #
def test_t2_ops_state_matches_last_recommendation(client):
    client.app.state.rule_cache.put(_rules(), decision_table_size=40, gamma=1.0)
    client.post("/recommend/cells", json=_payload())
    last_gate = client.app.state.last_gate
    body = client.get("/ops/state").json()
    assert body["phase"]["gate_detail"] == last_gate.gate.detail.as_dict()
    assert body["phase"]["quality_gate_passed"] == last_gate.gate.passed
    assert body["phase"]["judged"] == last_gate.judged_phase.value
    assert body["rules"]["candidate_coverage"] == last_gate.candidate_coverage


# --------------------------------------------------------------------------- #
# T-3: candidate_coverage が 0.0 固定でない
# --------------------------------------------------------------------------- #
def test_t3_candidate_coverage_not_pinned_to_zero():
    st, _ = _drsa_state()
    assert st.last_gate.candidate_coverage > 0.0


# --------------------------------------------------------------------------- #
# T-4: quality_gate_passed が true になりうる（現状は構造的に不可能）
# --------------------------------------------------------------------------- #
def test_t4_quality_gate_can_pass():
    st, _ = _drsa_state()
    assert st.last_gate.gate.passed is True
    assert st.last_gate.gate.detail.coverage is True


# --------------------------------------------------------------------------- #
# T-5: judged が DRSA になりうる
# --------------------------------------------------------------------------- #
def test_t5_judged_can_be_drsa():
    st, resp = _drsa_state()
    assert st.last_gate.judged_phase is Phase.DRSA
    assert resp.phase == "DRSA"


# --------------------------------------------------------------------------- #
# T-6: phase.current は実際に返したフェーズと一致（T-44 の回帰）
# --------------------------------------------------------------------------- #
def test_t6_current_matches_returned_phase(client):
    client.app.state.rule_cache.put(_rules(), decision_table_size=40, gamma=1.0)
    returned = client.post("/recommend/cells", json=_payload()).json()["phase"]
    body = client.get("/ops/state").json()
    assert body["phase"]["current"] == returned


# --------------------------------------------------------------------------- #
# T-7: 0 と null を取り違えない。notes に「未結線」を書かない（T-45 の回帰）
# --------------------------------------------------------------------------- #
def test_t7_zero_is_not_null(client):
    import json as _json

    body = client.get("/ops/state").json()
    # 未計算は必ず null。0.0 で埋めていない
    assert body["rules"]["candidate_coverage"] is None
    assert body["phase"]["quality_gate_passed"] is not False  # null であって false ではない
    assert body["notes"] == []
    blob = _json.dumps(body)
    assert "not wired" not in blob and "undecided" not in blob


# --------------------------------------------------------------------------- #
# デモ・リプレイは last_gate を汚さない（正本は本番経路1つ）
# --------------------------------------------------------------------------- #
def test_demo_kind_does_not_write_last_gate():
    s = Settings(_env_file=None, enabled_attributes=list(NAMES))
    st = _State()
    run_recommendation(
        RecommendRequest.model_validate(_payload()),
        settings=s, rule_cache=RuleCache(), app_state=st, log_kind="recommend_replay",
    )
    assert not hasattr(st, "last_gate")


# --------------------------------------------------------------------------- #
# 続き #1: 鮮度の開示 — judged_at
# --------------------------------------------------------------------------- #
def test_judged_at_null_before_and_iso_utc_after(client):
    assert client.get("/ops/state").json()["phase"]["judged_at"] is None

    client.app.state.rule_cache.put(_rules(), decision_table_size=40, gamma=1.0)
    client.post("/recommend/cells", json=_payload())
    body = client.get("/ops/state").json()

    judged_at = body["phase"]["judged_at"]
    assert isinstance(judged_at, str)
    parsed = datetime.fromisoformat(judged_at)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)
    assert body["phase"]["judged_at"] == client.app.state.last_gate.evaluated_at.isoformat()


# --------------------------------------------------------------------------- #
# 続き #1: gate_stats は推薦時点のまま、rules.* だけがキャッシュ更新で進む
# --------------------------------------------------------------------------- #
def test_gate_stats_frozen_while_rules_move(client):
    client.app.state.rule_cache.put(_rules(), decision_table_size=40, gamma=1.0)
    client.post("/recommend/cells", json=_payload())

    before = client.get("/ops/state").json()
    assert before["phase"]["gate_stats"] == {"size": 40, "gamma": 1.0, "rules": before["rules"]["count_certain_up"]}

    # 推薦せずにキャッシュだけ別の size/gamma で入れ替える
    client.app.state.rule_cache.put(_rules(), decision_table_size=99, gamma=0.5)
    after = client.get("/ops/state").json()

    # gate 側は推薦時点で固定
    assert after["phase"]["gate_stats"]["size"] == 40
    assert after["phase"]["gate_stats"]["gamma"] == 1.0
    assert after["phase"]["judged_at"] == before["phase"]["judged_at"]
    assert after["phase"]["gate_detail"] == before["phase"]["gate_detail"]
    # rules.* は新しいキャッシュを映す → ズレが観測できる
    assert after["rules"]["gamma"] == 0.5
    assert after["snapshot"]["decision_table_size"] == 99


# --------------------------------------------------------------------------- #
# 続き #2: experiment.split_active の null セマンティクス
# --------------------------------------------------------------------------- #
def test_split_active_null_before_recommendation(client):
    assert client.get("/ops/state").json()["experiment"]["split_active"] is None


def test_split_active_false_when_gate_fails(client):
    client.app.state.rule_cache.put(_rules(), decision_table_size=40, gamma=1.0)
    client.post("/recommend/cells", json=_payload())  # size 40 → SIMILARITY, ゲート不通過
    assert client.get("/ops/state").json()["experiment"]["split_active"] is False


def test_split_active_true_when_enabled_and_gate_passes():
    s = Settings(_env_file=None, enabled_attributes=list(NAMES),
                 drsa_min_rules=1, experiment_split_enabled=True)
    with TestClient(create_app(s)) as c:
        c.app.state.rule_cache.put(_rules(), decision_table_size=90, gamma=1.0)
        sc = c.app.state.snapshot_cache
        axes = {"interest_categories": ["hi"], "age_range": "20s", "occupation": "x"}
        sc.put(decision_table_size=90,
               surveys={"u1": dict(axes), **{f"n{i}": dict(axes) for i in range(5)}},
               ratings_by_user={f"n{i}": {"b1": 0.8, "b2": 0.3} for i in range(5)},
               booth_category={}, global_mean=0.5)
        resp = c.post("/recommend/cells", json=_payload()).json()
        assert resp["phase"] == "DRSA"
        assert c.get("/ops/state").json()["experiment"]["split_active"] is True
