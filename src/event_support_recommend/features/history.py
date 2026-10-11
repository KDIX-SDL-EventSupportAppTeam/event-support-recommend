"""参加者の訪問履歴から、条件属性の材料になる3つのカテゴリ集合を作る。

推薦時 (`strategies/common.py`) と決定表の組み立て (`snapshot_build.py`) の**両方**がこれを呼ぶ。
同じ関数を通すことで、「学習した属性」と「推薦時に使う属性」のずれを構造で防ぐ
(docs/specs/05-drsa.md §7)。ずれていた箇所は3つ:
自発訪問の +1、低評価の範囲（★1 のみ）、その行より後の評価の混入。

純関数。FastAPI・DB に依存しない（分析リポジトリが import する）。
"""

from __future__ import annotations

from collections.abc import Iterable

from ..models import Visit, VisitSource
from .rating import is_high_rating, is_low_rating


def history_signals(
    visits: Iterable[Visit],
    *,
    default_scale: int,
    high_ratio: float,
    low_ratio: float,
    request_scale: int | None = None,
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """(自発訪問のカテゴリ, 高評価カテゴリ, 低評価カテゴリ) を返す。

    `visits` には**その時点までに知り得る訪問だけ**を渡すこと。呼び出し側の責任。

    - 自発訪問 … `source = FREE_VISIT` の訪問のカテゴリ（事前推薦マスは含めない）
    - 高評価   … normalized >= high_ratio（既定 0.75 = 4段階の★4）
    - 低評価   … normalized <= low_ratio（既定 0.25 = 4段階の★1 だけ。★2・★3 は含めない）
    """
    free: set[str] = set()
    high: set[str] = set()
    low: set[str] = set()
    for v in visits:
        if v.category_id is None:
            continue
        if v.source == VisitSource.FREE_VISIT:
            free.add(v.category_id)
        if v.rating is None:
            continue
        scale = v.rating_scale or request_scale
        if is_high_rating(v.rating, scale, default_scale=default_scale, high_ratio=high_ratio):
            high.add(v.category_id)
        if is_low_rating(v.rating, scale, default_scale=default_scale, low_ratio=low_ratio):
            low.add(v.category_id)
    return frozenset(free), frozenset(high), frozenset(low)
