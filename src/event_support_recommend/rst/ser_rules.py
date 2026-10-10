"""セレンディピティ規則 (SER) — 「第1希望 × ブースのカテゴリ → ★4」。

仕様 (docs/specs/05-drsa.md §8, ADR 0010):
  - 入力は**関心外ブースへの評価だけ**。条件 = 第1希望 × ブースのカテゴリ、結果 = ★4 か
  - 関心外の評価が `min_rows` 件以上になるまで規則を作らない
  - 規則にするグループ（すべて満たす）:
      件数 >= smin / 人数 >= min_persons / ★4率 >= min_p /
      ★4率 − そのカテゴリ自体の★4率 >= lift
  - **比較基準は必ず「そのカテゴリ自体の★4率」。** 全体の★4率と比べると、誰もが★4 を付けやすい
    人気カテゴリを規則として拾ってしまう（去年の失敗の再現）。カテゴリ自体の★4率は
    関心の内外を問わず**全員の評価**から (★4数+1)/(件数+2) で平滑化して求める
  - 性別・年代などの個人属性は使わない（条件は 2 つだけ。個人特定リスクを増やさない）

純関数。入力の順序に依存しない（結果は決定的に整列する）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .equivalence import equivalence_classes

# 浮動小数の丸めで境界ちょうどの値を落とさないための許容。
_EPS = 1e-9


@dataclass(frozen=True)
class SerObservation:
    """関心外ブースへの評価 1 件。"""

    person_id: str
    top_category: str  # 評価者の第1希望
    booth_category: str  # 評価されたブースのカテゴリ
    high: bool  # ★4 か


@dataclass(frozen=True)
class SerRule:
    top_category: str
    booth_category: str
    n: int  # グループの件数
    n_high: int
    persons: int
    p: float  # グループの★4率（生）
    category_rate: float  # そのカテゴリ自体の★4率（平滑化）
    id: str

    @property
    def lift(self) -> float:
        return self.p - self.category_rate

    def summary(self) -> dict:
        """ログ・reason に残す要約（個人を特定する情報を含まない）。"""
        return {
            "id": self.id,
            "top_category": self.top_category,
            "booth_category": self.booth_category,
            "p": round(self.p, 4),
            "n": self.n,
            "persons": self.persons,
            "category_rate": round(self.category_rate, 4),
        }


def smoothed_rate(positives: int, n: int) -> float:
    """(★4数+1)/(件数+2)。件数が少ないカテゴリの率が 0 や 1 に振り切れないようにする。"""
    return (positives + 1) / (n + 2)


def _rule_id(top: str, cat: str) -> str:
    digest = hashlib.sha1(f"SER|{top}|{cat}".encode()).hexdigest()
    return f"S:{digest[:8]}"


def build_ser_rules(
    mismatch_observations: Sequence[SerObservation],
    category_ratings: Iterable[tuple[str, bool]],
    *,
    min_rows: int,
    smin: int,
    min_persons: int,
    min_p: float,
    lift: float,
) -> tuple[SerRule, ...]:
    """セレンディピティ規則を作る。

    mismatch_observations: 関心外ブースへの評価だけ（呼び出し側が絞る）
    category_ratings:      全員の評価の (ブースのカテゴリ, ★4か)。カテゴリ自体の★4率の材料
    戻り値は ★4率の高い順（同率は件数が多い順 → 条件の文字列順）で決定的。
    """
    if len(mismatch_observations) < min_rows:
        return ()

    cat_n: dict[str, int] = {}
    cat_pos: dict[str, int] = {}
    for cat, high in category_ratings:
        cat_n[cat] = cat_n.get(cat, 0) + 1
        cat_pos[cat] = cat_pos.get(cat, 0) + (1 if high else 0)

    classes = equivalence_classes(
        ((o.top_category, o.booth_category), o.person_id, o.high) for o in mismatch_observations
    )

    rules: list[SerRule] = []
    for (top, cat), st in classes.items():
        if st.n < smin or st.persons < min_persons:
            continue
        p = st.rate
        if p < min_p - _EPS:
            continue
        cat_rate = smoothed_rate(cat_pos.get(cat, 0), cat_n.get(cat, 0))
        if p - cat_rate < lift - _EPS:
            continue
        rules.append(
            SerRule(
                top_category=top,
                booth_category=cat,
                n=st.n,
                n_high=st.positives,
                persons=st.persons,
                p=p,
                category_rate=cat_rate,
                id=_rule_id(top, cat),
            )
        )
    rules.sort(key=lambda r: (-r.p, -r.n, r.top_category, r.booth_category))
    return tuple(rules)
