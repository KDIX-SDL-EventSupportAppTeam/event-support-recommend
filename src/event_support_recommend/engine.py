"""推薦の組み立て。api/ と strategies/ の間。

例外は握りつぶして COVERAGE 相当を返す。500 を返さない (docs/specs/01-io-contract.md O-6,
docs/rules/coding.md)。phase には「実際に使えた戦略」を返す。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from . import __version__, logging as jsonl
from .api.schemas import (
    AssignedOut,
    RecommendRequest,
    RecommendResponse,
    ScoreOut,
)
from .assignment import rank_pool, round_cell_count, select_assigned
from .cache import RuleCache
from .models import (
    CandidateBooth,
    Participant,
    Phase,
    RecommendationContext,
    RequestContext,
    RuntimeConfig,
    ScoredBooth,
    Survey,
    Visit,
    VisitSource,
    InterestMatch,
)
from .phases import decide_phase, evaluate_quality_gate
from .settings import Settings
from .strategies import StrategyUnavailable
from .strategies.common import compute_candidate_features, vector_from_features
from .strategies.registry import build_ladder, phase_of

_EXPLORATION_MAP = {"low": 1, "mid": 2, "high": 3, "1": 1, "2": 2, "3": 3}


def _runtime_config(s: Settings) -> RuntimeConfig:
    return RuntimeConfig(
        enabled_attributes=tuple(s.enabled_attributes),
        w_coverage=s.w_coverage,
        w_interest=s.w_interest,
        interest_partial=s.interest_partial,
        interest_mismatch=s.interest_mismatch,
        high_rating_ratio=s.high_rating_ratio,
        low_rating_ratio=s.low_rating_ratio,
        rating_scale_default=s.rating_scale_default,
        drsa_coverage_floor=s.drsa_coverage_floor,
        similarity_coverage_floor=s.similarity_coverage_floor,
        similarity_neighbors=s.similarity_neighbors,
        similarity_shrinkage=s.similarity_shrinkage,
        max_per_category=s.max_per_category,
        experiment_split_enabled=s.experiment_split_enabled,
        experiment_arm_a=s.experiment_arm_a,
        experiment_arm_b=s.experiment_arm_b,
        engine_version=s.resolved_engine_version,
    )


def _parse_survey(pre_survey: dict | None) -> Survey:
    if not pre_survey:
        return Survey.empty()

    def _first(*keys):
        for k in keys:
            if k in pre_survey and pre_survey[k] not in (None, "", []):
                return pre_survey[k]
        return None

    ic = _first("interest_categories") or []
    if not isinstance(ic, (list, tuple)):
        ic = [ic]
    explore_raw = _first("exploration_disposition")
    explore = _EXPLORATION_MAP.get(str(explore_raw).strip().lower()) if explore_raw is not None else None

    return Survey(
        answered=True,
        interest_categories=tuple(str(x) for x in ic),
        top_interest_category=(str(_first("top_interest_category")) if _first("top_interest_category") else None),
        age_range=_first("age_range", "age_group"),
        occupation=_first("occupation"),
        gender=_first("gender"),
        exploration_disposition=explore,
    )


def _build_context(
    req: RecommendRequest,
    cfg: RuntimeConfig,
    now: datetime,
    *,
    snapshot_data=None,
    ruleset=None,
) -> RecommendationContext:
    cat_by_booth = {c.booth_id: c.category_id for c in req.candidate_booths}

    candidates = tuple(
        CandidateBooth(
            booth_id=c.booth_id,
            category_id=c.category_id,
            visitor_count=max(0, int(c.visitor_count or 0)),
            is_active=bool(c.is_active),
        )
        for c in req.candidate_booths
    )

    visits: list[Visit] = []
    ratings: list[Visit] = []
    for v in req.visited_booths:
        cat = v.category_id or cat_by_booth.get(v.booth_id)
        visit = Visit(
            booth_id=v.booth_id,
            order=v.order,
            source=VisitSource.parse(v.source),
            rating=v.rating,
            rating_scale=v.rating_scale or req.rating_scale,
            category_id=cat,
        )
        visits.append(visit)
        if v.rating is not None:
            ratings.append(visit)

    participant = Participant(
        user_id=req.user_id,
        survey=_parse_survey(req.pre_survey),
        visits=tuple(visits),
        ratings=tuple(ratings),
    )
    request_ctx = RequestContext(
        user_id=req.user_id,
        cell_count=req.cell_count,
        exclude_booth_ids=frozenset(req.exclude_booth_ids or ()),
        candidates=candidates,
        received_at=now,
        rating_scale=req.rating_scale,
        unlock_context=req.unlock_context or (req.card_id or ""),
    )
    from .models import EventSnapshot

    return RecommendationContext(
        request=request_ctx,
        participant=participant,
        snapshot=EventSnapshot.unavailable(),
        config=cfg,
        snapshot_data=snapshot_data,
        ruleset=ruleset,
    )


def _empty_response() -> RecommendResponse:
    return RecommendResponse(phase=Phase.COVERAGE.value, decision_table_size=None, assigned=[], scores=[])


def run_recommendation(
    req: RecommendRequest,
    *,
    settings: Settings,
    rule_cache: RuleCache,
    snapshot_cache=None,
    now: datetime | None = None,
    log_kind: str = "recommend",
) -> RecommendResponse:
    """推薦を1回実行する。

    ``log_kind`` は JSONL の ``kind``。本番経路は既定の ``"recommend"``。
    デモ・リプレイは別の値を渡して研究ログと混ぜない
    (ADR 0008 §1, docs/specs/parameter-tuning/README.md P-1)。
    """
    now = now or datetime.now(timezone.utc)
    cfg = _runtime_config(settings)

    # --- キャッシュ読み出し（リクエスト経路では生成も取得もしない, R-2）---
    ruleset, rules_built_at = rule_cache.get()
    decision_table_size = rule_cache.decision_table_size
    gamma = rule_cache.gamma
    snapshot_data = snapshot_cache.get() if snapshot_cache is not None else None

    try:
        ctx = _build_context(req, cfg, now, snapshot_data=snapshot_data, ruleset=ruleset)
    except Exception as exc:  # pragma: no cover - 防御
        jsonl.emit(log_kind, {"error": f"context_build_failed: {exc!r}", "user_id": req.user_id})
        return _empty_response()

    if not ctx.request.candidates:
        return _empty_response()

    # --- 品質ゲート & フェーズ判定 ---
    certain_up = len(ruleset.certain_up) if ruleset else 0
    candidate_coverage = 0.0
    if ruleset is not None and ruleset.rules:
        try:
            feats = compute_candidate_features(ctx)
            vectors = [
                vector_from_features(feats[c.booth_id], cfg.enabled_attributes)
                for c in ctx.request.candidates
            ]
            candidate_coverage = ruleset.candidate_coverage(vectors)
        except Exception:  # pragma: no cover - 防御。ゲートは覆えない側に倒す
            candidate_coverage = 0.0
    gate = evaluate_quality_gate(
        decision_table_size, certain_up, gamma, candidate_coverage, settings
    )
    judged_phase = decide_phase(decision_table_size, settings, gate=gate)

    # --- 退避ラダー: judged_phase と STRATEGY から実際の戦略を選び、
    #     実行できなければ静かに1段下る (docs/specs/04-strategies.md §5)。
    #     phase には「実際に使えた戦略」を返す。ハードコードはここで外れた。---
    ladder = build_ladder(
        settings.strategy, judged_phase, is_production=settings.is_production
    )
    scored, actual_phase, strategy_name, fallback_reason = _run_ladder(
        ladder, ctx, cfg, budget_ms=settings.response_budget_ms, log_kind=log_kind
    )

    # --- 全候補ランキングと assigned 抽出（戦略の外・§6）---
    ranked = rank_pool(
        scored,
        user_id=ctx.request.user_id,
        unlock_context=ctx.request.unlock_context,
        exclude_booth_ids=ctx.request.exclude_booth_ids,
    )
    assigned = select_assigned(
        scored,
        cell_count=ctx.request.cell_count,
        user_id=ctx.request.user_id,
        unlock_context=ctx.request.unlock_context,
        exclude_booth_ids=ctx.request.exclude_booth_ids,
        max_per_category=cfg.max_per_category,
    )
    assigned_ids = {s.booth_id for s in assigned}
    rank_by_id = {s.booth_id: i + 1 for i, s in enumerate(ranked)}

    scores_out = [
        ScoreOut(
            booth_id=s.booth_id,
            score=round(s.score, 6),
            rank=rank_by_id.get(s.booth_id, len(ranked)),
            was_assigned=s.booth_id in assigned_ids,
            interest_match=s.interest_match.value,
            attributes=s.attributes,
            reason=s.reason,
        )
        for s in scored
    ]
    assigned_out = [
        AssignedOut(
            booth_id=s.booth_id,
            score=round(s.score, 6),
            rank=rank_by_id.get(s.booth_id, len(ranked)),
            attributes=s.attributes,
            reason=s.reason,
        )
        for s in assigned
    ]

    resp = RecommendResponse(
        phase=actual_phase.value,
        decision_table_size=decision_table_size,
        assigned=assigned_out,
        scores=scores_out,
    )

    _emit_phase_changed(log_kind, actual_phase, judged_phase, fallback_reason)
    _log_recommend(
        req, resp, judged_phase, actual_phase, rules_built_at, strategy_name,
        cfg.engine_version or __version__, log_kind, fallback_reason,
    )
    return resp


# フェーズが前回と変わった回を1行で特定できるようにするための直近フェーズ記録
# (docs/specs/runtime-phase-switching/04-observability.md「ログ」)。log_kind ごとに分ける
# ので、デモ・リプレイの切り替わりが研究ログの phase_changed に混ざらない。
_last_phase_by_kind: dict[str, str] = {}


def _emit_phase_changed(
    log_kind: str, actual_phase: Phase, judged_phase: Phase, fallback_reason: str | None
) -> None:
    prev = _last_phase_by_kind.get(log_kind)
    _last_phase_by_kind[log_kind] = actual_phase.value
    if prev is not None and prev != actual_phase.value:
        jsonl.emit(
            "phase_changed",
            {
                "from": prev,
                "to": actual_phase.value,
                "judged_phase": judged_phase.value,
                "fallback_reason": fallback_reason,
                "log_kind": log_kind,
            },
        )


def _coverage_fallback_scored(ctx: RecommendationContext, cfg: RuntimeConfig) -> list[ScoredBooth]:
    """ラダーが尽きた場合の最終防御（通常は CoverageStrategy が必ず成功する）。"""
    return [
        ScoredBooth(
            booth_id=c.booth_id,
            score=0.5,
            interest_match=InterestMatch.UNKNOWN,
            attributes={"v": 1, "strategy": "COVERAGE", "enabled": list(cfg.enabled_attributes),
                        "condition": {}, "raw": {"visitor_count": c.visitor_count}},
            reason={"v": 1, "strategy": "COVERAGE", "rules": [],
                    "engine": {"version": cfg.engine_version or __version__}},
        )
        for c in ctx.request.candidates
    ]


def _run_ladder(
    ladder: list,
    ctx: RecommendationContext,
    cfg: RuntimeConfig,
    *,
    budget_ms: int,
    log_kind: str,
) -> tuple[list[ScoredBooth], Phase, str, str | None]:
    """ラダーを上から試す。返り値 (scored, actual_phase, strategy_name, fallback_reason)。

    退避条件（04-strategies.md §5, 03-strategy-wiring.md）:
      - StrategyUnavailable（規則0本 / キャッシュ未構築 / 近傍が作れない / 未回答）
      - 実行中の例外
      - 実行が RESPONSE_BUDGET_MS を超えた（打ち切って1段下へ。200 は返す）
    末尾は必ず COVERAGE。COVERAGE は層1の情報だけで動くので必ず成功する。
    """
    budget = max(0.0, budget_ms / 1000.0)
    reasons: list[str] = []
    started = time.monotonic()
    for i, strat in enumerate(ladder):
        is_last = i == len(ladder) - 1
        try:
            scored = strat.recommend(ctx)
        except StrategyUnavailable as exc:
            reasons.append(f"{strat.name}:{exc}")
            continue
        except Exception as exc:
            jsonl.emit(log_kind, {"error": f"strategy_failed: {strat.name}: {exc!r}",
                                  "user_id": ctx.request.user_id})
            reasons.append(f"{strat.name}:exception")
            continue
        if not is_last and (time.monotonic() - started) > budget:
            reasons.append(f"{strat.name}:budget_exceeded")
            continue
        return scored, phase_of(strat.name), strat.name, (";".join(reasons) or None)

    reasons.append("ladder_exhausted")
    return _coverage_fallback_scored(ctx, cfg), Phase.COVERAGE, "COVERAGE", ";".join(reasons)


def _log_recommend(
    req: RecommendRequest,
    resp: RecommendResponse,
    judged_phase: Phase,
    actual_phase: Phase,
    rules_built_at: datetime | None,
    strategy_name: str,
    engine_version: str,
    log_kind: str = "recommend",
    fallback_reason: str | None = None,
) -> None:
    jsonl.emit(
        log_kind,
        {
            "user_id": req.user_id,
            # 本番はコミット SHA。当日の設定変更の前後を分ける唯一の手がかり
            # (docs/specs/09-research-design.md R-2, 11-deployment.md X-6)。
            "engine_version": engine_version,
            "judged_phase": judged_phase.value,
            "phase": actual_phase.value,
            # 判定と実際がずれた＝退避。理由を残す (03-strategy-wiring.md §退避)。
            "fell_back": actual_phase != judged_phase,
            "fallback_reason": fallback_reason,
            # 契約の phase（3値）とは別。STRATEGY 固定時はここで判別する (ADR 0007 §4)。
            "strategy": strategy_name,
            "decision_table_size": resp.decision_table_size,
            "rules_built_at": rules_built_at.isoformat() if rules_built_at else None,
            "candidate_count": len(resp.scores),
            "assigned_count": len(resp.assigned),
            "cell_count": round_cell_count(req.cell_count),
            "assigned_booth_ids": [a.booth_id for a in resp.assigned],
            "scores": [
                {"booth_id": s.booth_id, "score": s.score, "interest_match": s.interest_match,
                 "was_assigned": s.was_assigned, "rank_in_event": s.rank}
                for s in resp.scores
            ],
        },
    )
