"""セレンディピティ規則 (issue #41, rst/)。

関心外ブースへの評価だけから「第1希望 × ブースのカテゴリ → ★4」を作る。
最重要: 比較基準は**そのカテゴリ自体の★4率**。全体の★4率と比べると人気カテゴリを拾う
（去年の失敗の再現）。
"""

from __future__ import annotations

import random

import pytest

from event_support_recommend.cache import RuleCache, SnapshotCache
from event_support_recommend.rst import SerObservation, build_ser_rules, smoothed_rate
from event_support_recommend.settings import Settings
from event_support_recommend.snapshot_build import build_serendipity_rules, refresh_caches

KW = dict(min_rows=20, smin=5, min_persons=3, min_p=0.5, lift=0.15)


def obs(top, cat, highs, persons=None):
    """highs: bool の列。persons: 評価者数（既定は件数と同じ人数）。"""
    n = len(highs)
    persons = persons or n
    return [SerObservation(f"{top}{cat}p{i % persons}", top, cat, h) for i, h in enumerate(highs)]


def filler(n=20, cat="Z", top="T"):
    """関心外の評価が min_rows に届くようにするための無関係な行（それぞれ別カテゴリ・別人で規則にならない）。"""
    return [SerObservation(f"f{i}", top, f"{cat}{i}", False) for i in range(n)]


def build(observations, cat_ratings, **over):
    return build_ser_rules(observations, cat_ratings, **{**KW, **over})


def cat_ratings_from(observations, extra=()):
    return [(o.booth_category, o.high) for o in observations] + list(extra)


# --- 基本形 -----------------------------------------------------------------
def test_rule_is_made_when_group_beats_its_own_category_rate():
    o = obs("A", "X", [True] * 5 + [False]) + filler()  # 6 件・5 人以上・★4率 0.83
    # カテゴリ X を他の人（関心一致）が評価: ★4 は 2/10 → 平滑化 (2+6... 全員分) で低い
    others = [("X", True)] * 2 + [("X", False)] * 8
    rules = build(o, cat_ratings_from(o, others))
    assert len(rules) == 1
    r = rules[0]
    assert (r.top_category, r.booth_category, r.n, r.n_high) == ("A", "X", 6, 5)
    assert r.p == pytest.approx(5 / 6)
    assert r.lift == pytest.approx(5 / 6 - smoothed_rate(7, 16))
    assert r.lift >= 0.15


def test_popular_category_yields_no_rule():
    """全員が★4 を付けやすいカテゴリ（人気カテゴリ）では規則が出ない。"""
    o = obs("A", "X", [True] * 6) + filler()  # グループは★4率 100%
    popular = [("X", True)] * 200  # だがカテゴリ X は誰が評価しても★4
    assert smoothed_rate(206, 206) > 0.99
    assert build(o, cat_ratings_from(o, popular)) == ()
    # 比較基準が全体の★4率だったら（過去の誤り）通ってしまう: 全体の★4率は低い会場
    assert 1.0 - 0.3 >= 0.15


def test_baseline_is_the_category_own_rate_not_the_overall_rate():
    o = obs("A", "X", [True] * 4 + [False] * 1) + obs("A", "Y", [True] * 4 + [False] * 1) + filler()
    ratings = cat_ratings_from(o) + [("X", True)] * 50 + [("Y", False)] * 50
    rules = {r.booth_category for r in build(o, ratings)}
    assert rules == {"Y"}  # X は人気カテゴリなので不可。Y（低評価が多いカテゴリ）の中では目立つので可


# --- 件数・人数・率・リフトの境界 -------------------------------------------
def test_needs_at_least_smin_rows():
    mk = lambda n: obs("A", "X", [True] * n, persons=3) + filler()
    others = [("X", False)] * 20
    assert build(mk(5), cat_ratings_from(mk(5), others)) != ()
    assert build(mk(4), cat_ratings_from(mk(4), others)) == ()


def test_needs_at_least_three_persons():
    others = [("X", False)] * 20
    ok = obs("A", "X", [True] * 6, persons=3) + filler()
    two = obs("A", "X", [True] * 6, persons=2) + filler()
    assert build(ok, cat_ratings_from(ok, others)) != ()
    assert build(two, cat_ratings_from(two, others)) == ()  # 1〜2 人の癖は規則にしない


def test_needs_total_mismatch_rows_at_least_min_rows():
    others = [("X", False)] * 30
    base = obs("A", "X", [True] * 6, persons=3)
    assert build(base + filler(14), cat_ratings_from(base, others)) != ()  # 20 件ちょうど
    assert build(base + filler(13), cat_ratings_from(base, others)) == ()  # 19 件


@pytest.mark.parametrize("highs,expected", [(5, True), (4, False)])  # 10 件中の★4 数。p=0.5 / 0.4
def test_min_p_boundary(highs, expected):
    o = obs("A", "X", [True] * highs + [False] * (10 - highs), persons=5) + filler()
    others = [("X", False)] * 100  # カテゴリ率はごく低い（リフトは満たす）
    assert (build(o, cat_ratings_from(o, others)) != ()) is expected


def test_lift_boundary_uses_smoothed_category_rate():
    # グループ p=0.8（4/5）。カテゴリ全体 n 件中 pos 件が★4 → 平滑化率 (pos+1)/(n+2)
    o = obs("A", "X", [True] * 4 + [False], persons=5) + filler()
    # グループ自身がカテゴリ全体に含まれる（4/5）。他の人 20 件のうち pos_other が★4
    def rules_for(pos_other):
        ratings = cat_ratings_from(o, [("X", True)] * pos_other + [("X", False)] * (20 - pos_other))
        return build(o, ratings)

    # カテゴリ率 = (4+pos+1)/(25+2)。p − 率 >= 0.15 を満たす最大の pos を探す
    def lift_ok(pos_other):
        return 0.8 - (4 + pos_other + 1) / 27 >= 0.15 - 1e-9

    for pos in range(0, 21):
        assert (rules_for(pos) != ()) is lift_ok(pos), pos


# --- 決定性・個人属性 --------------------------------------------------------
def test_deterministic_regardless_of_input_order():
    o = (
        obs("A", "X", [True] * 6, persons=3) + obs("B", "Y", [True] * 5 + [False], persons=4)
        + obs("C", "Z", [False] * 6, persons=3) + filler()
    )
    ratings = cat_ratings_from(o, [("X", False)] * 20 + [("Y", False)] * 20)
    a = build(o, ratings)
    rng = random.Random(1)
    for _ in range(5):
        o2, r2 = o[:], ratings[:]
        rng.shuffle(o2)
        rng.shuffle(r2)
        assert build(o2, r2) == a
    assert [r.id for r in a] == [r.id for r in build(o, ratings)]
    assert len(a) == 2 and all(r.id.startswith("S:") for r in a)


def test_observation_has_no_personal_attributes():
    assert set(SerObservation.__dataclass_fields__) == {"person_id", "top_category", "booth_category", "high"}


# --- スナップショット（決定表と同じ元データ）からの生成 ----------------------------
def _tables(users, ratings_spec):
    """users: [(uid, top, interests, gender)]、ratings_spec: [(uid, booth_id, star)]。ブースは bX/bY/bA/..."""
    booths = [{"id": f"b{c}", "event_id": "E", "category_id": c, "is_active": 1} for c in "AXYZ"]
    return {
        "users": [{"id": u[0], "role": "participant"} for u in users],
        "booths": booths,
        "categories": [],
        "check_ins": [
            {"id": f"c{i}", "user_id": uid, "booth_id": bid, "event_id": "E", "visit_order": 1,
             "checked_in_at": f"2026-10-16T10:{i % 60:02d}:{i // 60:02d}"}
            for i, (uid, bid, _s) in enumerate(ratings_spec)
        ],
        "booth_ratings": [
            {"checkin_id": f"c{i}", "user_id": uid, "booth_id": bid, "event_id": "E", "rating": s, "scale": 4}
            for i, (uid, bid, s) in enumerate(ratings_spec)
        ],
        "user_survey_answers": [
            {"user_id": u[0], "event_id": "E", "gender": u[3],
             "custom_answers": {"interest_categories": u[2], "top_interest_category": u[1]}}
            for u in users
        ],
    }


def _settings(**over):
    return Settings(_env_file=None, enabled_attributes=["preference_match", "rating_affinity"], **over)


def _fan_scenario(gender="female", match_stars=4):
    """第1希望 A の 6 人が関心外 X を★4 で評価 + 関心外 Y を★1 で評価 + 関心一致 A を評価。
    第1希望 X の 10 人は X（関心一致）を低評価。"""
    users = [(f"a{i}", "A", ["A"], gender) for i in range(6)] + [(f"x{i}", "X", ["X"], "male") for i in range(10)]
    spec = []
    for i in range(6):
        spec += [(f"a{i}", "bX", 4), (f"a{i}", "bY", 1), (f"a{i}", "bA", match_stars)]
    for i in range(10):
        spec += [(f"x{i}", "bX", 1)]
    return _tables(users, spec)


def test_rules_from_snapshot_use_only_mismatch_ratings():
    rules, rows = build_serendipity_rules(_fan_scenario(), _settings())
    # 関心外: a* の bX(6) と bY(6) = 12 件。関心一致の bA(6) と x* の bX(10, 関心一致) は材料にしない
    assert rows == 12
    # 20 件未満なので規則は出ない
    assert rules == ()


def test_rules_appear_once_mismatch_rows_reach_min_rows():
    rules, rows = build_serendipity_rules(_fan_scenario(), _settings(ser_min_rows=12))
    assert rows == 12
    got = {(r.top_category, r.booth_category) for r in rules}
    assert got == {("A", "X")}  # bY は★1 ばかりで率が低い
    r = rules[0]
    assert (r.n, r.persons, r.p) == (6, 6, 1.0)
    # カテゴリ X 自体の★4率は 全員 16 件中 6 件 → (6+1)/(16+2)
    assert r.category_rate == pytest.approx(7 / 18)


def test_match_ratings_never_become_rules():
    # 関心一致 (bA) の評価がどれだけ高くても、それ自体では規則にならない
    rules, rows = build_serendipity_rules(_fan_scenario(match_stars=4), _settings(ser_min_rows=1))
    assert all(r.booth_category != "A" for r in rules)


def test_gender_and_other_personal_attributes_are_not_used():
    base = build_serendipity_rules(_fan_scenario(gender="female"), _settings(ser_min_rows=12))
    other = build_serendipity_rules(_fan_scenario(gender="その他"), _settings(ser_min_rows=12))
    assert base == other
    assert "gender" not in str(base[0][0].summary()) and "その他" not in str(other[0][0].summary())


def test_snapshot_generation_is_deterministic():
    a = build_serendipity_rules(_fan_scenario(), _settings(ser_min_rows=12))
    b = build_serendipity_rules(_fan_scenario(), _settings(ser_min_rows=12))
    assert a == b


def test_refresh_caches_stores_ser_rules_and_ops_state_shows_them(client):
    class Snap:
        built = True
        built_at = None
        tables = _fan_scenario()

    rc = client.app.state.rule_cache
    refresh_caches(Snap, settings=_settings(ser_min_rows=12), rule_cache=rc, snapshot_cache=SnapshotCache())
    ser = client.get("/ops/state").json()["ser"]
    assert ser["count"] == 1 and ser["mismatch_rows"] == 12
    assert ser["min_rows"] == 20 and ser["active"] is False  # クライアントの設定は既定の 20
    assert ser["rules"][0]["top_category"] == "A" and ser["rules"][0]["booth_category"] == "X"
    assert not any(uid in str(ser) for uid in ("a0", "a1", "x0", "user_id", "person_id"))  # 個人は出さない（人数のみ）


def test_ops_state_ser_empty_before_any_snapshot(client):
    ser = client.get("/ops/state").json()["ser"]
    assert ser["count"] == 0 and ser["mismatch_rows"] == 0 and ser["active"] is False


def _snapshot_log_lines(capsys):
    import json

    out = capsys.readouterr().out
    return [r for r in (json.loads(l) for l in out.splitlines() if l.startswith("{")) if r["kind"] == "snapshot"]


def test_snapshot_log_has_ser_rule_list_when_rules_exist(capsys):
    class Snap:
        built = True
        built_at = None
        tables = _fan_scenario()

    refresh_caches(Snap, settings=_settings(ser_min_rows=12), rule_cache=RuleCache(), snapshot_cache=SnapshotCache())
    rec = [r for r in _snapshot_log_lines(capsys) if "ser_rule_list" in r][-1]
    assert rec["ser_rules"] == 1 and len(rec["ser_rule_list"]) == 1
    item = rec["ser_rule_list"][0]
    assert set(item) == {"id", "top_category", "booth_category", "p", "n", "persons", "category_rate"}
    assert not any(uid in str(rec["ser_rule_list"]) for uid in ("a0", "a1", "x0", "user_id", "person_id"))


def test_snapshot_log_has_empty_ser_rule_list_when_no_rules(capsys):
    class Snap:
        built = True
        built_at = None
        tables = _fan_scenario()

    # ser_min_rows が材料の件数を上回れば規則は 0 本。空配列で「無かった」ことを記録する。
    refresh_caches(Snap, settings=_settings(ser_min_rows=10_000), rule_cache=RuleCache(), snapshot_cache=SnapshotCache())
    rec = [r for r in _snapshot_log_lines(capsys) if "ser_rule_list" in r][-1]
    assert rec["ser_rules"] == 0 and rec["ser_rule_list"] == []
