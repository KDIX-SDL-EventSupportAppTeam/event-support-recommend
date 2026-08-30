"""目視確認レポートの回帰テスト。

レポート生成が壊れないこと、そして各シナリオの自動チェックがすべて緑であること
（= 人気順への退化などが起きていないこと）を CI で固定する。
"""

from __future__ import annotations

from event_support_recommend.demo import build_report_html, run_drsa_demo, run_scenarios


def test_all_scenario_checks_pass():
    failures = [
        (sr.title, name, detail)
        for sr in run_scenarios()
        for name, ok, detail in sr.checks
        if not ok
    ]
    assert not failures, failures


def test_every_scenario_has_nonpositive_rank_correlation():
    for sr in run_scenarios():
        assert sr.rho <= 1e-9, (sr.title, sr.rho)


def test_drsa_demo_recovers_embedded_rule_at_relaxed_l():
    demos = {d.consistency: d for d in run_drsa_demo()}
    relaxed = demos[0.7]
    assert relaxed.gamma >= 0.0
    up_pm3 = any(
        r["conclusion"] == ">=HIGH" and "preference_match>=3" in r["text"] for r in relaxed.rules
    )
    down_pm1 = any(
        r["conclusion"] == "<=LOW" and "preference_match<=1" in r["text"] for r in relaxed.rules
    )
    assert up_pm3 and down_pm1, [r["text"] for r in relaxed.rules]


def test_report_html_builds():
    html = build_report_html()
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert "推薦エンジン 目視確認レポート" in html
    assert "Spearman" in html
