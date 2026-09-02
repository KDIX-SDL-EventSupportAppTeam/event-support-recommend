"""観測: /ops/state を真実にし、/ops/rebuild を実装する。

docs/specs/runtime-phase-switching/10-testing.md T-44〜T-48。
"""

from __future__ import annotations

import json

import pytest

from event_support_recommend import engine as engine_mod
from event_support_recommend.api.schemas import RecommendRequest
from event_support_recommend.cache import RuleCache, SnapshotCache
from event_support_recommend.drsa import DecisionTable, generate_rules
from event_support_recommend.drsa.decision_table import DecisionRow
from event_support_recommend.engine import run_recommendation
from event_support_recommend.models import DecisionClass, EventSnapshot
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


@pytest.fixture(autouse=True)
def _clear_phase_tracker():
    engine_mod._last_phase_by_kind.clear()
    yield
    engine_mod._last_phase_by_kind.clear()


# --------------------------------------------------------------------------- #
# T-44: /ops/state の phase.current が実際に返した phase と一致
# --------------------------------------------------------------------------- #
def test_t44_ops_state_current_matches_returned_phase(client):
    # 判定は SIMILARITY（size 40）だが snapshot 無し → 実際は COVERAGE。
    client.app.state.rule_cache.put(_rules(), decision_table_size=40, gamma=1.0)
    r = client.post("/recommend/cells", json=_payload())
    returned = r.json()["phase"]
    state = client.get("/ops/state").json()
    assert state["phase"]["current"] == returned
    assert state["phase"]["judged"] == "SIMILARITY"
    assert returned == "COVERAGE"


# --------------------------------------------------------------------------- #
# T-45: notes に「未結線」が残っていない
# --------------------------------------------------------------------------- #
def test_t45_no_not_wired_notes(client):
    state = client.get("/ops/state").json()
    assert state["notes"] == []
    blob = json.dumps(state)
    assert "not wired" not in blob and "undecided" not in blob


# --------------------------------------------------------------------------- #
# T-46: /ops/rebuild が1周させ decision_table_size が更新される
# --------------------------------------------------------------------------- #
def test_t46_ops_rebuild_updates_size(client):
    class FakeRepo:
        def fetch(self, event_id=None):
            return EventSnapshot(
                built=True,
                decision_table_size=None,
                tables={
                    "users": [{"id": "u1", "role": "participant"}],
                    "booths": [{"id": "bx", "event_id": "A", "category_id": "hi", "is_active": 1}],
                    "categories": [],
                    "check_ins": [{"id": "c1", "user_id": "u1", "booth_id": "bx",
                                   "event_id": "A", "checked_in_at": "2026-09-01T10:00"}],
                    "booth_ratings": [{"checkin_id": "c1", "user_id": "u1", "booth_id": "bx",
                                       "event_id": "A", "rating": 4, "scale": 4}],
                    "user_survey_answers": [],
                },
            )

    client.app.state.snapshot_repo = FakeRepo()
    before = client.get("/ops/state").json()["snapshot"]["decision_table_size"]
    out = client.post("/ops/rebuild").json()
    assert out["rebuilt"] is True
    assert out["decision_table_size"] == 1
    assert out["previous_decision_table_size"] == before
    assert client.get("/ops/state").json()["snapshot"]["decision_table_size"] == 1


def test_t46_ops_rebuild_noop_when_unavailable(client):
    out = client.post("/ops/rebuild").json()  # 既定は UnavailableRepository
    assert out["rebuilt"] is False
    assert out["decision_table_size"] is None


# --------------------------------------------------------------------------- #
# T-47: /ready はプローブに使わない旨のコメントを残す
# --------------------------------------------------------------------------- #
def test_t47_ready_is_not_a_probe(client):
    r = client.get("/ready")
    assert r.status_code == 503  # 規則が冷えている
    body = r.json()
    assert "probe" in body["note"]
    assert "snapshot_ok" in body


# --------------------------------------------------------------------------- #
# T-48: フェーズが変わった回のログに phase_changed が出る
# --------------------------------------------------------------------------- #
def test_t48_phase_changed_emitted_on_transition(capsys):
    s = Settings(_env_file=None, enabled_attributes=list(NAMES), drsa_min_rules=1)
    rc = RuleCache()
    sc = SnapshotCache()
    axes = {"interest_categories": ["hi"], "age_range": "20s", "occupation": "x"}
    surveys = {"u1": dict(axes), **{f"n{i}": dict(axes) for i in range(5)}}
    ratings = {f"n{i}": {"b1": 0.8, "b2": 0.3} for i in range(5)}
    sc.put(decision_table_size=99, surveys=surveys, ratings_by_user=ratings,
           booth_category={}, global_mean=0.5)
    req = RecommendRequest.model_validate(_payload())

    run_recommendation(req, settings=s, rule_cache=rc, snapshot_cache=sc)          # COVERAGE
    rc.put(_rules(), decision_table_size=40, gamma=1.0)
    run_recommendation(req, settings=s, rule_cache=rc, snapshot_cache=sc)          # SIMILARITY
    rc.put(_rules(), decision_table_size=90, gamma=1.0)
    run_recommendation(req, settings=s, rule_cache=rc, snapshot_cache=sc)          # DRSA

    changes = [
        json.loads(l) for l in capsys.readouterr().out.splitlines()
        if '"kind":"phase_changed"' in l
    ]
    assert len(changes) == 2
    assert (changes[0]["from"], changes[0]["to"]) == ("COVERAGE", "SIMILARITY")
    assert (changes[1]["from"], changes[1]["to"]) == ("SIMILARITY", "DRSA")


def test_t48_no_phase_changed_when_stable(capsys):
    s = Settings(_env_file=None, enabled_attributes=list(NAMES))
    rc = RuleCache()
    req = RecommendRequest.model_validate(_payload())
    run_recommendation(req, settings=s, rule_cache=rc)
    run_recommendation(req, settings=s, rule_cache=rc)
    assert '"kind":"phase_changed"' not in capsys.readouterr().out
