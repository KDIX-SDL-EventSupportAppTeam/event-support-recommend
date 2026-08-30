"""契約の写像（Pydantic）。

**HTTP 契約の正本は event-support-server の
docs/specs/bingo-dynamic-unlock/05-recommender/contract.md。本ファイルはその写しではなく、
観測した事実（docs/specs/01-io-contract.md §2.1）から起こした寛容なスキーマである。**

サーバー側の検証は寛容で、壊れた値は黙って捨てられる。こちらも「壊れていても 200 を返す」
方針で受ける（extra は許可し、未知フィールドは無視する）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_LENIENT = ConfigDict(extra="allow", populate_by_name=True)


class CandidateBoothIn(BaseModel):
    model_config = _LENIENT
    booth_id: str
    category_id: str | None = None
    visitor_count: int = 0
    is_active: bool = True


class VisitedBoothIn(BaseModel):
    model_config = _LENIENT
    booth_id: str
    order: int | None = Field(default=None, validation_alias="visit_order")
    source: str | None = None
    rating: int | None = None
    rating_scale: int | None = None
    category_id: str | None = None


class RecommendRequest(BaseModel):
    model_config = _LENIENT

    user_id: str = "unknown"
    card_id: str | None = None
    cell_count: int = 2
    exclude_booth_ids: list[str] = Field(default_factory=list)
    candidate_booths: list[CandidateBoothIn] = Field(default_factory=list)
    pre_survey: dict[str, Any] | None = None
    visited_booths: list[VisitedBoothIn] = Field(default_factory=list)
    rating_scale: int | None = None
    # 解放の文脈（シード用）。名称は契約に合わせて後日調整。無ければ空。
    unlock_context: str | None = None
    phase_hint: str | None = None

    @field_validator("cell_count", mode="before")
    @classmethod
    def _tolerant_cell_count(cls, v: object) -> int:
        """型崩れした cell_count は既定 2 へ寛容に倒す（500 を返さない方針）。
        2/4/6 以外の丸めは assignment.round_cell_count が担う。"""
        try:
            return int(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 2

    @field_validator("exclude_booth_ids", "candidate_booths", "visited_booths", mode="before")
    @classmethod
    def _none_to_empty(cls, v: object) -> object:
        return [] if v is None else v


class ScoreOut(BaseModel):
    booth_id: str
    score: float
    rank_in_event: int
    was_assigned: bool
    interest_match: str
    attributes: dict[str, Any]
    reason: dict[str, Any]


class AssignedOut(BaseModel):
    booth_id: str
    attributes: dict[str, Any]
    reason: dict[str, Any]


class RecommendResponse(BaseModel):
    # snake_case を厳守 (O-1 / C-1)。camelCase だと null 扱いで記録される。
    phase: str
    decision_table_size: int | None
    assigned: list[AssignedOut]
    scores: list[ScoreOut]
