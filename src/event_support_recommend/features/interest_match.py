"""interest_match — 契約の出力フィールド (docs/specs/02-features.md §4)。

条件属性 preference_match とは別物。混同しないこと (docs/rules/coding.md)。
セレンディピティ分析の分母を作るラベルであり、判定可能なのに UNKNOWN を返してはならない
(docs/specs/01-io-contract.md O-3)。
"""

from __future__ import annotations

from ..models import InterestMatch, Survey


def interest_match(booth_category_id: str | None, survey: Survey) -> InterestMatch:
    """(ブースのカテゴリ, 参加者の事前アンケート) から4値を判定する。

    - UNKNOWN … 未回答 / 関心分野が空 / ブースに category_id が無い
    - MATCH   … top_interest_category と一致
    - PARTIAL … interest_categories に含まれる（第1希望ではない）
    - MISMATCH… interest_categories に含まれない

    top_interest_category の設問が無い場合、PARTIAL は発生せず
    MATCH = 宣言した分野すべて になる。MISMATCH の意味は変わらない。
    """
    if not survey.answered or not survey.interest_categories or booth_category_id is None:
        return InterestMatch.UNKNOWN

    if survey.top_interest_category is not None and booth_category_id == survey.top_interest_category:
        return InterestMatch.MATCH

    if booth_category_id in survey.interest_categories:
        # top の設問がある構成では PARTIAL、無い構成では MATCH 相当。
        if survey.top_interest_category is None:
            return InterestMatch.MATCH
        return InterestMatch.PARTIAL

    return InterestMatch.MISMATCH
