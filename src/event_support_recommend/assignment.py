"""assigned の選抜 — 戦略の外側の共通処理 (docs/specs/04-strategies.md §6)。

1. score 降順に並べる
2. 同点は visitor_count 昇順 → シード付き乱数 (S-5)
3. 上位 cell_count 件を取る
4. exclude_booth_ids・重複を除く
足りなくても無理に埋めない (O-4)。
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from .models import InterestMatch, ScoredBooth
from .rst import SerRule
from .strategies.base import seed_for

_VALID_CELL_COUNTS = (2, 4, 6)
# 関心外枠のフォールバックで「同点」とみなす幅（浮動小数の丸め差だけを吸収する）
_SCORE_EPS = 1e-9


def round_cell_count(cell_count: int) -> int:
    """2 / 4 / 6 以外は最も近い有効値へ丸める (docs/specs/07-testing.md §3)。"""
    if cell_count <= 2:
        return 2
    if cell_count >= 6:
        return 6
    return min(_VALID_CELL_COUNTS, key=lambda v: (abs(v - cell_count), v))


def _visitor_count(s: ScoredBooth) -> int:
    raw = s.attributes.get("raw", {}) if isinstance(s.attributes, dict) else {}
    v = raw.get("visitor_count")
    return int(v) if isinstance(v, (int, float)) else 0


def _dedupe(scored: Iterable[ScoredBooth], exclude: set[str]) -> list[ScoredBooth]:
    seen: set[str] = set()
    out: list[ScoredBooth] = []
    for s in scored:
        if s.booth_id in exclude or s.booth_id in seen:
            continue
        seen.add(s.booth_id)
        out.append(s)
    return out


def rank_pool(
    scored: Sequence[ScoredBooth],
    *,
    user_id: str,
    unlock_context: str,
    exclude_booth_ids: Iterable[str] = (),
) -> list[ScoredBooth]:
    """候補全体を確定順に並べる（assigned 抽出前の共通ランキング）。"""
    pool = _dedupe(scored, set(exclude_booth_ids))
    rng = random.Random(seed_for(user_id, unlock_context))
    jitter = {s.booth_id: rng.random() for s in sorted(pool, key=lambda s: s.booth_id)}
    pool.sort(key=lambda s: (-s.score, _visitor_count(s), jitter[s.booth_id]))
    return pool


def _apply_category_cap(
    ranked: Sequence[ScoredBooth], cap: int
) -> list[ScoredBooth]:
    if cap <= 0:
        return list(ranked)
    counts: dict[str, int] = {}
    out: list[ScoredBooth] = []
    for s in ranked:
        cat = None
        if isinstance(s.attributes, dict):
            cat = s.attributes.get("raw", {}).get("category_id")
        key = str(cat)
        if counts.get(key, 0) >= cap:
            continue
        counts[key] = counts.get(key, 0) + 1
        out.append(s)
    return out


def select_assigned(
    scored: Sequence[ScoredBooth],
    *,
    cell_count: int,
    user_id: str,
    unlock_context: str,
    exclude_booth_ids: Iterable[str] = (),
    max_per_category: int = 0,
) -> list[ScoredBooth]:
    """マスに載せるぶんを選ぶ。分割（実験）を使わない通常経路。"""
    k = round_cell_count(cell_count)
    ranked = rank_pool(
        scored, user_id=user_id, unlock_context=unlock_context, exclude_booth_ids=exclude_booth_ids
    )
    ranked = _apply_category_cap(ranked, max_per_category)
    return ranked[:k]


def split_assigned(
    arm_a_scored: Sequence[ScoredBooth],
    arm_b_scored: Sequence[ScoredBooth],
    *,
    cell_count: int,
    user_id: str,
    unlock_context: str,
    arm_a: str,
    arm_b: str,
    exclude_booth_ids: Iterable[str] = (),
) -> list[ScoredBooth]:
    """参加者内ランダム化 (docs/specs/09-research-design.md §2)。

    1回の解放の枠を2つの戦略で半々に分ける。各戦略は自分の枠数ぶんだけ上位を取り、
    両者が同じブースを選んだら先に確定したほうが取り、他方は次点へ送る。
    割付（どちらの戦略が選んだか）だけを統制する。

    **品質ゲート通過後にのみ呼ばれる（X-1）。呼び出し側の責務。**
    """
    k = round_cell_count(cell_count)
    per_arm = k // 2
    split_seed = str(seed_for(user_id, f"{unlock_context}|split"))

    excl = set(exclude_booth_ids)
    ranked_a = rank_pool(
        arm_a_scored, user_id=user_id, unlock_context=f"{unlock_context}|A", exclude_booth_ids=excl
    )
    ranked_b = rank_pool(
        arm_b_scored, user_id=user_id, unlock_context=f"{unlock_context}|B", exclude_booth_ids=excl
    )

    taken: set[str] = set()
    picks: list[ScoredBooth] = []
    ia = ib = 0
    # A, B, A, B ... の順に1枠ずつ確定させる（枠数が偏らない）
    for turn in range(per_arm * 2):
        want_a = turn % 2 == 0
        src, idx, arm = (ranked_a, ia, arm_a) if want_a else (ranked_b, ib, arm_b)
        while idx < len(src) and src[idx].booth_id in taken:
            idx += 1
        if idx < len(src):
            chosen = src[idx]
            taken.add(chosen.booth_id)
            _tag_arm(chosen, arm, split_seed)
            picks.append(chosen)
            idx += 1
        if want_a:
            ia = idx
        else:
            ib = idx
    return picks


def _tag_arm(s: ScoredBooth, arm: str, split_seed: str) -> None:
    if isinstance(s.attributes, dict):
        s.attributes["arm"] = arm
        s.attributes["split_seed"] = split_seed
        s.attributes["strategy"] = arm
    if isinstance(s.reason, dict):
        s.reason["arm"] = arm


# --------------------------------------------------------------------------- #
# 関心外枠・セレンディピティ規則・探索 (docs/specs/04-strategies.md §6, ADR 0010)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Pick:
    """マス 1 つぶんの選抜結果。`tag` はそのマスを選んだ方法。"""

    booth: ScoredBooth
    tag: str  # RANDOM / DRSA / SER / EXPLORE（SER・EXPLORE 以外は採点した戦略の名前）
    slot: str  # "MISMATCH_QUOTA"（関心外枠）/ "REST"（残り＝全体の点数順）
    ser_rule: SerRule | None = None


TAG_SER = "SER"
TAG_EXPLORE = "EXPLORE"
SLOT_MISMATCH = "MISMATCH_QUOTA"
SLOT_REST = "REST"


def mismatch_quota(k: int, quota: float) -> int:
    """関心外枠の数。2→1、4→2、6→3（quota=0.5）。四捨五入は半分を切り上げる。"""
    q = min(1.0, max(0.0, quota))
    return min(k, int(k * q + 0.5))


def _scored_by(s: ScoredBooth) -> str:
    strat = s.reason.get("strategy") if isinstance(s.reason, dict) else None
    return str(strat) if strat else "RANDOM"


def _category_of(s: ScoredBooth) -> str | None:
    raw = s.attributes.get("raw", {}) if isinstance(s.attributes, dict) else {}
    cat = raw.get("category_id")
    return None if cat is None else str(cat)


def _mismatch_fallback(remaining: Sequence[ScoredBooth], rng: random.Random) -> ScoredBooth:
    """関心外枠のフォールバック: 点数が最も高い候補の中から、visitor_count を使わず乱数で選ぶ。

    DRSA の条件属性は「関心一致 → 高評価」を学ぶので、関心外の候補は 1 回の解放の中で
    点数がほぼ全員同じになる。`rank_pool` の同点規則（visitor_count 昇順）のまま先頭を取ると
    「最も空いているブース」に偏り、ADR 0010 がランダム期から外した反人気バイアス（P-6）が
    DRSA 期に残る（ADR 0010 追記 2026-10-14）。点数に差があれば高い方を優先する。
    `remaining` は `rank_pool` の順（点数降順）で、空でないこと。
    """
    best = remaining[0].score
    tied = sorted(
        (s for s in remaining if abs(s.score - best) <= _SCORE_EPS), key=lambda s: s.booth_id
    )
    return tied[0] if len(tied) == 1 else rng.choice(tied)


def select_picks(
    scored: Sequence[ScoredBooth],
    *,
    cell_count: int,
    user_id: str,
    unlock_context: str,
    exclude_booth_ids: Iterable[str] = (),
    max_per_category: int = 0,
    top_category: str | None = None,
    ser_rules: Sequence[SerRule] = (),
    mm_quota: float = 0.5,
    explore_rate: float = 0.3,
) -> list[Pick]:
    """マスに載せるぶんを選ぶ。関心外枠（半分）→ 残りは全体の点数順。

    関心外枠の 1 マスごと（乱数のシードは user＋unlock 文脈＋"|ser"）:
      - `explore_rate`（既定 30%）は**探索**: 関心外の候補から一様にランダム → EXPLORE
      - それ以外はまず**セレンディピティ規則**に当てはまる関心外候補（規則の★4率の高い順）→ SER
      - 当てはまる候補が無ければ**採点済みの点数**が最も高い候補（DRSA 期は DRSA、ランダム期は乱数）→ DRSA / RANDOM。
        同点は visitor_count を使わず参加者ごとの乱数（シードは user＋unlock 文脈＋"|mm-tie"）で選ぶ
    関心外の候補が足りない・候補が空でも落ちない。枠が埋まらなければ全体の順位で埋め、
    それでも足りなければ埋めない (O-4)。引き継ぎ資料の擬似コードは粗いので、
    仕様の文章（上の規則）を優先して実装している。
    """
    k = round_cell_count(cell_count)
    ranked = _apply_category_cap(
        rank_pool(
            scored, user_id=user_id, unlock_context=unlock_context, exclude_booth_ids=exclude_booth_ids
        ),
        max_per_category,
    )
    mm = [s for s in ranked if s.interest_match == InterestMatch.MISMATCH]
    rule_by_key = {(r.top_category, r.booth_category): r for r in ser_rules}

    rng = random.Random(seed_for(user_id, f"{unlock_context}|ser"))
    # フォールバックの同点決め専用。`rng` から引くと探索の判定列が変わるので分ける
    tie_rng = random.Random(seed_for(user_id, f"{unlock_context}|mm-tie"))
    chosen: list[Pick] = []
    taken: set[str] = set()
    for _ in range(min(mismatch_quota(k, mm_quota), len(mm))):
        remaining = [s for s in mm if s.booth_id not in taken]
        exploring = rng.random() < min(1.0, max(0.0, explore_rate))  # 毎回引く（再現性のため）
        pick: Pick
        if exploring:
            pick = Pick(rng.choice(remaining), TAG_EXPLORE, SLOT_MISMATCH)
        else:
            best: tuple[ScoredBooth, SerRule] | None = None
            if top_category is not None:
                for s in remaining:  # remaining は順位順。同率は上位が残る
                    cat = _category_of(s)
                    rule = rule_by_key.get((top_category, cat)) if cat is not None else None
                    if rule is not None and (best is None or rule.p > best[1].p):
                        best = (s, rule)
            if best is not None:
                pick = Pick(best[0], TAG_SER, SLOT_MISMATCH, best[1])
            else:
                top = _mismatch_fallback(remaining, tie_rng)
                pick = Pick(top, _scored_by(top), SLOT_MISMATCH)
        chosen.append(pick)
        taken.add(pick.booth.booth_id)

    for s in ranked:
        if len(chosen) >= k:
            break
        if s.booth_id not in taken:
            chosen.append(Pick(s, _scored_by(s), SLOT_REST))
            taken.add(s.booth_id)
    return chosen[:k]


def tag_pick(pick: Pick) -> ScoredBooth:
    """選ばれたマスの `reason.strategy` を RANDOM / DRSA / SER / EXPLORE に更新したコピーを返す。

    契約の attributes / reason は自由な JSON（唯一の拡張口, 01-io-contract.md §3.2）。
    キーの意味は変えない（§3.3 (4)）ので、採点した戦略は `scored_by` に残し、
    `attributes` には新しいキー `slot` を足すだけにする。
    """
    s = pick.booth
    reason = dict(s.reason) if isinstance(s.reason, dict) else {}
    attributes = dict(s.attributes) if isinstance(s.attributes, dict) else {}
    reason["scored_by"] = _scored_by(s)
    reason["strategy"] = pick.tag
    reason["slot"] = pick.slot
    if pick.ser_rule is not None:
        reason["ser_rule"] = pick.ser_rule.summary()
    attributes["slot"] = {"strategy": pick.tag, "kind": pick.slot}
    return replace(s, reason=reason, attributes=attributes)
