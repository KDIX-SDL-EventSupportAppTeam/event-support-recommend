"""決定表の組み立てとキャッシュ更新 (段3-b)。

`data/` が取得した生の行から DRSA の決定表を作り、規則を生成して
`RuleCache` と `SnapshotCache` に入れる。

- **`build_decision_records` は純関数**。DB にも HTTP にも触れない。合成データでテストできる
- 1行 = 「ある参加者が、あるブースを訪問し、評価した」1件
- 評価が無い訪問は行にしない（未評価を評価0として扱わない）
- `role <> 'participant'` と `is_active = 0` を除外する
- 同一参加者 × 同一ブースの重複は畳む
- 条件属性は `ENABLED_ATTRIBUTES` の2個（`features/` を呼ぶ）

docs/specs/runtime-phase-switching/02-decision-table.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from . import features, logging as jsonl
from .cache import RuleCache, SnapshotCache
from .drsa import DecisionTable, approximate, generate_rules
from .models import DecisionClass, Survey


# --------------------------------------------------------------------------- #
# 生の行 → ドメイン
# --------------------------------------------------------------------------- #
def _as_float(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _truthy(v: object) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "no")
    return bool(v)


def _parse_survey_row(row: dict) -> Survey:
    """`user_survey_answers` の1行を `Survey` へ。`custom_answers` は JSON かもしれない。"""
    custom = row.get("custom_answers")
    if isinstance(custom, str):
        try:
            custom = json.loads(custom)
        except ValueError:
            custom = {}
    if not isinstance(custom, dict):
        custom = {}

    def pick(*keys):
        for src in (custom, row):
            for k in keys:
                if k in src and src[k] not in (None, "", []):
                    return src[k]
        return None

    ic = pick("interest_categories") or []
    if not isinstance(ic, (list, tuple)):
        ic = [ic]
    explore = pick("exploration_disposition")
    explore_map = {"low": 1, "mid": 2, "high": 3, "1": 1, "2": 2, "3": 3}
    explore_val = explore_map.get(str(explore).strip().lower()) if explore is not None else None

    return Survey(
        answered=True,
        interest_categories=tuple(str(x) for x in ic),
        top_interest_category=(str(pick("top_interest_category")) if pick("top_interest_category") else None),
        age_range=pick("age_range", "age_group"),
        occupation=pick("occupation", "industry"),
        gender=pick("gender"),
        exploration_disposition=explore_val,
    )


@dataclass(frozen=True)
class _RatedVisit:
    user_id: str
    booth_id: str
    category_id: str | None
    normalized: float
    decision: DecisionClass
    checkin_id: str
    checked_in_at: str


def _rated_visits(tables: dict[str, list[dict]], *, default_scale: int, high_ratio: float,
                  low_ratio: float) -> list[_RatedVisit]:
    """評価済みチェックインだけを、参加者・アクティブブースに限って抽出する。"""
    participants = {
        str(u.get("id")) for u in tables.get("users", []) if str(u.get("role")) == "participant"
    }
    booth_cat: dict[str, str | None] = {}
    active_booths: set[str] = set()
    for b in tables.get("booths", []):
        bid = str(b.get("id"))
        booth_cat[bid] = b.get("category_id")
        if _truthy(b.get("is_active", 1)):
            active_booths.add(bid)

    checkin_at: dict[str, str] = {
        str(c.get("id")): str(c.get("checked_in_at") or "") for c in tables.get("check_ins", [])
    }

    # 同一参加者 × 同一ブースは、最後の評価に畳む（checked_in_at → checkin_id 順で決定的）。
    by_pair: dict[tuple[str, str], _RatedVisit] = {}
    ratings = sorted(
        tables.get("booth_ratings", []),
        key=lambda r: (str(checkin_at.get(str(r.get("checkin_id")), "")), str(r.get("checkin_id"))),
    )
    for r in ratings:
        uid = str(r.get("user_id"))
        bid = str(r.get("booth_id"))
        if uid not in participants or bid not in active_booths:
            continue
        rating = _as_float(r.get("rating"))
        if rating is None:
            continue  # 評価が無い訪問は行にしない
        scale = r.get("scale")
        normalized = features.normalize_rating(
            rating, int(scale) if scale else None, default_scale=default_scale
        )
        decision = features.classify_decision(
            rating, int(scale) if scale else None, default_scale=default_scale, high_ratio=high_ratio
        )
        if normalized is None or decision is None:
            continue
        cid = str(r.get("checkin_id"))
        by_pair[(uid, bid)] = _RatedVisit(
            user_id=uid,
            booth_id=bid,
            category_id=booth_cat.get(bid),
            normalized=normalized,
            decision=decision,
            checkin_id=cid,
            checked_in_at=checkin_at.get(cid, ""),
        )
    return list(by_pair.values())


# --------------------------------------------------------------------------- #
# 決定表（純関数）
# --------------------------------------------------------------------------- #
def build_decision_records(snapshot_tables: dict[str, list[dict]], settings) -> list[dict]:
    """生の行から決定表のレコード（{attr: value, ..., "decision": "HIGH"/"LOW"}）を作る。

    純関数。`len()` が `decision_table_size`（評価済みチェックイン件数）に一致する。
    """
    enabled = list(settings.enabled_attributes)
    visits = _rated_visits(
        snapshot_tables,
        default_scale=settings.rating_scale_default,
        high_ratio=settings.high_rating_ratio,
        low_ratio=settings.low_rating_ratio,
    )
    surveys = {
        str(s.get("user_id")): _parse_survey_row(s)
        for s in snapshot_tables.get("user_survey_answers", [])
    }

    # 参加者ごとの高評価 / 低評価カテゴリ（当該行は除外して自己参照を避ける）。
    hi_by_user: dict[str, dict[str, set[str]]] = {}
    lo_by_user: dict[str, dict[str, set[str]]] = {}
    for v in visits:
        if v.category_id is None:
            continue
        tgt = hi_by_user if v.decision == DecisionClass.HIGH else lo_by_user
        tgt.setdefault(v.user_id, {}).setdefault(v.checkin_id, set()).add(v.category_id)

    records: list[dict] = []
    for v in visits:
        survey = surveys.get(v.user_id, Survey.empty())
        pm = features.preference_match(v.category_id, survey, frozenset())
        hi = {c for cid, cats in hi_by_user.get(v.user_id, {}).items() if cid != v.checkin_id for c in cats}
        lo = {c for cid, cats in lo_by_user.get(v.user_id, {}).items() if cid != v.checkin_id for c in cats}
        ra = features.rating_affinity(v.category_id, hi, lo)
        vector = features.condition_vector(enabled, preference_match=pm, rating_affinity=ra)
        records.append({**vector, "decision": v.decision.value})
    return records


# --------------------------------------------------------------------------- #
# キャッシュ更新（バックグラウンドの1周）
# --------------------------------------------------------------------------- #
def refresh_caches(snapshot, *, settings, rule_cache: RuleCache, snapshot_cache: SnapshotCache) -> None:
    """取得したスナップショットから決定表・規則・近傍データを作りキャッシュへ入れる。

    `SnapshotRefresher(on_snapshot=...)` に渡す。取得不能なら**何もしない**
    （前回のキャッシュを保持する。空にしない）。
    """
    tables = getattr(snapshot, "tables", None) or {}
    if not getattr(snapshot, "built", False) or not tables:
        return

    records = build_decision_records(tables, settings)
    table = DecisionTable.from_records(list(settings.enabled_attributes), records)
    approx = approximate(table, settings.drsa_consistency)
    ruleset = generate_rules(
        table, min_support=settings.min_support, consistency_level=settings.drsa_consistency
    )
    built_at = getattr(snapshot, "built_at", None) or datetime.now(timezone.utc)
    rule_cache.put(
        ruleset,
        decision_table_size=len(records),
        gamma=approx.gamma,
        built_at=built_at,
    )

    # SIMILARITY 用の生データ。
    visits = _rated_visits(
        tables,
        default_scale=settings.rating_scale_default,
        high_ratio=settings.high_rating_ratio,
        low_ratio=settings.low_rating_ratio,
    )
    ratings_by_user: dict[str, dict[str, float]] = {}
    all_norm: list[float] = []
    booth_category: dict[str, str | None] = {}
    for v in visits:
        ratings_by_user.setdefault(v.user_id, {})[v.booth_id] = v.normalized
        all_norm.append(v.normalized)
        booth_category[v.booth_id] = v.category_id
    global_mean = sum(all_norm) / len(all_norm) if all_norm else 0.5
    surveys_raw = {
        str(s.get("user_id")): _survey_axes(_parse_survey_row(s))
        for s in tables.get("user_survey_answers", [])
    }
    snapshot_cache.put(
        decision_table_size=len(records),
        surveys=surveys_raw,
        ratings_by_user=ratings_by_user,
        booth_category=booth_category,
        global_mean=global_mean,
        built_at=built_at,
    )
    jsonl.emit(
        "snapshot",
        {
            "ok": True,
            "decision_table_size": len(records),
            "rules_certain_up": len(ruleset.certain_up),
            "rules_certain_down": len(ruleset.certain_down),
            "gamma": approx.gamma,
            "global_mean": round(global_mean, 4),
        },
    )


def _survey_axes(survey: Survey) -> dict:
    """近傍距離に使う軸だけを取り出す。`gender` は入れない（S 個人特定回避）。"""
    return {
        "interest_categories": list(survey.interest_categories),
        "age_range": survey.age_range,
        "occupation": survey.occupation,
        "exploration_disposition": survey.exploration_disposition,
    }
