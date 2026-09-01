"""POST /recommend/cells — これが本体。

どんな入力でも 200 を返す。500 を返さない (docs/specs/01-io-contract.md O-6)。
本文の検証エラーで 422 も返さない。壊れた入力は寛容に受けて COVERAGE 相当を返す。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import logging as jsonl
from ..cache import RuleCache, SnapshotCache
from ..engine import _empty_response, run_recommendation
from ..settings import get_settings
from .schemas import RecommendRequest, RecommendResponse

router = APIRouter()


def _rule_cache(request: Request) -> RuleCache:
    rc = getattr(request.app.state, "rule_cache", None)
    return rc if isinstance(rc, RuleCache) else RuleCache()


def _snapshot_cache(request: Request) -> SnapshotCache | None:
    sc = getattr(request.app.state, "snapshot_cache", None)
    return sc if isinstance(sc, SnapshotCache) else None


@router.post("/recommend/cells", response_model=RecommendResponse)
async def recommend_cells(request: Request) -> RecommendResponse:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    try:
        payload = RecommendRequest.model_validate(body)
    except Exception as exc:
        jsonl.emit("recommend", {"error": f"request_parse_failed: {exc!r}"})
        return _empty_response()

    # SNAPSHOT_EVENT_ID 未設定時に refresher が使う「直近リクエストの event_id」を控える。
    eid = body.get("event_id") if isinstance(body, dict) else None
    if eid:
        request.app.state.last_event_id = str(eid)

    try:
        return run_recommendation(
            payload,
            settings=settings,
            rule_cache=_rule_cache(request),
            snapshot_cache=_snapshot_cache(request),
        )
    except Exception as exc:  # pragma: no cover - 最終防御
        jsonl.emit("recommend", {"error": f"unhandled: {exc!r}"})
        return _empty_response()
