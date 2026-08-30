"""FastAPI 本体・lifespan (docs/specs/08-architecture.md §2)。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .. import __version__, logging as jsonl
from ..cache import RuleCache
from ..settings import get_settings
from .routes_ops import router as ops_router
from .routes_recommend import router as recommend_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.rule_cache = RuleCache()
    # 段3（ADR 0002 決着後）でスナップショット取得と5分ごとの規則再生成を起動する。
    # 現状は取得経路が未結線のため、規則キャッシュは空のまま = SIMILARITY 以下で運用。
    jsonl.emit(
        "startup",
        {
            "engine_version": settings.resolved_engine_version,
            "enabled_attributes": list(settings.enabled_attributes),
            "phase_drsa_min": settings.phase_drsa_min,
            "snapshot_wired": False,
            "note": "SIMILARITY/DRSA are not wired (ADR 0002 undecided).",
        },
    )
    yield


app = FastAPI(title="event-support-recommend", version=__version__, lifespan=lifespan)
app.include_router(recommend_router)
app.include_router(ops_router)


@app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
async def demo() -> str:
    """目視確認レポート（合成シナリオ）。tools/build_report.py と同じ内容を動的生成する。"""
    from ..demo import build_report_html

    return build_report_html()
