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

from .models import ScoredBooth
from .strategies.base import seed_for

_VALID_CELL_COUNTS = (2, 4, 6)


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
