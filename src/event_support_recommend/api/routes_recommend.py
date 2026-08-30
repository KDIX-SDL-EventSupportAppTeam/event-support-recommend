"""POST /recommend/cells — これが本体。

どんな入力でも 200 を返す。500 を返さない (docs/specs/01-io-contract.md O-6)。
本文の検証エラーで 422 も返さない。壊れた入力は寛容に受けて COVERAGE 相当を返す。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import logging as jsonl
from ..cache import RuleCache
from ..engine import _empty_response, run_recommendation
from ..settings import get_settings
from .schemas import RecommendRequest, RecommendResponse

router = APIRouter()


def _rule_cache(request: Request) -> RuleCache:
    rc = getattr(request.app.state, "rule_cache", None)
    return rc if isinstance(rc, RuleCache) else RuleCache()


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

    try:
        return run_recommendation(payload, settings=settings, rule_cache=_rule_cache(request))
    except Exception as exc:  # pragma: no cover - 最終防御
        jsonl.emit("recommend", {"error": f"unhandled: {exc!r}"})
        return _empty_response()
