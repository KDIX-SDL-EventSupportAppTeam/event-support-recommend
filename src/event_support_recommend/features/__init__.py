"""features/ — 条件属性の計算。★event-support-analytics が import する公開 API。

FastAPI・SQL・drsa/・strategies/ に依存しない純関数だけを置く
(docs/specs/08-architecture.md §1, docs/specs/02-features.md §7)。
シグネチャと戻り値は本リポジトリの公開 API。変更時は 02-features.md §6 のバージョニング規律に従う。
"""

from .attributes import (
    ATTRIBUTE_META,
    ATTRIBUTES_SCHEMA_VERSION,
    condition_vector,
    exploration_disposition,
    free_visit_categories,
    preference_match,
    rating_affinity,
)
from .interest_match import interest_match
from .rating import classify_decision, is_high_rating, is_low_rating, normalize_rating

__all__ = [
    "ATTRIBUTE_META",
    "ATTRIBUTES_SCHEMA_VERSION",
    "condition_vector",
    "exploration_disposition",
    "free_visit_categories",
    "preference_match",
    "rating_affinity",
    "interest_match",
    "classify_decision",
    "is_high_rating",
    "is_low_rating",
    "normalize_rating",
]
