"""通常の異常入力 — すべて 200 を返す (docs/specs/07-testing.md §3)。

開場直後はこれらが常態。異常系ではない。500 を返した時点でテスト失敗。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from event_support_recommend.api.app import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


CANDS = [
    {"booth_id": "b1", "category_id": "cat_a", "visitor_count": 3},
    {"booth_id": "b2", "category_id": "cat_b", "visitor_count": 40},
    {"booth_id": "b3", "category_id": "cat_c", "visitor_count": 12},
]


@pytest.mark.parametrize(
    "payload",
    [
        {"user_id": "u", "cell_count": 4, "candidate_booths": CANDS, "pre_survey": None},
        {"user_id": "u", "cell_count": 4, "candidate_booths": CANDS, "pre_survey": {}},
        {"user_id": "u", "cell_count": 4, "candidate_booths": CANDS, "visited_booths": []},
        {
            "user_id": "u",
            "cell_count": 4,
            "candidate_booths": CANDS,
            "visited_booths": [
                {"booth_id": "b1", "source": "FREE_VISIT", "rating": None, "rating_scale": 4}
            ],
        },
        {"user_id": "u", "cell_count": 6, "candidate_booths": CANDS[:1]},
        {"user_id": "u", "cell_count": 4, "candidate_booths": [CANDS[0]]},
        {"user_id": "u", "cell_count": 3, "candidate_booths": CANDS},
        {"user_id": "u", "cell_count": 5, "candidate_booths": CANDS},
        {"user_id": "u", "cell_count": 7, "candidate_booths": CANDS},
        {
            "user_id": "u",
            "cell_count": 4,
            "candidate_booths": CANDS,
            "pre_survey": {"interest_categories": ["does_not_exist"]},
        },
        {
            "user_id": "u",
            "cell_count": 4,
            "candidate_booths": CANDS,
            "some_future_field": {"nested": True},
            "another": 123,
        },
        {},  # 完全に空
        {"user_id": "u", "cell_count": "weird", "candidate_booths": CANDS},  # 型崩れ
    ],
)
def test_always_200_and_scores_match_candidates(client, payload):
    r = client.post("/recommend/cells", json=payload)
    assert r.status_code == 200
    body = r.json()
    expected = len(payload.get("candidate_booths", []))
    assert len(body["scores"]) == expected
    assert len(body["assigned"]) <= 6
    assert body["decision_table_size"] is None  # スナップショット未取得
    assert body["phase"] == "COVERAGE"


def test_broken_json_body_still_200(client):
    r = client.post(
        "/recommend/cells", content=b"not json", headers={"content-type": "application/json"}
    )
    assert r.status_code == 200
    assert r.json()["scores"] == []


def test_rating_all_null_gives_rating_affinity_two(client):
    payload = {
        "user_id": "u",
        "cell_count": 2,
        "candidate_booths": [{"booth_id": "b1", "category_id": "cat_a", "visitor_count": 1}],
        "pre_survey": {"interest_categories": ["cat_a"], "top_interest_category": "cat_a"},
        "visited_booths": [{"booth_id": "x", "source": "FREE_VISIT", "rating": None}],
    }
    body = client.post("/recommend/cells", json=payload).json()
    assert body["scores"][0]["attributes"]["condition"]["rating_affinity"] == 2
