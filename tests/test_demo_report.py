"""目視確認レポート／プレイグラウンドの回帰テスト。

既定パラメータで全チェックが緑であること、パラメータ上書きが効くこと、
入力サニタイズが範囲外を丸めることを固定する。
"""

from __future__ import annotations

import pytest

from event_support_recommend.demo import (
    build_playground_html,
    build_report_html,
    effective_params,
    report_payload,
    run_drsa_demo,
    run_scenarios,
    sanitize_overrides,
)


def test_default_params_all_checks_pass():
    failures = [
        (sr.title, name, detail)
        for sr in run_scenarios()
        for name, ok, detail in sr.checks
        if not ok
    ]
    assert not failures, failures


def test_every_scenario_has_nonpositive_rank_correlation_by_default():
    for sr in run_scenarios():
        assert sr.rho <= 1e-9, (sr.title, sr.rho)


def test_drsa_demo_recovers_embedded_rule_at_relaxed_l():
    demos = {d.consistency: d for d in run_drsa_demo()}
    relaxed = demos[0.7]
    assert any(
        r["conclusion"] == ">=HIGH" and "preference_match>=3" in r["text"] for r in relaxed.rules
    )
    assert any(
        r["conclusion"] == "<=LOW" and "preference_match<=1" in r["text"] for r in relaxed.rules
    )


def test_overrides_change_scoring():
    """w_interest を上げると第1希望 (b_pop, MATCH) の score が上がる。"""
    base = {s.key: s.rows for s in run_scenarios()}["s1"]
    hot = {s.key: s.rows for s in run_scenarios({"w_coverage": 0.1, "w_interest": 0.9})}["s1"]
    b_pop_base = next(r for r in base if r["booth_id"] == "b_pop")["score"]
    b_pop_hot = next(r for r in hot if r["booth_id"] == "b_pop")["score"]
    assert b_pop_hot > b_pop_base


def test_mismatch_weight_is_floored_even_if_set_to_zero():
    rows = {s.key: s.rows for s in run_scenarios({"interest_mismatch": 0.0})}["s1"]
    for r in rows:
        if r["interest_match"] in ("MISMATCH", "UNKNOWN"):
            assert r["interest_term"] >= 0.05
            assert r["score"] > 0.0


def test_sanitize_clamps_and_drops_unknown():
    out = sanitize_overrides({"w_interest": 5.0, "min_support": -3, "bogus": 1})
    assert out == {"w_interest": 1.0, "min_support": 1}
    assert "bogus" not in out


def test_effective_params_fills_defaults():
    p = effective_params({"w_interest": 0.7})
    assert p["w_interest"] == 0.7
    assert p["w_coverage"] == 0.5


def test_report_payload_shape():
    p = report_payload(None)
    assert p["overall"]["passed"] == p["overall"]["total"]
    assert len(p["scenarios"]) == 6
    assert {"params", "param_specs", "guard", "drsa"} <= p.keys()


@pytest.mark.parametrize("html_fn", [build_report_html, build_playground_html])
def test_html_builds(html_fn):
    html = html_fn()
    assert html.lstrip().lower().startswith("<!doctype html>")


def test_demo_endpoints(client):
    assert client.get("/demo").status_code == 200
    r = client.post("/demo/run", json={"overrides": {"w_interest": 0.9, "w_coverage": 0.1}})
    assert r.status_code == 200
    body = r.json()
    assert body["params"]["w_interest"] == 0.9
    assert len(body["scenarios"]) == 6


# --------------------------------------------------------------------------- #
# P-1 — デモ・リプレイの推薦が研究ログ (kind: "recommend") を汚さないこと
# (ADR 0008 §1, docs/specs/parameter-tuning/README.md §2)
# --------------------------------------------------------------------------- #
def _kinds(captured: str) -> set[str]:
    import json

    return {json.loads(l)["kind"] for l in captured.splitlines() if l.startswith("{")}


def test_demo_run_never_emits_kind_recommend(client, capsys):
    """★ これが破れると、合成 ID (u_alice 等) の推薦が本物と同じ kind で混ざる。"""
    capsys.readouterr()
    assert client.post("/demo/run", json={}).status_code == 200
    kinds = _kinds(capsys.readouterr().out)
    assert "recommend" not in kinds, "デモの推薦が研究ログに混ざっている"
    assert "recommend_demo" in kinds, "デモの推薦が記録されていない（案 b になっている）"


def test_ops_replay_never_emits_kind_recommend(client, capsys):
    """リプレイは本物の user_id で走るので、合成 ID より紛れ込みやすい。"""
    from conftest import make_request

    payload = make_request(user_id="u_real").model_dump(mode="json")
    capsys.readouterr()
    assert client.post("/ops/replay", json=payload).status_code == 200
    kinds = _kinds(capsys.readouterr().out)
    assert "recommend" not in kinds, "リプレイが研究ログに混ざっている"
    assert "recommend_replay" in kinds


def test_production_path_keeps_kind_recommend(client, capsys):
    """逆方向の固定: 本番経路の kind は変えていない（既存の分析経路を壊さない）。"""
    from conftest import make_request

    payload = make_request().model_dump(mode="json")
    capsys.readouterr()
    assert client.post("/recommend/cells", json=payload).status_code == 200
    assert "recommend" in _kinds(capsys.readouterr().out)
