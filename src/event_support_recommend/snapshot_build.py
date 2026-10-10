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
from .models import DecisionClass, InterestMatch, Survey, Visit, VisitSource
from .rst import SerObservation, SerRule, build_ser_rules


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
# 決定表の条件属性にしないもの。人の属性は同じ人の候補ブースでは値が同じで、ブースを順位付けできない
# （DRSA の条件に入れると誤作動が増える。docs/specs/05-drsa.md §7）。有効化されていても載せない。
_NOT_IN_DECISION_TABLE = frozenset({"exploration_disposition"})


def decision_table_attributes(settings) -> list[str]:
    """決定表（と DRSA 規則）の条件属性名。`ENABLED_ATTRIBUTES` から人の属性を除いたもの。"""
    return [a for a in settings.enabled_attributes if a not in _NOT_IN_DECISION_TABLE]


def _free_pairs(tables: dict[str, list[dict]]) -> set[tuple[str, str]]:
    """(user_id, booth_id) のうち、カードのセルの source が FREE_VISIT のもの。

    `bingo_cards` / `bingo_cells` が取れていなければ空（自発訪問の信号が立たないだけ）。
    """
    owner = {str(c.get("id")): str(c.get("user_id")) for c in tables.get("bingo_cards", [])}
    out: set[tuple[str, str]] = set()
    for cell in tables.get("bingo_cells", []):
        uid = owner.get(str(cell.get("card_id")))
        if uid is None:
            continue
        if VisitSource.parse(cell.get("source")) == VisitSource.FREE_VISIT:
            out.add((uid, str(cell.get("booth_id"))))
    return out


def _attributes_at_checkin(
    tables: dict[str, list[dict]],
    surveys: dict[str, Survey],
    rated: list[_RatedVisit],
    settings,
) -> dict[str, tuple[int, int]]:
    """評価済みチェックインごとの (preference_match, rating_affinity)。

    **その行より前**のチェックイン（checked_in_at → checkin_id 順）の情報だけで計算する。
    推薦は「いま知り得ること」で属性を作るので、学習側も同じ時点の情報だけを使う
    （未来の評価を使うと、推薦時には再現できない属性で規則を学ぶことになる）。
    計算は `features.history_signals` を推薦時と共有する。
    """
    participants = {r.user_id for r in rated}
    booth_cat = {str(b.get("id")): b.get("category_id") for b in tables.get("booths", [])}
    free_pairs = _free_pairs(tables)
    rating_by_checkin: dict[str, dict] = {str(r.get("checkin_id")): r for r in tables.get("booth_ratings", [])}

    # 参加者ごとの時系列: (checked_in_at, checkin_id, booth_id)
    timeline: dict[str, list[tuple[str, str, str]]] = {}
    seen: set[str] = set()
    for c in tables.get("check_ins", []):
        uid, cid = str(c.get("user_id")), str(c.get("id"))
        if uid in participants:
            timeline.setdefault(uid, []).append((str(c.get("checked_in_at") or ""), cid, str(c.get("booth_id"))))
            seen.add(cid)
    for v in rated:  # check_ins に見つからない評価は、時刻不明として先頭に置く（履歴なし）
        if v.checkin_id not in seen:
            timeline.setdefault(v.user_id, []).append(("", v.checkin_id, v.booth_id))

    wanted = {v.checkin_id for v in rated}
    out: dict[str, tuple[int, int]] = {}
    for uid, events in timeline.items():
        survey = surveys.get(uid, Survey.empty())
        past: list[Visit] = []
        for _at, cid, bid in sorted(events):
            cat = booth_cat.get(bid)
            rating_row = rating_by_checkin.get(cid)
            if cid in wanted:
                free, high, low = features.history_signals(
                    past,
                    default_scale=settings.rating_scale_default,
                    high_ratio=settings.high_rating_ratio,
                    low_ratio=settings.low_rating_ratio,
                )
                out[cid] = (
                    features.preference_match(cat, survey, free),
                    features.rating_affinity(cat, high, low),
                )
            rating = _as_float(rating_row.get("rating")) if rating_row else None
            scale = rating_row.get("scale") if rating_row else None
            past.append(
                Visit(
                    booth_id=bid,
                    order=None,
                    source=VisitSource.FREE_VISIT if (uid, bid) in free_pairs else VisitSource.UNKNOWN,
                    rating=int(rating) if rating is not None else None,
                    rating_scale=int(scale) if scale else None,
                    category_id=cat,
                )
            )
    return out


def build_decision_records(snapshot_tables: dict[str, list[dict]], settings) -> list[dict]:
    """生の行から決定表のレコード（{attr: value, ..., "decision": "HIGH"/"LOW"}）を作る。

    純関数。`len()` が `decision_table_size`（評価済みチェックイン件数）に一致する。
    属性は推薦時と同じ定義（自発訪問の +1・低評価は★1 のみ・その行より前の情報だけ）。
    """
    enabled = decision_table_attributes(settings)
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
    attrs = _attributes_at_checkin(snapshot_tables, surveys, visits, settings)

    records: list[dict] = []
    for v in visits:
        pm, ra = attrs[v.checkin_id]
        vector = features.condition_vector(enabled, preference_match=pm, rating_affinity=ra)
        records.append({**vector, "decision": v.decision.value})
    return records


def base_rates(records: list[dict]) -> tuple[float | None, float | None]:
    """決定表全体の (HIGH の割合, LOW の割合)。品質ゲートのリフト条件の比較基準。

    HIGH = 高評価（既定は★4）、LOW = それ以外（★1〜3）。空なら (None, None)。
    """
    n = len(records)
    if n == 0:
        return None, None
    high = sum(1 for r in records if r.get("decision") == DecisionClass.HIGH.value)
    return high / n, (n - high) / n


def build_serendipity_rules(
    snapshot_tables: dict[str, list[dict]], settings
) -> tuple[tuple[SerRule, ...], int]:
    """関心外ブースへの評価だけから、セレンディピティ規則を作る。純関数。

    戻り値は (規則, 規則の材料にできた関心外の評価の件数)。
    関心外 = `features.interest_match` が MISMATCH（評価者の事前アンケートに照らして）。
    関心一致 (MATCH/PARTIAL) や判定不能 (UNKNOWN) の評価は材料にしない。
    ただし「そのカテゴリ自体の★4率」は、比較基準として**全員の評価**から求める（人気カテゴリを拾わないため）。
    条件は 第1希望 × ブースのカテゴリ の 2 つだけ。性別・年代などの個人属性は使わない。
    """
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
    observations: list[SerObservation] = []
    category_ratings: list[tuple[str, bool]] = []
    for v in visits:
        if v.category_id is None:
            continue
        high = v.decision == DecisionClass.HIGH
        category_ratings.append((v.category_id, high))
        survey = surveys.get(v.user_id, Survey.empty())
        if survey.top_interest_category is None:
            continue
        if features.interest_match(v.category_id, survey) != InterestMatch.MISMATCH:
            continue
        observations.append(SerObservation(v.user_id, survey.top_interest_category, v.category_id, high))
    rules = build_ser_rules(
        observations,
        category_ratings,
        min_rows=settings.ser_min_rows,
        smin=settings.ser_smin,
        min_persons=settings.ser_min_persons,
        min_p=settings.ser_min_p,
        lift=settings.ser_lift,
    )
    return rules, len(observations)


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
    table = DecisionTable.from_records(decision_table_attributes(settings), records)
    approx = approximate(table, settings.drsa_consistency)
    ruleset = generate_rules(
        table, min_support=settings.min_support, consistency_level=settings.drsa_consistency
    )
    built_at = getattr(snapshot, "built_at", None) or datetime.now(timezone.utc)
    rate_high, rate_low = base_rates(records)

    # セレンディピティ規則。失敗しても DRSA 側の更新は止めない（前回の規則を保持する）。
    new_ser: tuple[SerRule, ...] | None
    try:
        new_ser, ser_rows = build_serendipity_rules(tables, settings)
        ser_rules = new_ser
    except Exception as exc:  # pragma: no cover - 防御
        jsonl.emit("snapshot", {"ok": True, "ser_failed": repr(exc)})
        new_ser, ser_rows = None, -1
        ser_rules = rule_cache.ser_rules

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

    # ここまでで重い計算はすべて終わっている。以降は短い差し替えだけ（途中で例外が出ても
    # 前回のキャッシュが残る）。
    rule_cache.put(
        ruleset,
        decision_table_size=len(records),
        gamma=approx.gamma,
        built_at=built_at,
        base_rate_high=rate_high,
        base_rate_low=rate_low,
        ser_rules=new_ser,
        ser_mismatch_rows=max(ser_rows, 0),
    )
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
            "base_rate_high": rate_high,
            "base_rate_low": rate_low,
            "ser_rules": len(ser_rules),
            # 規則の一覧は毎回出す（無ければ空配列）。出現・消滅の時刻を事後に復元するため (#48)。
            "ser_rule_list": [r.summary() for r in ser_rules],
            "ser_mismatch_rows": ser_rows,
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
