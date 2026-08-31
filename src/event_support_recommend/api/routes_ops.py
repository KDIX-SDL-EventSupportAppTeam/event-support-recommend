"""/health /ready /ops/* (docs/specs/01-io-contract.md §4, docs/specs/10-observability.md §2)。

/ops/* は OPS_TOKEN で保護する。このサービスは書き込みを行わないので最悪でも情報の露出に留まる。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from ..cache import RuleCache
from ..engine import run_recommendation
from ..phases import decide_phase, evaluate_quality_gate
from ..settings import Settings, get_settings
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

    段3 未結線なので本番では永久に 503 を返す（仕様どおりの正しい挙動）。
    **Cloud Run の startup / liveness プローブに使ってはいけない**
    (docs/specs/11-deployment.md D-2, X-1)。使うとリビジョンが永久に起動しない。
    プローブは依存ゼロで即答する `/health` を使うこと。
    """
    rc = _rule_cache(request)
    payload = {
        "ready": rc.ready,
        "rules": rc.ready,
        "snapshot_wired": False,
        "note": "monitoring only; do not use as a Cloud Run probe (see 11-deployment.md D-2)",
    }
    return JSONResponse(payload, status_code=200 if rc.ready else 503)


@router.get("/ops/state")
async def ops_state(request: Request) -> dict:
    require_ops(request)
    s = _settings(request)
    rc = _rule_cache(request)
    rules_state = rc.snapshot_state()
    size = rc.decision_table_size
    gate = evaluate_quality_gate(
        size, rules_state["count_certain_up"], rc.gamma, 0.0, s
    )
    phase = decide_phase(size, s, gate=gate)
    return {
        "engine_version": s.resolved_engine_version,
        "snapshot": {
            "built_at": rules_state["built_at"],
            "ok": rc.ready,
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
            "current": phase.value,
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
        "notes": ["SIMILARITY/DRSA not wired: ADR 0002 undecided"],
    }


@router.post("/ops/rebuild")
async def ops_rebuild(request: Request) -> dict:
    require_ops(request)
    # スナップショット取得が未結線のため、現状は再生成対象が無い。
    return {"rebuilt": False, "reason": "snapshot path not wired (ADR 0002)"}


@router.post("/ops/replay")
async def ops_replay(request: Request) -> dict:
    """保存済みリクエストを再実行して出力を返す。アルゴリズム変更時の回帰確認。

    **本物の `user_id` で走るため、ログの kind を分けないと研究ログと区別できなくなる**
    (ADR 0008 §1)。
    """
    require_ops(request)
    body = await request.json()
    payload = RecommendRequest.model_validate(body)
    resp = run_recommendation(
        payload,
        settings=_settings(request),
        rule_cache=_rule_cache(request),
        log_kind="recommend_replay",
    )
    return resp.model_dump()
