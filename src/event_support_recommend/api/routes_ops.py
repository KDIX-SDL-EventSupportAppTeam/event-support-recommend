"""/health /ready /ops/* (docs/specs/01-io-contract.md §4, docs/specs/10-observability.md §2)。

/ops/* は OPS_TOKEN で保護する。このサービスは書き込みを行わないので最悪でも情報の露出に留まる。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from ..cache import RuleCache, SnapshotCache
from ..data import build_repository
from ..engine import run_recommendation
from ..phases import decide_phase, evaluate_quality_gate
from ..settings import Settings, get_settings
from ..snapshot_build import refresh_caches
from .schemas import RecommendRequest

# /health /ready は常時公開（Cloud Run プローブ・監視）。/ops/* だけを条件付き登録する
# (docs/specs/11-deployment.md D-2, D-4)。
public_router = APIRouter()
router = APIRouter()


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def _rule_cache(request: Request) -> RuleCache:
    rc = getattr(request.app.state, "rule_cache", None)
    return rc if isinstance(rc, RuleCache) else RuleCache()


def _snapshot_cache(request: Request) -> SnapshotCache:
    sc = getattr(request.app.state, "snapshot_cache", None)
    return sc if isinstance(sc, SnapshotCache) else SnapshotCache()


def require_ops(request: Request) -> None:
    """`/ops/*` と `/demo` の共通ガード (ADR 0008 §2)。未設定なら開発用に素通しする。"""
    token = _settings(request).ops_token
    if not token:
        return  # 未設定なら開発用に素通し
    given = request.headers.get("x-ops-token") or ""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        given = given or auth[7:]
    if given != token:
        raise HTTPException(status_code=401, detail="invalid ops token")


@public_router.get("/health")
async def health(request: Request) -> dict:
    """生存確認。依存なしで即答する。

    `engine_version` は本番ではコミット SHA（`ENGINE_VERSION`）。デプロイ後の V-1 は
    ここでリビジョンを確認する (docs/specs/11-deployment.md §7 V-1・X-6)。
    """
    return {"status": "ok", "engine_version": _settings(request).resolved_engine_version}


@public_router.get("/ready")
async def ready(request: Request) -> Response:
    """スナップショット・規則が温まっているか（監視用）。

    スナップショットと規則が温まったかを表す（段3・段4 結線後の意味）。

    **それでも Cloud Run の startup / liveness プローブに使ってはいけない**
    (docs/specs/11-deployment.md D-2, 04-observability.md T-47)。規則が0本でも
    サービスは正常（COVERAGE で応答できる）であり、プローブにすると正常なリビジョンが
    起動しない。プローブは依存ゼロで即答する `/health`。ここは監視用途に限る。
    """
    rc = _rule_cache(request)
    sc = _snapshot_cache(request)
    payload = {
        "ready": rc.ready,
        "rules": rc.ready,
        "snapshot_ok": sc.ready,
        "note": "monitoring only; do not use as a Cloud Run probe (see 11-deployment.md D-2)",
    }
    return JSONResponse(payload, status_code=200 if rc.ready else 503)


@router.get("/ops/state")
async def ops_state(request: Request) -> dict:
    require_ops(request)
    s = _settings(request)
    rc = _rule_cache(request)
    sc = _snapshot_cache(request)
    rules_state = rc.snapshot_state()
    snap_state = sc.snapshot_state()
    size = rc.decision_table_size
    gate = evaluate_quality_gate(
        size, rules_state["count_certain_up"], rc.gamma, 0.0, s
    )
    judged = decide_phase(size, s, gate=gate)
    # 「実際に返した phase」を優先して返す (T-44)。まだ1件も推薦していなければ判定値。
    current = getattr(request.app.state, "last_phase", None) or judged.value
    return {
        "engine_version": s.resolved_engine_version,
        "snapshot": {
            "built_at": snap_state["built_at"] or rules_state["built_at"],
            "ok": sc.ready or rc.ready,
            "decision_table_size": size,
        },
        "rules": {
            "built_at": rules_state["built_at"],
            "count_certain_up": rules_state["count_certain_up"],
            "count_certain_down": rules_state["count_certain_down"],
            "gamma": rules_state["gamma"],
            "candidate_coverage": 0.0,
            "consistency_level": rules_state["consistency_level"] or s.drsa_consistency,
        },
        "phase": {
            "current": current,
            "judged": judged.value,
            "quality_gate_passed": gate.passed,
            "gate_detail": gate.detail.as_dict(),
        },
        "experiment": {
            "split_active": s.experiment_split_enabled and gate.passed,
            "split_started_at": None,
        },
        "config": {
            "phase_similarity_min": s.phase_similarity_min,
            "phase_drsa_min": s.phase_drsa_min,
            "enabled_attributes": list(s.enabled_attributes),
            "default_phase_drsa_min": s.default_phase_drsa_min,
        },
        "notes": [],
    }


@router.post("/ops/rebuild")
async def ops_rebuild(request: Request) -> dict:
    """その場でスナップショット取得と規則再生成を1周させる（調査手段。通常は使わない）。

    5分ごとの自動更新で足りるが、「フェーズが上がるはずなのに上がらない」ときに使う
    (docs/specs/runtime-phase-switching/04-observability.md)。
    """
    require_ops(request)
    s = _settings(request)
    rc = _rule_cache(request)
    sc = _snapshot_cache(request)
    repo = getattr(request.app.state, "snapshot_repo", None) or build_repository(s)
    event_id = s.snapshot_event_id or getattr(request.app.state, "last_event_id", None)

    before = rc.decision_table_size
    try:
        snapshot = await asyncio.to_thread(repo.fetch, event_id)
        refresh_caches(snapshot, settings=s, rule_cache=rc, snapshot_cache=sc)
    except Exception as exc:  # pragma: no cover - 防御。前回キャッシュは保持される
        return {"rebuilt": False, "reason": f"rebuild failed: {exc!r}",
                "decision_table_size": before}
    return {
        "rebuilt": bool(getattr(snapshot, "built", False)),
        "snapshot_ok": bool(getattr(snapshot, "built", False)),
        "previous_decision_table_size": before,
        "decision_table_size": rc.decision_table_size,
    }


@router.post("/ops/replay")
async def ops_replay(request: Request) -> dict:
    """保存済みリクエストを再実行して出力を返す。アルゴリズム変更時の回帰確認。

    **本物の `user_id` で走るため、ログの kind を分けないと研究ログと区別できなくなる**
    (ADR 0008 §1)。
    """
    require_ops(request)
    body = await request.json()
    payload = RecommendRequest.model_validate(body)
    sc = getattr(request.app.state, "snapshot_cache", None)
    resp = run_recommendation(
        payload,
        settings=_settings(request),
        rule_cache=_rule_cache(request),
        snapshot_cache=sc,
        log_kind="recommend_replay",
    )
    return resp.model_dump()
