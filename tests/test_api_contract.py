"""契約の遵守 (docs/specs/07-testing.md §2 C-1..C-7)。

サーバー側の検証は寛容で、壊しても気づかない。型で守れない部分をここで固定する。
"""

from __future__ import annotations

# client フィクスチャは conftest.py（明示 Settings。開発者の .env に依存しない）


def _payload(**over):
    base = {
        "user_id": "u1",
        "cell_count": 4,
        "candidate_booths": [
            {"booth_id": "b1", "category_id": "cat_a", "visitor_count": 3},
            {"booth_id": "b2", "category_id": "cat_b", "visitor_count": 40},
            {"booth_id": "b3", "category_id": "cat_c", "visitor_count": 12},
        ],
        "pre_survey": {"interest_categories": ["cat_a"], "top_interest_category": "cat_a"},
        "visited_booths": [],
    }
    base.update(over)
    return base


def test_c1_response_key_is_snake_case(client):
    r = client.post("/recommend/cells", json=_payload())
    assert r.status_code == 200
    body = r.json()
    assert "decision_table_size" in body
    assert "decisionTableSize" not in body


def test_c2_scores_count_matches_candidates(client):
    r = client.post("/recommend/cells", json=_payload())
    body = r.json()
    assert len(body["scores"]) == 3


def test_c3_interest_match_is_one_of_four(client):
    r = client.post("/recommend/cells", json=_payload())
    allowed = {"MATCH", "PARTIAL", "MISMATCH", "UNKNOWN"}
    for s in r.json()["scores"]:
        assert s["interest_match"] in allowed


def test_c3_no_unknown_when_determinable(client):
    r = client.post("/recommend/cells", json=_payload())
    kinds = {s["interest_match"] for s in r.json()["scores"]}
    assert "UNKNOWN" not in kinds  # アンケート回答済みなので判定可能


def test_c4_assigned_within_cell_count(client):
    r = client.post("/recommend/cells", json=_payload(cell_count=2))
    assert len(r.json()["assigned"]) <= 2


def test_c5_returned_booth_ids_are_candidates(client):
    body = client.post("/recommend/cells", json=_payload()).json()
    cand = {"b1", "b2", "b3"}
    assert {s["booth_id"] for s in body["scores"]} <= cand
    assert {a["booth_id"] for a in body["assigned"]} <= cand


def test_c6_score_in_unit_interval(client):
    for s in client.post("/recommend/cells", json=_payload()).json()["scores"]:
        assert 0.0 <= s["score"] <= 1.0


def test_c7_openapi_exposes_recommend_endpoint(client):
    schema = client.get("/openapi.json").json()
    assert "/recommend/cells" in schema["paths"]


def test_attributes_are_self_describing(client):
    """docs/specs/01-io-contract.md §3.3 — v / strategy / enabled / raw を刻む。"""
    body = client.post("/recommend/cells", json=_payload()).json()
    a = body["scores"][0]["attributes"]
    assert a["v"] == 1
    assert a["strategy"] == "COVERAGE"
    assert a["enabled"] == ["preference_match", "rating_affinity"]
    assert "raw" in a and "visitor_count" in a["raw"]
    assert "condition" in a


def test_health_has_no_dependency(client):
    assert client.get("/health").status_code == 200


def test_ready_reports_not_warmed(client):
    r = client.get("/ready")
    assert r.status_code == 503  # 段3 未結線なので規則キャッシュは空
    assert r.json()["ready"] is False


def test_ops_state_reports_phase_and_gate(client):
    body = client.get("/ops/state").json()
    assert body["phase"]["current"] == "COVERAGE"
    assert body["phase"]["quality_gate_passed"] is False
    assert set(body["phase"]["gate_detail"]) == {"size", "rules", "gamma", "coverage"}
