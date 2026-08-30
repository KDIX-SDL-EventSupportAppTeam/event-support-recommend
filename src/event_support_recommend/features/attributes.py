"""条件属性の計算 (docs/specs/02-features.md §3)。

すべて利得型（値が大きいほど「良い」向き）の順序尺度。DRSA の優越関係に必要
(docs/specs/05-drsa.md §1)。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..models import Survey, Visit, VisitSource

# attributes ペイロードのスキーマ版 (docs/specs/02-features.md §6)。
# 属性を足す・意味を変えるときは上げ、02-features.md に旧版の定義を残す。
ATTRIBUTES_SCHEMA_VERSION = 1

# 分析側が DRSA 以外の手法へ流用するときに使う、属性の値域と順序の向き。
ATTRIBUTE_META: dict[str, dict] = {
    "preference_match": {"domain": (0, 1, 2, 3), "order": "gain"},
    "rating_affinity": {"domain": (1, 2, 3), "order": "gain"},
    "exploration_disposition": {"domain": (1, 2, 3), "order": "gain"},
}


def free_visit_categories(visits: Iterable[Visit]) -> frozenset[str]:
    """行動信号 B の材料。source = FREE_VISIT の訪問のカテゴリだけを集める。

    事前推薦マス (PRESURVEY) を除外する — こちらが推薦したブースを「その人の興味」として
    数えると推薦が自己増幅する (docs/specs/02-features.md §3.1, テスト F-2)。
    """
    return frozenset(
        v.category_id
        for v in visits
        if v.source == VisitSource.FREE_VISIT and v.category_id is not None
    )


def preference_match(
    booth_category_id: str | None,
    survey: Survey,
    free_categories: Iterable[str],
) -> int:
    """A-1 選好一致度 (0..3)。宣言 D と行動 B を足す。

        D = 2  ブースのカテゴリが top_interest_category と一致
            1  ブースのカテゴリが interest_categories に含まれる
            0  含まれない
        B = 1  ブースのカテゴリが FREE_VISIT 訪問のカテゴリと一致
            0  一致しない
        preference_match = min(D + B, 3)

    未回答は 0（信号なし）。契約表の「0 = 明確に避けている」ではなく本実装は「0 = 信号なし」
    (docs/specs/02-features.md §3.1 の意図的差分)。
    """
    if booth_category_id is None:
        return 0

    d = 0
    if survey.answered:
        if survey.top_interest_category is not None and booth_category_id == survey.top_interest_category:
            d = 2
        elif booth_category_id in survey.interest_categories:
            d = 1

    b = 1 if booth_category_id in set(free_categories) else 0
    return min(d + b, 3)


def rating_affinity(
    booth_category_id: str | None,
    high_rated_categories: Iterable[str],
    low_rated_categories: Iterable[str],
) -> int:
    """A-2 評価履歴との親和度 (1..3)。

        3  高評価を付けたブースと同カテゴリ
        2  そのカテゴリについて評価の情報が無い（既定・無関係側）
        1  低評価を付けたブースと同カテゴリ

    高評価と低評価の両方に該当したら 2（相殺, テスト F-4）。
    序盤はほとんど 2 になる。これは仕様 (docs/specs/02-features.md §3.2)。
    """
    if booth_category_id is None:
        return 2
    high = booth_category_id in set(high_rated_categories)
    low = booth_category_id in set(low_rated_categories)
    if high and low:
        return 2
    if high:
        return 3
    if low:
        return 1
    return 2


_EXPLORATION_MAP = {"low": 1, "mid": 2, "high": 3}


def exploration_disposition(survey: Survey) -> int | None:
    """A-3 探索志向 (1..3)。予備・既定は無効 (ADR 0003)。

    収集はするが ENABLED_ATTRIBUTES に入っていなければ決定表に載せない。
    """
    return survey.exploration_disposition


def condition_vector(enabled: Sequence[str], **values: int | None) -> dict[str, int]:
    """有効な条件属性だけを順序どおりに並べた辞書を返す。

    None（測定不能）の属性は落とす。DRSA へ渡すベクトルと attributes.condition の両方に使う。
    """
    out: dict[str, int] = {}
    for name in enabled:
        v = values.get(name)
        if v is not None:
            out[name] = int(v)
    return out
