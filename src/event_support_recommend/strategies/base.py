"""Strategy インターフェースと共通ユーティリティ (docs/specs/04-strategies.md §1)。

全戦略に共通する不変条件 (§0):
  S-1 候補全件にスコアを付けて返す。1件も落とさない
  S-2 人気順に退化しない（スコアと visitor_count の順位相関が正になってはならない）
  S-3 例外を投げない。情報が無ければ「無関係」側へ倒す
  S-4 HTTP も SQL も知らない
  S-5 同スコアの並べ替えは visitor_count 昇順 → シード付き乱数
"""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from ..models import RecommendationContext, ScoredBooth


class StrategyUnavailable(Exception):
    """戦略が実行条件を満たせず、退避ラダーを1段下るべきことを表す
    (docs/specs/04-strategies.md §5)。理由を `str(exc)` に持たせる。"""


@runtime_checkable
class Strategy(Protocol):
    name: str

    def recommend(self, ctx: RecommendationContext) -> list[ScoredBooth]:
        """候補全件ぶんの ScoredBooth を返す。assigned の選抜は戦略の外 (§6)。"""
        ...


def seed_for(user_id: str, unlock_context: str) -> int:
    """再現用シード。hash(user_id, unlock 文脈) で決める (docs/specs/01-io-contract.md §6)。

    グローバル乱数を使わない。同じ入力なら同じ結果、参加者ごとにばらける。
    """
    digest = hashlib.sha1(f"{user_id}|{unlock_context}".encode()).digest()
    return int.from_bytes(digest[:8], "big")
