"""段3-b: 決定表の組み立てとバックグラウンド再生成。

docs/specs/runtime-phase-switching/10-testing.md T-11〜T-18, T-51。
純関数なので合成データで検証する。
"""

from __future__ import annotations

import time

from event_support_recommend.cache import RuleCache, SnapshotCache
from event_support_recommend.drsa import DecisionTable, generate_rules
from event_support_recommend.settings import Settings
from event_support_recommend.snapshot_build import build_decision_records, refresh_caches


def _settings(**over) -> Settings:
    return Settings(
        _env_file=None,
        enabled_attributes=["preference_match", "rating_affinity"],
        min_support=1,
        **over,
    )


def _tables(*, users, booths, check_ins, booth_ratings, surveys=()):
    return {
        "users": list(users),
        "booths": list(booths),
        "categories": [],
        "check_ins": list(check_ins),
        "booth_ratings": list(booth_ratings),
        "user_survey_answers": list(surveys),
    }


def _u(i, role="participant"):
    return {"id": f"u{i}", "role": role}


def _b(i, cat="c1", active=1, event="A"):
    return {"id": f"b{i}", "event_id": event, "category_id": cat, "is_active": active}


def _ci(cid, uid, bid, at="2026-09-01T10:00:00", event="A"):
    return {"id": cid, "user_id": uid, "booth_id": bid, "event_id": event,
            "visit_order": 1, "checked_in_at": at}


def _rt(cid, uid, bid, rating, scale=4, event="A"):
    return {"checkin_id": cid, "user_id": uid, "booth_id": bid, "event_id": event,
            "rating": rating, "scale": scale}


# --------------------------------------------------------------------------- #
# T-11: 評価が無い訪問は行にならない
# --------------------------------------------------------------------------- #
def test_t11_unrated_visit_is_not_a_row():
    tables = _tables(
        users=[_u(1)],
        booths=[_b(1), _b(2)],
        check_ins=[_ci("ci1", "u1", "b1"), _ci("ci2", "u1", "b2")],
        booth_ratings=[_rt("ci1", "u1", "b1", 4)],  # b2 は未評価
    )
    recs = build_decision_records(tables, _settings())
    assert len(recs) == 1


# --------------------------------------------------------------------------- #
# T-12: decision_table_size == 評価済みチェックイン件数
# --------------------------------------------------------------------------- #
def test_t12_size_matches_rated_checkins():
    tables = _tables(
        users=[_u(1), _u(2)],
        booths=[_b(1), _b(2), _b(3)],
        check_ins=[_ci(f"ci{i}", f"u{(i % 2) + 1}", f"b{(i % 3) + 1}", at=f"2026-09-01T10:0{i}:00")
                   for i in range(6)],
        booth_ratings=[_rt(f"ci{i}", f"u{(i % 2) + 1}", f"b{(i % 3) + 1}", 3 + (i % 2)) for i in range(6)],
    )
    recs = build_decision_records(tables, _settings())
    rc, sc = RuleCache(), SnapshotCache()

    class Snap:
        built = True
        built_at = None
        tables = None

    Snap.tables = tables
    refresh_caches(Snap, settings=_settings(), rule_cache=rc, snapshot_cache=sc)
    assert rc.decision_table_size == len(recs)
    assert sc.get().decision_table_size == len(recs)


# --------------------------------------------------------------------------- #
# T-13: role <> 'participant' を除外
# --------------------------------------------------------------------------- #
def test_t13_non_participant_excluded():
    tables = _tables(
        users=[_u(1, role="exhibitor"), _u(2, role="staff"), _u(3, "participant")],
        booths=[_b(1)],
        check_ins=[_ci("a", "u1", "b1"), _ci("b", "u2", "b1"), _ci("c", "u3", "b1")],
        booth_ratings=[_rt("a", "u1", "b1", 4), _rt("b", "u2", "b1", 1), _rt("c", "u3", "b1", 3)],
    )
    assert len(build_decision_records(tables, _settings())) == 1


# --------------------------------------------------------------------------- #
# T-14: is_active = 0 を除外
# --------------------------------------------------------------------------- #
def test_t14_inactive_booth_excluded():
    tables = _tables(
        users=[_u(1)],
        booths=[_b(1, active=1), _b(2, active=0)],
        check_ins=[_ci("a", "u1", "b1"), _ci("b", "u1", "b2")],
        booth_ratings=[_rt("a", "u1", "b1", 4), _rt("b", "u1", "b2", 4)],
    )
    assert len(build_decision_records(tables, _settings())) == 1


# --------------------------------------------------------------------------- #
# T-15: 他イベントの行が混ざらない
# --------------------------------------------------------------------------- #
def test_t15_other_event_rows_do_not_leak():
    # data/ の SQL がイベントで絞るので、スナップショットには対象イベントの booths だけが入る。
    # 他イベントのブースへの評価は「アクティブブースに無い」ため行にならない。
    tables = _tables(
        users=[_u(1)],
        booths=[_b(1, event="A")],  # イベント A のブースのみ（SQL 絞り込み後）
        check_ins=[_ci("a", "u1", "b1", event="A"), _ci("x", "u1", "b99", event="B")],
        booth_ratings=[_rt("a", "u1", "b1", 4, event="A"), _rt("x", "u1", "b99", 4, event="B")],
    )
    recs = build_decision_records(tables, _settings())
    assert len(recs) == 1


# --------------------------------------------------------------------------- #
# T-16: 同一参加者 × 同一ブースの重複が畳まれる
# --------------------------------------------------------------------------- #
def test_t16_duplicate_pair_folded():
    tables = _tables(
        users=[_u(1)],
        booths=[_b(1)],
        check_ins=[_ci("a", "u1", "b1", at="2026-09-01T10:00:00"),
                   _ci("b", "u1", "b1", at="2026-09-01T11:00:00")],
        booth_ratings=[_rt("a", "u1", "b1", 1), _rt("b", "u1", "b1", 4)],
    )
    recs = build_decision_records(tables, _settings())
    assert len(recs) == 1
    assert recs[0]["decision"] == "HIGH"  # 最後（11:00）の評価が残る


# --------------------------------------------------------------------------- #
# T-17: 条件属性は ENABLED_ATTRIBUTES の2個だけ
# --------------------------------------------------------------------------- #
def test_t17_condition_attributes_are_exactly_enabled_two():
    tables = _tables(
        users=[_u(1)],
        booths=[_b(1)],
        check_ins=[_ci("a", "u1", "b1")],
        booth_ratings=[_rt("a", "u1", "b1", 4)],
    )
    rec = build_decision_records(tables, _settings())[0]
    assert set(rec) == {"preference_match", "rating_affinity", "decision"}


# --------------------------------------------------------------------------- #
# T-18: 決定表 → generate_rules が drsa/ の結果と矛盾しない・入力順non依存
# --------------------------------------------------------------------------- #
def test_t18_generate_rules_is_order_independent():
    tables = _tables(
        users=[_u(i) for i in range(1, 9)],
        booths=[_b(1, "c1"), _b(2, "c2"), _b(3, "c3")],
        check_ins=[_ci(f"ci{i}", f"u{(i % 8) + 1}", f"b{(i % 3) + 1}", at=f"2026-09-01T10:{i:02d}:00")
                   for i in range(24)],
        booth_ratings=[_rt(f"ci{i}", f"u{(i % 8) + 1}", f"b{(i % 3) + 1}", 4 if i % 2 else 1)
                       for i in range(24)],
    )
    s = _settings()
    recs = build_decision_records(tables, s)
    names = list(s.enabled_attributes)
    r1 = generate_rules(DecisionTable.from_records(names, recs),
                        min_support=s.min_support, consistency_level=s.drsa_consistency)
    r2 = generate_rules(DecisionTable.from_records(names, list(reversed(recs))),
                        min_support=s.min_support, consistency_level=s.drsa_consistency)
    assert [r.id for r in r1.rules] == [r.id for r in r2.rules]


# --------------------------------------------------------------------------- #
# T-51: 本番相当（数百行）で1周が SNAPSHOT_TTL_SEC を超えない
# --------------------------------------------------------------------------- #
def test_t51_full_cycle_well_within_ttl():
    n_users, n_booths = 60, 30
    users = [_u(i) for i in range(n_users)]
    booths = [_b(i, f"c{i % 6}") for i in range(n_booths)]
    check_ins, ratings = [], []
    k = 0
    for ui in range(n_users):
        for bj in range((ui % 8) + 3):  # 参加者ごとに 3〜10 件
            bid = (ui + bj) % n_booths
            cid = f"ci{k}"
            check_ins.append(_ci(cid, f"u{ui}", f"b{bid}", at=f"2026-09-01T10:{k % 60:02d}:00"))
            ratings.append(_rt(cid, f"u{ui}", f"b{bid}", (k % 4) + 1))
            k += 1
    tables = _tables(users=users, booths=booths, check_ins=check_ins, booth_ratings=ratings)
    assert k >= 300

    class Snap:
        built = True
        built_at = None

    Snap.tables = tables
    rc, sc = RuleCache(), SnapshotCache()
    started = time.monotonic()
    refresh_caches(Snap, settings=_settings(), rule_cache=rc, snapshot_cache=sc)
    elapsed = time.monotonic() - started
    assert elapsed < 30.0  # 300s の TTL に対して十分速い
    assert rc.decision_table_size == k
    assert sc.ready


# --------------------------------------------------------------------------- #
# 取得不能なら何もしない（前回キャッシュを保持）
# --------------------------------------------------------------------------- #
def test_unavailable_snapshot_keeps_previous_cache():
    rc, sc = RuleCache(), SnapshotCache()
    from event_support_recommend.drsa import RuleSet

    rc.put(RuleSet(rules=(), consistency_level=0.8, min_support=1),
           decision_table_size=42, gamma=1.0)

    class Snap:
        built = False
        tables = {}

    refresh_caches(Snap, settings=_settings(), rule_cache=rc, snapshot_cache=sc)
    assert rc.decision_table_size == 42  # 変わっていない
    assert sc.ready is False
