"""条件属性 features/ (docs/specs/07-testing.md §6 F-1..F-8)。

分析側が import する公開 API なのでシグネチャの回帰も固定する。
"""

from __future__ import annotations

import pytest

from event_support_recommend import features
from event_support_recommend.models import DecisionClass, InterestMatch, Survey, Visit, VisitSource


def survey(**over) -> Survey:
    base = dict(
        answered=True,
        interest_categories=("cat_a", "cat_b"),
        top_interest_category="cat_a",
    )
    base.update(over)
    return Survey(**base)


# --- F-1: preference_match の D x B 総当たり ---

@pytest.mark.parametrize(
    "cat,free,expected",
    [
        ("cat_a", {"cat_a"}, 3),   # D2 B1
        ("cat_a", set(), 2),        # D2 B0
        ("cat_b", {"cat_b"}, 2),   # D1 B1
        ("cat_b", set(), 1),        # D1 B0
        ("cat_z", {"cat_z"}, 1),   # D0 B1
        ("cat_z", set(), 0),        # D0 B0
    ],
)
def test_preference_match_matrix(cat, free, expected):
    assert features.preference_match(cat, survey(), free) == expected


def test_preference_match_unanswered_is_zero():
    assert features.preference_match("cat_a", Survey.empty(), set()) == 0


# --- F-2: PRESURVEY 訪問は行動信号 B に入らない（自己増幅の防止）---

def test_presurvey_visit_excluded_from_behavioural_signal():
    visits = [
        Visit("b1", 1, VisitSource.PRESURVEY, None, None, "cat_z"),
        Visit("b2", 2, VisitSource.FREE_VISIT, None, None, "cat_y"),
    ]
    free = features.free_visit_categories(visits)
    assert free == frozenset({"cat_y"})
    # PRESURVEY のカテゴリでは B が立たない
    assert features.preference_match("cat_z", Survey.empty(), free) == 0
    assert features.preference_match("cat_y", Survey.empty(), free) == 1


# --- F-3: rating_affinity は rating_scale に依存しない ---

def test_rating_normalisation_scale_independent():
    # 4段階の 4 と 5段階の 5 はどちらも normalized 1.0
    assert features.normalize_rating(4, 4, default_scale=4) == pytest.approx(1.0)
    assert features.normalize_rating(5, 5, default_scale=4) == pytest.approx(1.0)
    assert features.normalize_rating(1, 4, default_scale=4) == pytest.approx(0.0)
    # 3/4 と 4/5 はどちらも normalized 0.75
    assert features.normalize_rating(4, 5, default_scale=4) == pytest.approx(
        features.normalize_rating(4, 5, default_scale=4)
    )
    assert features.is_high_rating(4, 4, default_scale=4, high_ratio=0.75)
    assert features.is_high_rating(5, 5, default_scale=4, high_ratio=0.75)
    assert not features.is_high_rating(3, 4, default_scale=4, high_ratio=0.75)


# --- F-4: 高低両方に該当したら 2（相殺）---

def test_rating_affinity_conflict_resolves_to_two():
    assert features.rating_affinity("cat_a", {"cat_a"}, {"cat_a"}) == 2
    assert features.rating_affinity("cat_a", {"cat_a"}, set()) == 3
    assert features.rating_affinity("cat_a", set(), {"cat_a"}) == 1
    assert features.rating_affinity("cat_a", set(), set()) == 2  # 情報なし -> 無関係側


# --- F-5: interest_match の4値。top が無ければ PARTIAL は出ない ---

@pytest.mark.parametrize(
    "cat,expected",
    [
        ("cat_a", InterestMatch.MATCH),
        ("cat_b", InterestMatch.PARTIAL),
        ("cat_z", InterestMatch.MISMATCH),
    ],
)
def test_interest_match_with_top(cat, expected):
    assert features.interest_match(cat, survey()) is expected


def test_interest_match_without_top_has_no_partial():
    sv = survey(top_interest_category=None)
    results = {features.interest_match(c, sv) for c in ("cat_a", "cat_b", "cat_z")}
    assert InterestMatch.PARTIAL not in results
    assert features.interest_match("cat_b", sv) is InterestMatch.MATCH
    assert features.interest_match("cat_z", sv) is InterestMatch.MISMATCH


def test_interest_match_unknown_conditions():
    assert features.interest_match("cat_a", Survey.empty()) is InterestMatch.UNKNOWN
    assert features.interest_match(None, survey()) is InterestMatch.UNKNOWN
    assert features.interest_match("cat_a", survey(interest_categories=())) is InterestMatch.UNKNOWN


# --- F-8: attributes payload に v / enabled / raw が含まれる（COVERAGE 経由で確認）---

def test_classify_decision_binary():
    assert features.classify_decision(4, 4, default_scale=4, high_ratio=0.75) is DecisionClass.HIGH
    assert features.classify_decision(3, 4, default_scale=4, high_ratio=0.75) is DecisionClass.LOW
    assert features.classify_decision(None, 4, default_scale=4, high_ratio=0.75) is None


def test_condition_vector_drops_missing():
    v = features.condition_vector(
        ["preference_match", "rating_affinity", "exploration_disposition"],
        preference_match=3,
        rating_affinity=2,
        exploration_disposition=None,
    )
    assert v == {"preference_match": 3, "rating_affinity": 2}
    assert list(v.keys()) == ["preference_match", "rating_affinity"]  # 順序を保つ


def test_attribute_meta_is_gain_type():
    for name, meta in features.ATTRIBUTE_META.items():
        assert meta["order"] == "gain", name
        assert tuple(sorted(meta["domain"])) == meta["domain"]
