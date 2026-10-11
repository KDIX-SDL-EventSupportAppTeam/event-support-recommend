"""関心外枠・セレンディピティ規則・探索によるマスの選抜 (issue #42, ADR 0010)。"""

from __future__ import annotations

import json

import pytest

from event_support_recommend.api.schemas import RecommendRequest
from event_support_recommend.assignment import select_picks, tag_pick
from event_support_recommend.cache import RuleCache
from event_support_recommend.engine import run_recommendation
from event_support_recommend.models import InterestMatch, ScoredBooth
from event_support_recommend.rst import SerRule
from event_support_recommend.settings import Settings

M, P_, X, U = InterestMatch.MATCH, InterestMatch.PARTIAL, InterestMatch.MISMATCH, InterestMatch.UNKNOWN


def sb(bid, score, im, cat, vc=10, strat="DRSA"):
    return ScoredBooth(
        booth_id=bid,
        score=score,
        interest_match=im,
        attributes={"v": 1, "strategy": strat, "enabled": [], "condition": {},
                    "raw": {"visitor_count": vc, "category_id": cat}},
        reason={"v": 1, "strategy": strat, "rules": []},
    )


def rule(top, cat, p, n=8, persons=5, cat_rate=0.3):
    return SerRule(top, cat, n, round(n * p), persons, p, cat_rate, f"S:{top}{cat}")


def pool(n_match=6, n_mm=6):
    """関心一致は高得点、関心外は低得点。素直に点数順で選ぶと関心外は 1 つも入らない。"""
    out = [sb(f"m{i}", 0.9 - i * 0.01, M, "A") for i in range(n_match)]
    cats = ["X", "Y", "Z", "W", "V", "T"]
    out += [sb(f"x{i}", 0.3 - i * 0.01, X, cats[i % len(cats)]) for i in range(n_mm)]
    return out


def picks(scored, k, uid="u1", ctx="c", **kw):
    kw.setdefault("top_category", "A")
    return select_picks(scored, cell_count=k, user_id=uid, unlock_context=ctx, **kw)


# --- 関心外枠の数 ------------------------------------------------------------
@pytest.mark.parametrize("k,expected", [(2, 1), (4, 2), (6, 3)])
def test_mismatch_quota_is_half_of_the_cells(k, expected):
    for uid in (f"u{i}" for i in range(20)):
        got = picks(pool(), k, uid=uid)
        assert len(got) == k
        assert sum(p.booth.interest_match == X for p in got) == expected
        assert sum(p.slot == "MISMATCH_QUOTA" for p in got) == expected


def test_without_the_quota_serendipity_would_be_impossible():
    # 参考: 点数順だけで選ぶと関心外は 1 つも入らない（DRSA が関心一致に寄る問題）
    top = sorted(pool(), key=lambda s: -s.score)[:4]
    assert not any(s.interest_match == X for s in top)


# --- 探索の割合 --------------------------------------------------------------
def test_explore_share_is_about_thirty_percent():
    tags = [p.tag for i in range(600) for p in picks(pool(), 2, uid=f"user{i}", top_category=None)
            if p.slot == "MISMATCH_QUOTA"]
    assert len(tags) == 600
    share = tags.count("EXPLORE") / len(tags)
    assert 0.25 <= share <= 0.35, share
    # 規則が無く探索でもないマスは採点順（ここでは DRSA）
    assert set(tags) == {"EXPLORE", "DRSA"}


def test_explore_rate_zero_and_one():
    t0 = {p.tag for i in range(50) for p in picks(pool(), 2, uid=f"u{i}", explore_rate=0.0)}
    t1 = {p.tag for i in range(50) for p in picks(pool(), 2, uid=f"u{i}", explore_rate=1.0)
          if p.slot == "MISMATCH_QUOTA"}
    assert "EXPLORE" not in t0 and t1 == {"EXPLORE"}


def test_explore_picks_are_spread_not_a_fixed_booth():
    ids = {p.booth.booth_id for i in range(200) for p in picks(pool(), 2, uid=f"u{i}", explore_rate=1.0)
           if p.slot == "MISMATCH_QUOTA"}
    assert len(ids) == 6  # 関心外 6 つ全部に散る


# --- セレンディピティ規則 -----------------------------------------------------
def test_candidates_with_a_rule_come_first_in_descending_p():
    rules = [rule("A", "X", 0.8), rule("A", "Y", 0.6), rule("B", "Z", 0.99)]  # B の規則は A の人に無関係
    got = picks(pool(), 4, ser_rules=rules, explore_rate=0.0)
    mm = [p for p in got if p.slot == "MISMATCH_QUOTA"]
    assert [(p.tag, p.booth.booth_id) for p in mm] == [("SER", "x0"), ("SER", "x1")]  # x0=X(0.8), x1=Y(0.6)
    assert mm[0].ser_rule.id == "S:AX" and mm[1].ser_rule.id == "S:AY"


def test_without_matching_rule_falls_back_to_scored_order():
    rules = [rule("B", "X", 0.9)]  # この参加者(A)には当てはまらない
    got = picks(pool(), 4, ser_rules=rules, explore_rate=0.0)
    assert [p.tag for p in got if p.slot == "MISMATCH_QUOTA"] == ["DRSA", "DRSA"]


def test_random_phase_fallback_tag_is_random():
    scored = [sb(f"m{i}", 0.5, M, "A", strat="RANDOM") for i in range(4)] + [
        sb(f"x{i}", 0.5, X, "X", strat="RANDOM") for i in range(4)
    ]
    got = picks(scored, 2, explore_rate=0.0)
    assert [p.tag for p in got if p.slot == "MISMATCH_QUOTA"] == ["RANDOM"]


def test_ser_share_is_about_seventy_percent_when_a_rule_matches():
    rules = [rule("A", "X", 0.8)]
    tags = [p.tag for i in range(600) for p in picks(pool(), 2, uid=f"u{i}", ser_rules=rules)
            if p.slot == "MISMATCH_QUOTA"]
    assert 0.65 <= tags.count("SER") / len(tags) <= 0.75
    assert set(tags) == {"SER", "EXPLORE"}


def test_rule_without_user_top_category_is_ignored():
    got = picks(pool(), 4, ser_rules=[rule("A", "X", 0.8)], top_category=None, explore_rate=0.0)
    assert "SER" not in {p.tag for p in got}


# --- 足りない・空 ------------------------------------------------------------
def test_few_mismatch_candidates_fill_the_rest_from_overall_ranking():
    scored = pool(n_match=6, n_mm=1)
    got = picks(scored, 6)
    assert len(got) == 6
    assert sum(p.booth.interest_match == X for p in got) == 1
    assert len({p.booth.booth_id for p in got}) == 6


def test_no_mismatch_candidates_at_all():
    got = picks(pool(n_mm=0), 4)
    assert len(got) == 4 and all(p.slot == "REST" for p in got)


def test_empty_and_short_candidates_do_not_raise_or_pad():
    assert picks([], 6) == []
    assert len(picks(pool(n_match=1, n_mm=0), 6)) == 1  # 足りなくても無理に埋めない
    visited_all = picks(pool(), 4, exclude_booth_ids=[s.booth_id for s in pool()])
    assert visited_all == []


def test_excluded_and_duplicate_candidates_are_never_picked():
    scored = pool() + [sb("x0", 0.3, X, "X")]
    got = picks(scored, 6, exclude_booth_ids=["x1"], explore_rate=1.0)
    ids = [p.booth.booth_id for p in got]
    assert "x1" not in ids and len(ids) == len(set(ids))


def test_deterministic_for_same_input_and_varies_by_user():
    a = [(p.booth.booth_id, p.tag) for p in picks(pool(), 6, uid="u1")]
    assert a == [(p.booth.booth_id, p.tag) for p in picks(pool(), 6, uid="u1")]
    assert any(
        [(p.booth.booth_id, p.tag) for p in picks(pool(), 6, uid=f"u{i}")] != a for i in range(2, 12)
    )


# --- 記録 --------------------------------------------------------------------
def test_tag_pick_records_strategy_and_keeps_scoring_strategy():
    got = picks(pool(), 4, ser_rules=[rule("A", "X", 0.8)], explore_rate=0.0)
    ser = next(p for p in got if p.tag == "SER")
    t = tag_pick(ser)
    assert t.reason["strategy"] == "SER" and t.reason["scored_by"] == "DRSA"
    assert t.reason["ser_rule"] == {"id": "S:AX", "top_category": "A", "booth_category": "X",
                                    "p": 0.8, "n": 8, "persons": 5, "category_rate": 0.3}
    assert t.attributes["slot"] == {"strategy": "SER", "kind": "MISMATCH_QUOTA"}
    assert t.attributes["strategy"] == "DRSA"  # 既存キーの意味は変えない
    assert ser.booth.reason["strategy"] == "DRSA"  # 元のオブジェクトは変えない


# --- エンジン経由（契約の形・scores 全件・500 にならない）---------------------------
def _settings(**over):
    return Settings(_env_file=None, enabled_attributes=["preference_match", "rating_affinity"], **over)


def _payload(uid="u1", cell_count=4, n=10):
    return {
        "user_id": uid,
        "cell_count": cell_count,
        "candidate_booths": [
            {"booth_id": f"b{i}", "category_id": "A" if i < 4 else "XYZW"[i % 4], "visitor_count": i * 3}
            for i in range(n)
        ],
        "pre_survey": {"interest_categories": ["A"], "top_interest_category": "A"},
    }


def test_engine_records_strategy_on_every_assigned_and_returns_all_scores():
    rc = RuleCache()
    rc.put_ser((rule("A", "X", 0.8), rule("A", "Y", 0.7), rule("A", "Z", 0.6), rule("A", "W", 0.55)),
              mismatch_rows=30)
    s = _settings(explore_rate=0.0)
    resp = run_recommendation(RecommendRequest.model_validate(_payload()), settings=s, rule_cache=rc)
    assert len(resp.scores) == 10  # 候補全件
    assert len(resp.assigned) == 4
    strategies = [a.reason["strategy"] for a in resp.assigned]
    assert set(strategies) <= {"RANDOM", "DRSA", "SER", "EXPLORE"}
    assert strategies.count("SER") == 2  # 関心外枠 4→2。どちらも規則に当てはまる
    assert all(a.reason["ser_rule"]["id"].startswith("S:") for a in resp.assigned if a.reason["strategy"] == "SER")
    assigned_ids = {a.booth_id for a in resp.assigned}
    assert {x.booth_id for x in resp.scores if x.was_assigned} == assigned_ids
    # 契約の形（キー）は変わらない
    assert set(resp.model_dump()) == {"phase", "decision_table_size", "assigned", "scores"}
    assert set(resp.assigned[0].model_dump()) == {"booth_id", "score", "rank", "attributes", "reason"}


def test_engine_log_has_strategy_per_assigned(capsys):
    rc = RuleCache()
    run_recommendation(RecommendRequest.model_validate(_payload()), settings=_settings(), rule_cache=rc)
    rec = [json.loads(l) for l in capsys.readouterr().out.splitlines() if '"kind":"recommend"' in l][-1]
    assert len(rec["assigned_strategies"]) == 4
    assert {a["strategy"] for a in rec["assigned_strategies"]} <= {"RANDOM", "DRSA", "SER", "EXPLORE"}
    assert len(rec["scores"]) == 10  # scores は候補全件のまま


@pytest.mark.parametrize("n", [0, 1, 2])
def test_engine_does_not_fail_on_empty_or_tiny_candidates(n, client):
    r = client.post("/recommend/cells", json=_payload(n=n, cell_count=6))
    assert r.status_code == 200
    body = r.json()
    assert len(body["scores"]) == n and len(body["assigned"]) <= n


def test_popular_outlier_is_not_systematically_assigned_through_the_whole_pipeline():
    """関心外枠・SER・探索を通しても、人気ブースが人気だから選ばれることは無い（人気ガード）。"""
    rc = RuleCache()
    rc.put_ser((rule("A", "X", 0.8),), mismatch_rows=30)
    s = _settings()
    hits = 0
    users = 300
    for i in range(users):
        p = _payload(uid=f"u{i}", cell_count=2, n=8)
        p["candidate_booths"][5]["visitor_count"] = 9999  # b5 だけ突出して人気（category は関心外）
        resp = run_recommendation(RecommendRequest.model_validate(p), settings=s, rule_cache=rc)
        hits += any(a.booth_id == "b5" for a in resp.assigned)
    # 2 マス・8 候補。一様なら 0.25。人気順（常に選ばれる）なら 1.0
    assert hits / users <= 0.4, hits / users


def test_ops_state_counts_how_each_cell_was_chosen(client):
    client.app.state.rule_cache.put_ser((rule("A", "X", 0.8),), mismatch_rows=30)
    for i in range(30):
        client.post("/recommend/cells", json=_payload(uid=f"u{i}", cell_count=4))
    used = client.get("/ops/state").json()["ser"]["used"]
    assert used["unlocks"] == 30 and used["slots"] == 120 and used["mismatch_slots"] == 60
    assert sum(used["by_strategy"].values()) == 120
    assert used["by_strategy"]["SER"] > 0 and used["by_strategy"]["EXPLORE"] > 0
