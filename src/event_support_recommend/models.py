"""層3 — 内部ドメインモデル (docs/specs/01-io-contract.md §2.3)。

層1(リクエスト)と層2(スナップショット)の由来を隠蔽する。戦略はこれだけを見る。
HTTP も SQL も知らない素の dataclass。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# --- 契約由来の enum 相当（値の綴りは契約に合わせる）---


class Phase(str, Enum):
    COVERAGE = "COVERAGE"
    SIMILARITY = "SIMILARITY"
    DRSA = "DRSA"


class VisitSource(str, Enum):
    """bingo_cells.source。行動由来の選好は FREE_VISIT のみから作る
    (docs/specs/02-features.md §3.1)。"""

    FREE_VISIT = "FREE_VISIT"
    PRESURVEY = "PRESURVEY"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, raw: object) -> "VisitSource":
        if isinstance(raw, str):
            key = raw.strip().upper()
            for m in cls:
                if m.value == key:
                    return m
        return cls.UNKNOWN


class InterestMatch(str, Enum):
    MATCH = "MATCH"
    PARTIAL = "PARTIAL"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"


class DecisionClass(str, Enum):
    """決定クラス。LOW < HIGH の2クラス (docs/specs/05-drsa.md §1)。"""

    LOW = "LOW"
    HIGH = "HIGH"


# --- 層3 本体 ---


@dataclass(frozen=True)
class Visit:
    booth_id: str
    order: int | None
    source: VisitSource
    rating: int | None
    rating_scale: int | None
    category_id: str | None = None


@dataclass(frozen=True)
class Survey:
    """事前アンケートの平坦化済み辞書のドメイン表現。

    未回答は `answered=False`。空 dict / None はどちらも未回答扱い
    (docs/specs/02-features.md §1.2, docs/rules/coding.md)。
    """

    answered: bool
    interest_categories: tuple[str, ...] = ()
    top_interest_category: str | None = None
    age_range: str | None = None
    occupation: str | None = None
    gender: str | None = None
    exploration_disposition: int | None = None  # 1..3、無ければ None

    @classmethod
    def empty(cls) -> "Survey":
        return cls(answered=False)


@dataclass(frozen=True)
class CandidateBooth:
    booth_id: str
    category_id: str | None
    visitor_count: int
    is_active: bool = True


@dataclass(frozen=True)
class Participant:
    user_id: str
    survey: Survey
    visits: tuple[Visit, ...]
    # 参加者自身の評価履歴（visited_booths 由来）。カテゴリ付き。
    ratings: tuple[Visit, ...] = ()


@dataclass(frozen=True)
class RequestContext:
    user_id: str
    cell_count: int
    exclude_booth_ids: frozenset[str]
    candidates: tuple[CandidateBooth, ...]
    received_at: datetime
    rating_scale: int | None = None
    unlock_context: str = ""  # シード用。position 範囲や解放回など


@dataclass(frozen=True)
class DrsaRuleView:
    """規則の要約ビュー（reason に載せる分）。本体は drsa/ 側の型。"""

    id: str
    conclusion: str  # ">=HIGH" / "<=LOW"
    support: int
    confidence: float


@dataclass
class EventSnapshot:
    """層2。取得経路 (ADR 0002) は未決。built なら decision_table_size が数値。"""

    built: bool
    built_at: datetime | None = None
    decision_table_size: int | None = None
    participants: int = 0
    booths: int = 0
    # 段3/段4 で埋める。現状は常に空。
    decision_table: list[dict] = field(default_factory=list)

    @classmethod
    def unavailable(cls) -> "EventSnapshot":
        return cls(built=False, decision_table_size=None)


@dataclass(frozen=True)
class RuntimeConfig:
    """戦略が参照する設定のスナップショット。settings.Settings の写し。"""

    enabled_attributes: tuple[str, ...]
    w_coverage: float
    w_interest: float
    high_rating_ratio: float
    low_rating_ratio: float
    rating_scale_default: int
    drsa_coverage_floor: float
    similarity_coverage_floor: float
    max_per_category: int
    experiment_split_enabled: bool
    experiment_arm_a: str
    experiment_arm_b: str
    interest_partial: float = 0.6
    interest_mismatch: float = 0.2


@dataclass(frozen=True)
class RecommendationContext:
    request: RequestContext
    participant: Participant
    snapshot: EventSnapshot
    config: RuntimeConfig


@dataclass
class ScoredBooth:
    """戦略の出力。候補全件ぶん返す (S-1)。"""

    booth_id: str
    score: float  # 0..1、同一リクエスト内でのみ比較可能 (O-7)
    interest_match: InterestMatch
    attributes: dict
    reason: dict
