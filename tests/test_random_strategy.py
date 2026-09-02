"""RANDOM（下限ベースライン）も人気順退化の禁止テストを通す
(docs/decisions/adrs/0007-戦略の選択を環境変数で行う.md §2, docs/specs/07-testing.md §1)。

P-2（明確に負）は COVERAGE 限定なので対象外。P-1・P-3〜P-6 と S-1〜S-5 を固定する。
"""

from __future__ import annotations

import statistics

import pytest

from event_support_recommend.api.schemas import RecommendRequest
from event_support_recommend.cache import RuleCache
from event_support_recommend.engine import run_recommendation
from event_support_recommend.settings import Settings


@pytest.fixture
def run_random():
    settings = Settings(
        _env_file=None,
        enabled_attributes=["preference_match", "rating_affinity"],
        strategy="random",
        app_env="development",
        ops_token="",
    )

    def _run(req: RecommendRequest):
        return run_recommendation(req, settings=settings, rule_cache=RuleCache())

    return _run


def _req(uid: str, *, cell_count: int = 4) -> RecommendRequest:
    return RecommendRequest.model_validate(
        {
            "user_id": uid,
            "cell_count": cell_count,
            "candidate_booths": [
                {"booth_id": f"b{i}", "category_id": f"cat_{i % 4}", "visitor_count": v}
                for i, v in enumerate([2, 6, 11, 18, 27, 39, 52, 70, 95, 130])
            ],
            "pre_survey": {"interest_categories": ["cat_0", "cat_1"], "top_interest_category": "cat_0"},
        }
    )


def _vc(s) -> int:
    return s.attributes["raw"]["visitor_count"]


def _spearman(xs, ys) -> float:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = sum((a - mx) ** 2 for a in rx) ** 0.5
    sy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (sx * sy) if sx and sy else 0.0


def test_s1_returns_every_candidate(run_random):
    resp = run_random(_req("u1"))
    assert len(resp.scores) == 10


def test_s3_p3_outlier_popular_booth_not_first_assigned(run_random):
    req = RecommendRequest.model_validate(
        {
            "user_id": "u1",
            "cell_count": 4,
            "candidate_booths": [
                {"booth_id": "pop", "category_id": "cat_0", "visitor_count": 9999},
                {"booth_id": "b1", "category_id": "cat_0", "visitor_count": 2},
                {"booth_id": "b2", "category_id": "cat_0", "visitor_count": 4},
                {"booth_id": "b3", "category_id": "cat_0", "visitor_count": 6},
            ],
            "pre_survey": {"interest_categories": ["cat_0"], "top_interest_category": "cat_0"},
        }
    )
    # 全候補が同じ interest_match（MATCH）→ クラス内は visitor_count 昇順。人気は末尾。
    assert run_random(req).assigned[0].booth_id != "pop"


def test_p1_score_visitor_correlation_is_not_systematically_positive(run_random):
    rhos = []
    for i in range(60):
        resp = run_random(_req(f"user_{i}"))
        rhos.append(_spearman([s.score for s in resp.scores], [_vc(s) for s in resp.scores]))
    # P-1: 系統的な正の相関が無いこと。個々の参加者では正になりうる（クラス序列が
    # たまたま人気順と揃う場合）ので、平均で見る。
    assert statistics.mean(rhos) <= 0.1, statistics.mean(rhos)


def test_p4_different_participants_get_different_recommendations(run_random):
    """属性が違えば結果が違う (P-4)。候補の interest_match が複数クラスに分かれる場合。"""
    seen = {tuple(x.booth_id for x in run_random(_req(f"u{i}")).assigned) for i in range(8)}
    assert len(seen) > 1  # 全員同じにはならない


def test_single_interest_class_input_is_participant_invariant_like_coverage():
    """**既知の限界。** 候補の interest_match が1クラスしか無い入力では、
    クラス内が同点になり順序が visitor_count 昇順だけで決まるため、
    全参加者に同じ推薦が返る。

    これは RANDOM 固有の欠陥ではなく **COVERAGE も全く同じ**であり
    （`pre_survey=null` は 07-testing §3 で「開場直後の常態」、
    demo シナリオ ② は rank が visitor_count 昇順に完全一致することを期待値としている）、
    P-4 の「属性が違えば結果が違う」は満たしている（この入力では属性が違わない）。

    ただし ADR 0007 §2 が RANDOM に期待する「下限ベースライン」としては弱い。
    P-6 と両立しないこの点は未決定事項として docs/README.md に記録した。
    ここでは**現在の挙動を可視化して固定する**（黙って劣化させないため）。
    """
    tied = {
        "user_id": "PLACEHOLDER",
        "cell_count": 4,
        "candidate_booths": [
            {"booth_id": f"b{i}", "category_id": "cat_x", "visitor_count": v}
            for i, v in enumerate([2, 6, 11, 18, 27, 39, 52, 70, 95, 130])
        ],
        "pre_survey": None,
    }

    def assigned_for(strategy: str) -> set[tuple[str, ...]]:
        s = Settings(
            _env_file=None,
            enabled_attributes=["preference_match", "rating_affinity"],
            strategy=strategy,
            ops_token="",
        )
        return {
            tuple(
                a.booth_id
                for a in run_recommendation(
                    RecommendRequest.model_validate({**tied, "user_id": f"u{i}"}),
                    settings=s,
                    rule_cache=RuleCache(),
                ).assigned
            )
            for i in range(20)
        }

    assert len(assigned_for("random")) == 1
    assert assigned_for("random") == assigned_for("coverage")  # COVERAGE と同じ挙動


def test_p5_mismatch_score_is_not_zero(run_random):
    req = RecommendRequest.model_validate(
        {
            "user_id": "u1",
            "cell_count": 2,
            "candidate_booths": [{"booth_id": "b1", "category_id": "cat_x", "visitor_count": 5}],
            "pre_survey": {"interest_categories": ["cat_a"], "top_interest_category": "cat_a"},
        }
    )
    s = run_random(req).scores[0]
    assert s.interest_match == "MISMATCH"
    assert s.score >= 0.05


def test_p6_same_interest_match_sorted_by_visitor_count_asc(run_random):
    for uid in [f"u{i}" for i in range(20)]:
        resp = run_random(_req(uid))
        by_class: dict[str, list] = {}
        for s in resp.scores:
            by_class.setdefault(s.interest_match, []).append(s)
        for group in by_class.values():
            ordered = sorted(group, key=lambda s: s.rank)
            vcs = [_vc(s) for s in ordered]
            assert vcs == sorted(vcs), (uid, vcs)


def test_e1_reproducible(run_random):
    a = run_random(_req("u1"))
    b = run_random(_req("u1"))
    assert [(s.booth_id, s.score) for s in a.scores] == [(s.booth_id, s.score) for s in b.scores]
    assert [x.booth_id for x in a.assigned] == [x.booth_id for x in b.assigned]


def test_log_carries_strategy_field(run_random, capsys):
    run_random(_req("u1"))
    lines = [l for l in capsys.readouterr().out.splitlines() if '"kind":"recommend"' in l]
    assert lines and '"strategy":"RANDOM"' in lines[-1]
    assert '"phase":"COVERAGE"' in lines[-1]  # 契約の phase は3値のまま (ADR 0007 §4)
