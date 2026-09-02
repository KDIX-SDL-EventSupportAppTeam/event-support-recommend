"""FastAPI 本体・lifespan (docs/specs/08-architecture.md §2)。

本番向けの条件付き登録 (docs/specs/11-deployment.md D-3, D-4, ADR 0008 §2):
  - `APP_ENV=production` かつ `OPS_TOKEN` 未設定なら `/ops/*` `/demo` を登録しない（404）
  - 本番で `OPS_TOKEN` があれば `/demo` を登録し、`/ops/*` と同じ `require_ops()` で保護する
  「本番なのに秘密が未設定」を、素通しではなく機能停止で表現する。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from .. import __version__, logging as jsonl
from ..cache import RuleCache, SnapshotCache
from ..data import SnapshotRefresher, build_repository
from ..snapshot_build import refresh_caches
from ..settings import Settings, get_settings
from ..strategies import resolve_strategy
from .routes_ops import (
    public_router as ops_public_router,
    require_ops,
    router as ops_router,
)
from .routes_recommend import router as recommend_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # create_app が settings を差し込んでいればそれを使う（テスト・明示構成）。
    settings = getattr(app.state, "settings", None) or get_settings()
    app.state.settings = settings
    app.state.rule_cache = RuleCache()
    app.state.snapshot_cache = SnapshotCache()
    _, strategy_note = resolve_strategy(settings.strategy, is_production=settings.is_production)

    # 段3 — スナップショットの定期再取得。READONLY_PROXY_URL が空なら起動しない
    # （= COVERAGE 固定で動く、01-snapshot-source.md）。取得できた1周ごとに
    # refresh_caches が決定表・規則・近傍データを作って両キャッシュへ入れる（段3-b）。
    refresher: SnapshotRefresher | None = None
    snapshot_wired = bool(settings.readonly_proxy_url.strip())
    # /ops/rebuild が使う。プロキシ未設定なら UnavailableRepository。
    app.state.snapshot_repo = build_repository(settings)

    def _on_snapshot(snap) -> None:
        refresh_caches(
            snap,
            settings=settings,
            rule_cache=app.state.rule_cache,
            snapshot_cache=app.state.snapshot_cache,
        )

    if snapshot_wired:
        refresher = SnapshotRefresher(
            build_repository(settings),
            interval_sec=settings.snapshot_ttl_sec,
            on_snapshot=_on_snapshot,
            event_id_getter=lambda: (
                settings.snapshot_event_id or getattr(app.state, "last_event_id", None)
            ),
        )
        refresher.start()
    app.state.snapshot_refresher = refresher

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
            "snapshot_wired": snapshot_wired,
        },
    )
    try:
        yield
    finally:
        if refresher is not None:
            await refresher.stop()


def _register_demo(app: FastAPI) -> None:
    """パラメータ調整プレイグラウンドを登録する (ADR 0008 §2)。

    `require_ops()` は `OPS_TOKEN` が空なら素通しするので、開発時の挙動は従来どおり。
    """

    @app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
    async def demo(request: Request) -> str:
        """パラメータ調整プレイグラウンド（合成シナリオ・目視確認用）。"""
        require_ops(request)
        from ..demo import build_playground_html

        return build_playground_html()

    @app.post("/demo/run", include_in_schema=False)
    async def demo_run(request: Request, body: dict | None = None) -> dict:
        """合成シナリオを上書きパラメータで再計算して返す。overrides は既知キー・範囲内に丸める。

        **本番設定は書き換えない。** `demo.report_payload()` は毎回新しい `Settings` を組み立て、
        `app.state.settings` に触らない (ADR 0008 §3・X-1/X-2)。
        """
        require_ops(request)
        from ..demo import report_payload

        overrides = (body or {}).get("overrides") if isinstance(body, dict) else None
        return report_payload(overrides)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="event-support-recommend", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.include_router(recommend_router)
    app.include_router(ops_public_router)  # /health /ready は常時公開

    # /ops/* /demo — production かつ OPS_TOKEN 未設定なら登録しない (D-4, ADR 0008 §2)。
    # 秘密があれば本番でも登録し、require_ops() で保護する。
    protected_registered = not (settings.is_production and not settings.ops_token)
    if protected_registered:
        app.include_router(ops_router)
        _register_demo(app)
    app.state.ops_registered = protected_registered
    app.state.demo_registered = protected_registered

    return app


app = create_app()
