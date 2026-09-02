"""評価の正規化とクラス分け (docs/specs/02-features.md §1.1, §3.2)。

rating_scale は可変なのでスケール比で正規化してから判定する。
4段階でも5段階でも同じ入力比なら同じ結果になること (テスト F-3)。
"""

from __future__ import annotations

from ..models import DecisionClass


def normalize_rating(rating: float | None, rating_scale: int | None, *, default_scale: int) -> float | None:
    """rating を 0.0..1.0 に正規化する。

    normalized = (rating - 1) / (rating_scale - 1)
    rating が None なら None。scale が壊れていれば default_scale を使う。
    """
    if rating is None:
        return None
    scale = rating_scale if rating_scale and rating_scale >= 2 else default_scale
    if scale < 2:
        return None
    value = (float(rating) - 1.0) / (scale - 1.0)
    return max(0.0, min(1.0, value))


def classify_decision(
    rating: float | None,
    rating_scale: int | None,
    *,
    default_scale: int,
    high_ratio: float,
) -> DecisionClass | None:
    """決定クラス。normalized >= HIGH_RATING_RATIO なら HIGH、それ以外は LOW。

    rating が無い行は決定表に載らないので None を返す
    (docs/specs/02-features.md §1.2)。
    """
    normalized = normalize_rating(rating, rating_scale, default_scale=default_scale)
    if normalized is None:
        return None
    return DecisionClass.HIGH if normalized >= high_ratio else DecisionClass.LOW


def is_high_rating(
    rating: float | None, rating_scale: int | None, *, default_scale: int, high_ratio: float
) -> bool:
    normalized = normalize_rating(rating, rating_scale, default_scale=default_scale)
    return normalized is not None and normalized >= high_ratio


def is_low_rating(
    rating: float | None, rating_scale: int | None, *, default_scale: int, low_ratio: float
) -> bool:
    normalized = normalize_rating(rating, rating_scale, default_scale=default_scale)
    return normalized is not None and normalized <= low_ratio
