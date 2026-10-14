"""決定表と推薦時の属性計算のずれ（issue #38）。

ずれていた3点を固定する:
  1. 関心一致度: 推薦時は自発訪問の +1 を足す。決定表も足す
  2. 低評価: 推薦時は★1 だけ。決定表も★1 だけ（★2・★3 は低評価の履歴にしない）
  3. 評価履歴の時点: 決定表の各行は、その行より**前**の情報だけを使う（未来の評価を使わない）
"""

from __future__ import annotations

from datetime import datetime, timezone

from conftest import make_request
from event_support_recommend.cache import RuleCache
from event_support_recommend.drsa import RuleSet
from event_support_recommend.engine import _build_context, _runtime_config
from event_support_recommend.models import VisitSource
from event_support_recommend.settings import Settings
from event_support_recommend.snapshot_build import (
    base_rates,
    build_decision_records,
    decision_table_attributes,
    refresh_caches,
)
from event_support_recommend.strategies.common import compute_candidate_features


def _settings(**over) -> Settings:
    return Settings(
        _env_file=None,
        enabled_attributes=over.pop("enabled_attributes", ["preference_match", "rating_affinity"]),
        min_support=1,
        **over,
    )


def _tables(*, checkins, ratings, cells=(), cards=(), surveys=None, booths=None):
    booths = booths or [
        {"id": "b1", "event_id": "A", "category_id": "cat_a", "is_active": 1},
        {"id": "b2", "event_id": "A", "category_id": "cat_a", "is_active": 1},
        {"id": "b3", "event_id": "A", "category_id": "cat_b", "is_active": 1},
    ]
    t = {
        "users": [{"id": "u1", "role": "participant"}],
        "booths": booths,
        "categories": [],
        "check_ins": [
            {"id": cid, "user_id": "u1", "booth_id": bid, "event_id": "A", "visit_order": i,
             "checked_in_at": f"2026-10-16T10:{i:02d}:00"}
            for i, (cid, bid) in enumerate(checkins)
        ],
        "booth_ratings": [
            {"checkin_id": cid, "user_id": "u1", "booth_id": bid, "event_id": "A", "rating": r, "scale": 4}
            for cid, bid, r in ratings
        ],
        "user_survey_answers": surveys
        if surveys is not None
        else [{"user_id": "u1", "event_id": "A",
               "custom_answers": {"interest_categories": ["cat_a"], "top_interest_category": "cat_a"}}],
    }
    if cards:
        t["bingo_cards"] = list(cards)
        t["bingo_cells"] = list(cells)
    return t


def _rows(tables, **kw):
    """checkin_id 順に (pm, ra, decision) を返すため、評価済み 1 行 = 1 レコードで読む。"""
    return build_decision_records(tables, _settings(**kw))


# --- 3. 未来の評価を使わない ------------------------------------------------
def test_row_does_not_use_later_ratings():
    # c1(b1,cat_a) → ★4。c2(b2,cat_a) はその後。
    tables = _tables(
        checkins=[("c1", "b1"), ("c2", "b2")],
        ratings=[("c1", "b1", 4), ("c2", "b2", 3)],
    )
    r1, r2 = _rows(tables)
    # 1行目（c1）の時点では評価履歴が無い → rating_affinity は 2（情報なし）
    assert r1["rating_affinity"] == 2
    # 2行目（c2）は c1 の★4（同カテゴリ）を知っている → 3
    assert r2["rating_affinity"] == 3


def test_first_row_ignores_a_later_low_rating():
    tables = _tables(
        checkins=[("c1", "b1"), ("c2", "b2")],
        ratings=[("c1", "b1", 3), ("c2", "b2", 1)],
    )
    r1, _ = _rows(tables)
    assert r1["rating_affinity"] == 2  # 後の★1 は見えない


# --- 2. 低評価は★1 のみ ----------------------------------------------------
def test_low_rating_history_is_star_one_only():
    for star, expected in [(1, 1), (2, 2), (3, 2)]:
        tables = _tables(
            checkins=[("c1", "b1"), ("c2", "b2")],
            ratings=[("c1", "b1", star), ("c2", "b2", 4)],
        )
        _, r2 = _rows(tables)
        assert r2["rating_affinity"] == expected, star


# --- 1. 自発訪問の +1 ------------------------------------------------------
def test_free_visit_adds_one_to_preference_match():
    cards = [{"id": "card1", "event_id": "A", "user_id": "u1"}]
    cells_free = [{"card_id": "card1", "booth_id": "b3", "source": "FREE_VISIT"}]
    cells_pre = [{"card_id": "card1", "booth_id": "b3", "source": "PRESURVEY"}]
    base = dict(
        checkins=[("c1", "b3"), ("c2", "b1")],
        ratings=[("c2", "b1", 4)],
    )
    # b3 を自由に訪問したあと、cat_a（第1希望, D=2）の b1 を評価 → B は cat_a 一致ではないので 2
    assert _rows(_tables(**base, cards=cards, cells=cells_free))[0]["preference_match"] == 2
    # cat_a の自由訪問が先にあれば B=1 → 3
    t = _tables(
        checkins=[("c0", "b2"), ("c2", "b1")],
        ratings=[("c2", "b1", 4)],
        cards=cards,
        cells=[{"card_id": "card1", "booth_id": "b2", "source": "FREE_VISIT"}],
    )
    assert _rows(t)[0]["preference_match"] == 3
    # 同じ訪問でも事前推薦マス (PRESURVEY) なら B は立たない（自己増幅の防止）
    t = _tables(
        checkins=[("c0", "b2"), ("c2", "b1")],
        ratings=[("c2", "b1", 4)],
        cards=cards,
        cells=[{"card_id": "card1", "booth_id": "b2", "source": "PRESURVEY"}],
    )
    assert _rows(t)[0]["preference_match"] == 2
    assert _rows(_tables(**base, cards=cards, cells=cells_pre))[0]["preference_match"] == 2


def test_recommend_cell_visit_is_not_a_free_signal_on_both_sides():
    """推薦マス (RECOMMEND) への訪問は行動信号 B に入らない。決定表と推薦時で一致する (F-2)。

    サーバーは推薦マスへの訪問を source="RECOMMEND" で送る。推薦エンジンはこれを
    VisitSource.UNKNOWN として扱い、FREE_VISIT と数えない。
    """
    cards = [{"id": "card1", "event_id": "A", "user_id": "u1"}]
    t = _tables(
        checkins=[("c0", "b2"), ("c2", "b1")],
        ratings=[("c2", "b1", 4)],
        cards=cards,
        cells=[{"card_id": "card1", "booth_id": "b2", "source": "RECOMMEND"}],
    )
    assert _rows(t)[0]["preference_match"] == 2  # cat_a の訪問でも B は立たない

    def feats_with(source):
        req = make_request(
            user_id="u1",
            candidate_booths=[{"booth_id": "b1", "category_id": "cat_a", "visitor_count": 1}],
            visited_booths=[{"booth_id": "b2", "visit_order": 1, "source": source, "category_id": "cat_a"}],
            rating_scale=4,
        )
        ctx = _build_context(req, _runtime_config(_settings()), datetime.now(timezone.utc))
        expected = {"RECOMMEND": VisitSource.UNKNOWN, "PRESURVEY": VisitSource.PRESURVEY,
                    "FREE_VISIT": VisitSource.FREE_VISIT}[source]
        assert ctx.participant.visits[0].source == expected
        return compute_candidate_features(ctx)["b1"].preference_match

    assert feats_with("RECOMMEND") == 2
    assert feats_with("PRESURVEY") == 2
    assert feats_with("FREE_VISIT") == 3  # 対照: 自分で選んだ訪問なら +1


def test_missing_bingo_tables_degrade_to_no_free_signal():
    t = _tables(checkins=[("c0", "b2"), ("c2", "b1")], ratings=[("c2", "b1", 4)])
    assert "bingo_cells" not in t
    assert _rows(t)[0]["preference_match"] == 2  # 落ちない。自発訪問の信号が立たないだけ


# --- 同じ参加者・同じ時点で、推薦時と決定表の属性値が一致する ----------------
def test_decision_table_matches_recommendation_time_attributes():
    cards = [{"id": "card1", "event_id": "A", "user_id": "u1"}]
    cells = [
        {"card_id": "card1", "booth_id": "b2", "source": "FREE_VISIT"},
        {"card_id": "card1", "booth_id": "b3", "source": "PRESURVEY"},
    ]
    checkins = [("c1", "b2"), ("c2", "b3"), ("c3", "b1")]
    ratings = [("c1", "b2", 2), ("c2", "b3", 4), ("c3", "b1", 3)]
    tables = _tables(checkins=checkins, ratings=ratings, cards=cards, cells=cells)
    settings = _settings()
    recs = build_decision_records(tables, settings)
    assert len(recs) == 3

    # c3（b1, cat_a）の時点 = c1・c2 まで訪問済みの参加者への推薦を、実際の推薦経路で再現する。
    req = make_request(
        user_id="u1",
        candidate_booths=[{"booth_id": "b1", "category_id": "cat_a", "visitor_count": 1}],
        visited_booths=[
            {"booth_id": "b2", "visit_order": 1, "source": "FREE_VISIT", "rating": 2, "rating_scale": 4,
             "category_id": "cat_a"},
            {"booth_id": "b3", "visit_order": 2, "source": "PRESURVEY", "rating": 4, "rating_scale": 4,
             "category_id": "cat_b"},
        ],
        rating_scale=4,
    )
    ctx = _build_context(req, _runtime_config(settings), datetime.now(timezone.utc))
    feats = compute_candidate_features(ctx)["b1"]
    assert recs[2]["preference_match"] == feats.preference_match
    assert recs[2]["rating_affinity"] == feats.rating_affinity
    # 値そのものも確認: D=2、cat_a の自由訪問 b2 で B=1 → 3。b2 は★2 なので低評価ではない → 2
    assert (feats.preference_match, feats.rating_affinity) == (3, 2)


# --- exploration_disposition ------------------------------------------------
def test_exploration_disposition_is_not_in_decision_table_but_does_not_empty_it():
    s = _settings(enabled_attributes=["preference_match", "rating_affinity", "exploration_disposition"])
    assert decision_table_attributes(s) == ["preference_match", "rating_affinity"]
    t = _tables(checkins=[("c1", "b1")], ratings=[("c1", "b1", 4)])
    recs = build_decision_records(t, s)
    assert len(recs) == 1 and "exploration_disposition" not in recs[0]


# --- 基準率 -----------------------------------------------------------------
def test_base_rates():
    assert base_rates([]) == (None, None)
    recs = [{"decision": "HIGH"}] * 1 + [{"decision": "LOW"}] * 3
    assert base_rates(recs) == (0.25, 0.75)


def test_refresh_caches_stores_base_rates():
    tables = _tables(
        checkins=[("c1", "b1"), ("c2", "b2"), ("c3", "b3")],
        ratings=[("c1", "b1", 4), ("c2", "b2", 2), ("c3", "b3", 1)],
    )

    class Snap:
        built = True
        built_at = None

    Snap.tables = tables
    from event_support_recommend.cache import SnapshotCache

    rc = RuleCache()
    refresh_caches(Snap, settings=_settings(), rule_cache=rc, snapshot_cache=SnapshotCache())
    high, low = rc.base_rates
    assert high == 1 / 3 and low == 2 / 3
    assert rc.snapshot_state()["base_rate_high"] == 1 / 3


def test_rule_cache_base_rates_default_none():
    rc = RuleCache()
    assert rc.base_rates == (None, None)
    rc.put(RuleSet(rules=(), consistency_level=0.6, min_support=5), decision_table_size=0, gamma=0.0)
    assert rc.base_rates == (None, None)


# --- 任意テーブル（bingo_*）が取れなくてもスナップショットは成立する --------------
def test_optional_tables_failure_does_not_fail_snapshot():
    from event_support_recommend.data.proxy import ProxyError
    from event_support_recommend.data.repository import ProxySnapshotRepository

    class Client:
        configured = True

        def select(self, table, sql, params):
            if table.startswith("bingo_"):
                raise ProxyError(f"proxy returned an error for {table!r}")
            return []

    snap = ProxySnapshotRepository(Client(), default_event_id="A").fetch()
    assert snap.built is True
    assert "bingo_cells" not in snap.tables and "booths" in snap.tables
