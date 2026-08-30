"""FastAPI 本体・lifespan (docs/specs/08-architecture.md §2)。

本番向けの条件付き登録 (docs/specs/11-deployment.md D-3, D-4):
  - `APP_ENV=production` では `/demo` `/demo/run` を登録しない（存在させない・404）
  - `APP_ENV=production` かつ `OPS_TOKEN` 未設定なら `/ops/*` を登録しない（404）
  「認証をかける」ではなくルーティング自体を条件付きにする。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .. import __version__, logging as jsonl
from ..cache import RuleCache
from ..settings import Settings, get_settings
from ..strategies import resolve_strategy
from .routes_ops import public_router as ops_public_router, router as ops_router
from .routes_recommend import router as recommend_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # create_app が settings を差し込んでいればそれを使う（テスト・明示構成）。
    settings = getattr(app.state, "settings", None) or get_settings()
    app.state.settings = settings
    app.state.rule_cache = RuleCache()
    _, strategy_note = resolve_strategy(settings.strategy, is_production=settings.is_production)
    # 段3（ADR 0002 決着後）でスナップショット取得と5分ごとの規則再生成を起動する。
    # 現状は取得経路が未結線のため、規則キャッシュは空のまま = SIMILARITY 以下で運用。
    jsonl.emit(
        "startup",
        {
            "engine_version": settings.resolved_engine_version,
            "app_env": settings.app_env,
            "enabled_attributes": list(settings.enabled_attributes),
            "phase_drsa_min": settings.phase_drsa_min,
            "strategy": settings.strategy,
            "strategy_note": strategy_note,
            "ops_protected": bool(settings.ops_token),
            "ops_registered": getattr(app.state, "ops_registered", True),
            "demo_registered": getattr(app.state, "demo_registered", True),
            "snapshot_wired": False,
            "note": "SIMILARITY/DRSA are not wired (ADR 0002 undecided).",
        },
    )
    yield


def _register_demo(app: FastAPI) -> None:
    @app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
    async def demo() -> str:
        """パラメータ調整プレイグラウンド（合成シナリオ・目視確認用）。"""
        from ..demo import build_playground_html

        return build_playground_html()

    @app.post("/demo/run", include_in_schema=False)
    async def demo_run(body: dict | None = None) -> dict:
        """合成シナリオを上書きパラメータで再計算して返す。overrides は既知キー・範囲内に丸める。"""
        from ..demo import report_payload

        overrides = (body or {}).get("overrides") if isinstance(body, dict) else None
        return report_payload(overrides)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="event-support-recommend", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.include_router(recommend_router)
    app.include_router(ops_public_router)  # /health /ready は常時公開

    # /ops/* — production かつ OPS_TOKEN 未設定なら登録しない (D-4)。
    ops_registered = not (settings.is_production and not settings.ops_token)
    if ops_registered:
        app.include_router(ops_router)
    app.state.ops_registered = ops_registered

    # /demo — production では登録しない (D-3)。この 404 は暫定
    # (docs/specs/parameter-tuning/README.md P-1/P-2 が未決)。
    demo_registered = not settings.is_production
    if demo_registered:
        _register_demo(app)
    app.state.demo_registered = demo_registered

    return app


app = create_app()
