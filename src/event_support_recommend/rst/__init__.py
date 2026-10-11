"""rst/ — 古典ラフ集合（等価クラスの集計）によるセレンディピティ規則。純粋。

`drsa/` と同列。DB も HTTP も `features/`・`drsa/` も知らない。標準ライブラリだけで動く
(docs/specs/05-drsa.md §8, docs/decisions/adrs/0010-…)。

DRSA が「関心が合うほど高評価」という順序の規則を学ぶのに対し、こちらは
**関心外ブースへの評価だけ**から「第1希望 × ブースのカテゴリ → ★4」という順序の無い規則を作る。
同じ条件（等価クラス）に属する評価をまとめ、★4 の率が高く、かつ**そのカテゴリ自体の★4率**より
十分高いグループだけを規則にする。
"""

from .equivalence import ClassStats, equivalence_classes
from .ser_rules import SerObservation, SerRule, build_ser_rules, smoothed_rate

__all__ = [
    "ClassStats",
    "equivalence_classes",
    "SerObservation",
    "SerRule",
    "build_ser_rules",
    "smoothed_rate",
]
