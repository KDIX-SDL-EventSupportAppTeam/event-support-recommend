from __future__ import annotations

import os

import pytest

os.environ.setdefault("OPS_TOKEN", "")

from fastapi.testclient import TestClient  # noqa: E402

from event_support_recommend.api.app import create_app  # noqa: E402
from event_support_recommend.api.schemas import RecommendRequest  # noqa: E402
from event_support_recommend.cache import RuleCache  # noqa: E402
from event_support_recommend.engine import run_recommendation  # noqa: E402
from event_support_recommend.settings import Settings  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        enabled_attributes=["preference_match", "rating_affinity"],
        ops_token="",
    )


@pytest.fixture
def client(settings: Settings):
    """テスト用アプリ。

    モジュール直下の `api.app.app` を使わない。あれは import 時に `.env` を読むため、
    開発者の手元に `APP_ENV=production` や `STRATEGY=random` があるとテストの前提が
    変わってしまう（docs/specs/11-deployment.md D-3・ADR 0007）。
    """
    with TestClient(create_app(settings)) as c:
        yield c


def make_request(**over) -> RecommendRequest:
    base = dict(
        user_id="u1",
        cell_count=4,
        exclude_booth_ids=[],
        candidate_booths=[
            {"booth_id": "b1", "category_id": "cat_a", "visitor_count": 3},
            {"booth_id": "b2", "category_id": "cat_b", "visitor_count": 40},
            {"booth_id": "b3", "category_id": "cat_c", "visitor_count": 12},
            {"booth_id": "b4", "category_id": "cat_a", "visitor_count": 25},
        ],
        pre_survey={"interest_categories": ["cat_a"], "top_interest_category": "cat_a"},
        visited_booths=[],
    )
    base.update(over)
    return RecommendRequest.model_validate(base)


@pytest.fixture
def run(settings):
    def _run(req: RecommendRequest, rule_cache: RuleCache | None = None):
        return run_recommendation(
            req, settings=settings, rule_cache=rule_cache or RuleCache()
        )

    return _run
