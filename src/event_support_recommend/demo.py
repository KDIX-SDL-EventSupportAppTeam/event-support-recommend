"""目視確認用のデモレポート生成。

固定の合成シナリオを engine / drsa に流し、結果を1枚の HTML にまとめる。
- COVERAGE 推薦: 候補ごとの score・interest_match・coverage項/interest項を表で表示
- 散布図: score vs visitor_count（人気順への退化が起きていないことを目で確認）
- 自動チェック: docs/specs/07-testing.md の「起きてはいけないこと」を各シナリオで判定
- DRSA コア: 合成決定表から抽出された規則を人間可読で表示（engine 経路は未結線なので単体で）

標準ライブラリのみ（numpy は既存依存だが未使用）。`build_report_html()` は副作用なし。
"""

from __future__ import annotations

import html
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .api.schemas import RecommendRequest
from .cache import RuleCache
from .drsa import DecisionTable, approximate, generate_rules
from .drsa.decision_table import DecisionRow
from .engine import run_recommendation
from .models import DecisionClass
from .settings import Settings

# interest_match ごとの色（散布図・バッジ共通）
_COLOR = {
    "MATCH": "#2e7d32",
    "PARTIAL": "#f9a825",
    "MISMATCH": "#c62828",
    "UNKNOWN": "#607d8b",
}


# --------------------------------------------------------------------------- #
# 順位相関（自前・同順位は平均順位）
# --------------------------------------------------------------------------- #
def _avg_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[order[j + 1]] == values[order[i]]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    rx, ry = _avg_ranks(xs), _avg_ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = sum((a - mx) ** 2 for a in rx) ** 0.5
    sy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (sx * sy) if sx and sy else 0.0


# --------------------------------------------------------------------------- #
# シナリオ定義
# --------------------------------------------------------------------------- #
@dataclass
class ScenarioResult:
    key: str
    title: str
    intent: str
    watch: str
    request: dict
    rows: list[dict] = field(default_factory=list)  # 表示用に整形した scores
    assigned_ids: list[str] = field(default_factory=list)
    phase: str = ""
    decision_table_size: int | None = None
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    rho: float = 0.0


_SETTINGS = Settings(_env_file=None, enabled_attributes=["preference_match", "rating_affinity"])

# 共通の候補セット（人気度に幅・関心一致に幅）
_BOOTHS = [
    {"booth_id": "b_pop", "category_id": "cat_a", "visitor_count": 500},  # 人気突出・第1希望
    {"booth_id": "b_a1", "category_id": "cat_a", "visitor_count": 8},
    {"booth_id": "b_b1", "category_id": "cat_b", "visitor_count": 15},
    {"booth_id": "b_b2", "category_id": "cat_b", "visitor_count": 60},
    {"booth_id": "b_x1", "category_id": "cat_x", "visitor_count": 3},
    {"booth_id": "b_x2", "category_id": "cat_x", "visitor_count": 40},
    {"booth_id": "b_x3", "category_id": "cat_y", "visitor_count": 22},
]


def _scenario_defs() -> list[dict]:
    return [
        dict(
            key="s1",
            title="① アンケート回答あり・人気ブースが1件突出",
            intent="第1希望 cat_a、関心 [cat_a, cat_b]。b_pop は cat_a だが訪問者500人。",
            watch="人気の b_pop が先頭に来ないこと。関心一致（MATCH/PARTIAL）が上位、"
            "同じ interest_match の中では訪問者数が少ない順に並ぶこと。",
            request=dict(
                user_id="u_alice",
                cell_count=4,
                candidate_booths=_BOOTHS,
                pre_survey={"interest_categories": ["cat_a", "cat_b"], "top_interest_category": "cat_a"},
            ),
        ),
        dict(
            key="s2",
            title="② アンケート未回答",
            intent="pre_survey が null。関心情報がまったく無い開場直後の状態。",
            watch="全候補 interest_match=UNKNOWN。実質「訪問者数が少ない順」だけになること"
            "（rank が visitor_count 昇順と完全一致）。",
            request=dict(user_id="u_bob", cell_count=4, candidate_booths=_BOOTHS, pre_survey=None),
        ),
        dict(
            key="s3",
            title="③ 第1希望の設問なし（多肢選択のみ）",
            intent="top_interest_category が無い構成。interest_categories=[cat_a, cat_b] のみ。",
            watch="PARTIAL が発生せず、cat_a も cat_b も MATCH になること"
            "（docs/specs/02-features.md §4）。",
            request=dict(
                user_id="u_carol",
                cell_count=4,
                candidate_booths=_BOOTHS,
                pre_survey={"interest_categories": ["cat_a", "cat_b"]},
            ),
        ),
        dict(
            key="s4a",
            title="④-A 同じ候補・参加者 alice（第1希望 cat_a）",
            intent="参加者ごとに結果が変わることの確認（その1）。",
            watch="④-B と assigned が異なること。cat_a が上位。",
            request=dict(
                user_id="u_alice",
                cell_count=4,
                candidate_booths=_BOOTHS,
                pre_survey={"interest_categories": ["cat_a"], "top_interest_category": "cat_a"},
            ),
        ),
        dict(
            key="s4b",
            title="④-B 同じ候補・参加者 dave（第1希望 cat_x）",
            intent="参加者ごとに結果が変わることの確認（その2）。",
            watch="④-A と assigned が異なること。cat_x が上位。",
            request=dict(
                user_id="u_dave",
                cell_count=4,
                candidate_booths=_BOOTHS,
                pre_survey={"interest_categories": ["cat_x"], "top_interest_category": "cat_x"},
            ),
        ),
        dict(
            key="s5",
            title="⑤ 候補が cell_count より少ない",
            intent="候補2件・cell_count=6。",
            watch="assigned が2件のまま返ること（人気ブースで無理に埋めない・O-4）。",
            request=dict(
                user_id="u_erin",
                cell_count=6,
                candidate_booths=[
                    {"booth_id": "only_a", "category_id": "cat_a", "visitor_count": 30},
                    {"booth_id": "only_b", "category_id": "cat_b", "visitor_count": 5},
                ],
                pre_survey={"interest_categories": ["cat_a"], "top_interest_category": "cat_a"},
            ),
        ),
    ]


def run_scenarios() -> list[ScenarioResult]:
    defs = _scenario_defs()
    results: list[ScenarioResult] = []
    by_key: dict[str, ScenarioResult] = {}

    for d in defs:
        req = RecommendRequest.model_validate(d["request"])
        resp = run_recommendation(req, settings=_SETTINGS, rule_cache=RuleCache())
        rows = []
        for s in sorted(resp.scores, key=lambda s: s.rank_in_event):
            raw = s.attributes.get("raw", {})
            rows.append(
                dict(
                    rank=s.rank_in_event,
                    booth_id=s.booth_id,
                    category=raw.get("category_id"),
                    visitor_count=raw.get("visitor_count"),
                    interest_match=s.interest_match,
                    coverage_term=raw.get("coverage_term"),
                    interest_term=raw.get("interest_term"),
                    score=s.score,
                    assigned=s.was_assigned,
                )
            )
        sr = ScenarioResult(
            key=d["key"],
            title=d["title"],
            intent=d["intent"],
            watch=d["watch"],
            request=d["request"],
            rows=rows,
            assigned_ids=[a.booth_id for a in resp.assigned],
            phase=resp.phase,
            decision_table_size=resp.decision_table_size,
        )
        xs = [r["visitor_count"] for r in rows]
        ys = [r["score"] for r in rows]
        sr.rho = spearman(xs, ys)
        results.append(sr)
        by_key[d["key"]] = sr

    _attach_checks(by_key)
    return results


def _monotonic_within_group(rows: list[dict]) -> bool:
    """同じ interest_match の候補どうしで rank が visitor_count 昇順に厳密に並ぶか (P-6)。"""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["interest_match"], []).append(r)
    for g in groups.values():
        ordered = sorted(g, key=lambda r: r["rank"])
        vc = [r["visitor_count"] for r in ordered]
        if any(a > b for a, b in zip(vc, vc[1:])):
            return False
    return True


def _attach_checks(by_key: dict[str, ScenarioResult]) -> None:
    for sr in by_key.values():
        c = sr.checks
        c.append(
            (
                "score と visitor_count の順位相関が正でない (P-1)",
                sr.rho <= 1e-9,
                f"Spearman ρ = {sr.rho:+.3f}",
            )
        )
        c.append(
            (
                "MISMATCH 候補の score が 0 でない (P-5)",
                all(r["score"] > 0 for r in sr.rows if r["interest_match"] == "MISMATCH")
                or not any(r["interest_match"] == "MISMATCH" for r in sr.rows),
                "不一致カテゴリが構造的に排除されていない",
            )
        )
        c.append(
            (
                "同じ interest_match 内で訪問者数が少ない順 (P-6)",
                _monotonic_within_group(sr.rows),
                "",
            )
        )
        c.append(
            (
                "返り値がすべて 200 相当・scores は候補全件 (C-2)",
                len(sr.rows) == len(sr.request["candidate_booths"]),
                f"candidates={len(sr.request['candidate_booths'])}, scores={len(sr.rows)}",
            )
        )

    if "s1" in by_key:
        sr = by_key["s1"]
        first = sr.assigned_ids[0] if sr.assigned_ids else None
        sr.checks.append(
            ("突出人気ブース b_pop が assigned の先頭でない (P-3)", first != "b_pop", f"先頭 = {first}")
        )

    if "s2" in by_key:
        sr = by_key["s2"]
        sr.checks.append(
            (
                "未回答なら全候補 UNKNOWN",
                all(r["interest_match"] == "UNKNOWN" for r in sr.rows),
                "",
            )
        )
        ranks_by_vc = [r["booth_id"] for r in sorted(sr.rows, key=lambda r: r["visitor_count"])]
        ranks_actual = [r["booth_id"] for r in sorted(sr.rows, key=lambda r: r["rank"])]
        sr.checks.append(
            (
                "未回答なら rank が visitor_count 昇順に完全一致",
                ranks_by_vc == ranks_actual,
                "",
            )
        )

    if "s3" in by_key:
        sr = by_key["s3"]
        kinds = {r["interest_match"] for r in sr.rows}
        sr.checks.append(("第1希望の設問が無ければ PARTIAL が出ない (F-5)", "PARTIAL" not in kinds, ""))

    if "s4a" in by_key and "s4b" in by_key:
        a, b = by_key["s4a"], by_key["s4b"]
        a.checks.append(
            ("参加者が違えば assigned が変わる (P-4)", a.assigned_ids != b.assigned_ids,
             f"A={a.assigned_ids} / B={b.assigned_ids}")
        )
        b.checks.append(
            ("参加者が違えば assigned が変わる (P-4)", a.assigned_ids != b.assigned_ids,
             f"A={a.assigned_ids} / B={b.assigned_ids}")
        )

    if "s5" in by_key:
        sr = by_key["s5"]
        sr.checks.append(
            ("候補不足でも無理に埋めない (O-4)", len(sr.assigned_ids) == 2, f"assigned={sr.assigned_ids}")
        )


# --------------------------------------------------------------------------- #
# DRSA コアのデモ（engine 経路は未結線なので drsa/ を直接叩く）
# --------------------------------------------------------------------------- #
_DRSA_NAMES = ("preference_match", "rating_affinity")


def _synth_decision_table(n: int, noise: float, seed: int) -> DecisionTable:
    """正解の規則を埋め込む: pm>=3 -> HIGH, pm<=1 -> LOW（各 1-noise の確率）。"""
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        pm, ra = rng.randint(0, 3), rng.randint(1, 3)
        base = (
            DecisionClass.HIGH if pm >= 3 else DecisionClass.LOW if pm <= 1
            else rng.choice([DecisionClass.HIGH, DecisionClass.LOW])
        )
        if rng.random() < noise:
            base = DecisionClass.LOW if base == DecisionClass.HIGH else DecisionClass.HIGH
        rows.append(DecisionRow((pm, ra), base))
    return DecisionTable(_DRSA_NAMES, rows)


@dataclass
class DrsaDemo:
    n: int
    noise: float
    consistency: float
    gamma: float
    rules: list[dict]
    applied: list[dict]


def run_drsa_demo() -> list[DrsaDemo]:
    demos: list[DrsaDemo] = []
    table = _synth_decision_table(n=150, noise=0.1, seed=20261016)
    for l in (1.0, 0.8, 0.7):
        ap = approximate(table, consistency_level=l)
        rs = generate_rules(table, min_support=_SETTINGS.min_support, consistency_level=l)
        rules = [
            dict(
                id=r.id,
                text=r.as_text(),
                conclusion=r.conclusion,
                support=r.support,
                confidence=round(r.confidence, 3),
            )
            for r in rs.rules
        ]
        applied = []
        for pm in (0, 1, 2, 3):
            for ra in (1, 2, 3):
                up, down, matched = rs.apply({"preference_match": pm, "rating_affinity": ra})
                applied.append(
                    dict(
                        pm=pm,
                        ra=ra,
                        up=round(up, 3),
                        down=round(down, 3),
                        score=round((1 + up - down) / 2, 3),
                        n_rules=len(matched),
                    )
                )
        demos.append(
            DrsaDemo(n=len(table), noise=0.1, consistency=l, gamma=round(ap.gamma, 3),
                     rules=rules, applied=applied)
        )
    return demos


# --------------------------------------------------------------------------- #
# HTML 生成
# --------------------------------------------------------------------------- #
def _e(x: object) -> str:
    return html.escape(str(x))


def _fmt(x: object) -> str:
    if isinstance(x, float):
        return f"{x:.3f}"
    return _e(x)


def _badge(kind: str) -> str:
    return f'<span class="badge" style="background:{_COLOR.get(kind, "#777")}">{_e(kind)}</span>'


def _scatter_svg(rows: list[dict], rho: float) -> str:
    w, h, pad = 460, 260, 44
    xs = [r["visitor_count"] or 0 for r in rows]
    ys = [r["score"] or 0 for r in rows]
    xmax = max(xs + [1])
    plot_w, plot_h = w - pad * 2, h - pad * 2

    def px(v: float) -> float:
        return pad + (v / xmax) * plot_w if xmax else pad

    def py(v: float) -> float:
        return pad + plot_h - v * plot_h  # score は 0..1

    dots = []
    for r in rows:
        cx, cy = px(r["visitor_count"] or 0), py(r["score"] or 0)
        col = _COLOR.get(r["interest_match"], "#777")
        dots.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="6" fill="{col}" fill-opacity="0.85" '
            f'stroke="#0006"><title>{_e(r["booth_id"])}  visitor={r["visitor_count"]}  '
            f'score={r["score"]:.3f}  {_e(r["interest_match"])}</title></circle>'
        )
    grid = []
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        y = py(frac)
        grid.append(f'<line x1="{pad}" y1="{y:.1f}" x2="{w - pad}" y2="{y:.1f}" stroke="#8883"/>')
        grid.append(f'<text x="{pad - 6}" y="{y + 4:.1f}" text-anchor="end" class="tick">{frac:.2f}</text>')
    verdict = "OK（負 or 0：人気順ではない）" if rho <= 1e-9 else "要注意（正：人気順に相関）"
    vcol = "#2e7d32" if rho <= 1e-9 else "#c62828"
    return f"""<svg viewBox="0 0 {w} {h}" class="scatter" xmlns="http://www.w3.org/2000/svg">
  <rect x="0" y="0" width="{w}" height="{h}" fill="none"/>
  {''.join(grid)}
  <line x1="{pad}" y1="{pad}" x2="{pad}" y2="{h - pad}" stroke="#8886"/>
  <line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{h - pad}" stroke="#8886"/>
  <text x="{w/2:.0f}" y="{h-8}" text-anchor="middle" class="axis">visitor_count →（0〜{xmax}）</text>
  <text x="14" y="{h/2:.0f}" text-anchor="middle" class="axis" transform="rotate(-90 14 {h/2:.0f})">score ↑</text>
  {''.join(dots)}
  <text x="{w - pad}" y="{pad - 14}" text-anchor="end" class="rho" fill="{vcol}">Spearman ρ = {rho:+.3f} — {verdict}</text>
</svg>"""


def _scenario_html(sr: ScenarioResult) -> str:
    head = (
        f'<tr><th>rank</th><th>booth_id</th><th>category</th><th>visitor</th>'
        f'<th>interest_match</th><th>coverage項</th><th>interest項</th><th>score</th><th>assigned</th></tr>'
    )
    body = ""
    for r in sr.rows:
        cls = ' class="assigned"' if r["assigned"] else ""
        body += (
            f"<tr{cls}><td>{r['rank']}</td><td>{_e(r['booth_id'])}</td>"
            f"<td>{_e(r['category'])}</td><td>{_e(r['visitor_count'])}</td>"
            f"<td>{_badge(r['interest_match'])}</td>"
            f"<td>{_fmt(r['coverage_term'])}</td><td>{_fmt(r['interest_term'])}</td>"
            f"<td><b>{_fmt(r['score'])}</b></td>"
            f"<td>{'✅' if r['assigned'] else ''}</td></tr>"
        )
    checks = ""
    for name, ok, detail in sr.checks:
        mark = '<span class="ok">✓</span>' if ok else '<span class="ng">✗</span>'
        checks += f"<li>{mark} {_e(name)}{f' — <code>{_e(detail)}</code>' if detail else ''}</li>"
    return f"""<section class="card">
  <h3>{_e(sr.title)}</h3>
  <p class="intent">{_e(sr.intent)}</p>
  <p class="watch"><b>見るべき点：</b>{_e(sr.watch)}</p>
  <p class="meta">phase=<code>{_e(sr.phase)}</code>　decision_table_size=<code>{_e(sr.decision_table_size)}</code>
     　assigned=<code>{_e(sr.assigned_ids)}</code></p>
  <div class="cols">
    <table>{head}{body}</table>
    <div>{_scatter_svg(sr.rows, sr.rho)}</div>
  </div>
  <ul class="checks">{checks}</ul>
</section>"""


def _drsa_html(demos: list[DrsaDemo]) -> str:
    blocks = ""
    for d in demos:
        rule_rows = "".join(
            f"<tr><td><code>{_e(r['id'])}</code></td><td>{_e(r['text'])}</td>"
            f"<td>{r['support']}</td><td>{r['confidence']}</td></tr>"
            for r in d.rules
        ) or '<tr><td colspan="4"><i>規則なし</i></td></tr>'
        grid = ""
        for pm in (3, 2, 1, 0):
            cells = ""
            for ra in (1, 2, 3):
                a = next(x for x in d.applied if x["pm"] == pm and x["ra"] == ra)
                shade = int(255 - a["score"] * 120)
                cells += (
                    f'<td style="background:rgb({shade},{255 if a["score"]>=0.5 else shade},{shade})">'
                    f'{a["score"]:.2f}<br><small>↑{a["up"]:.2f} ↓{a["down"]:.2f}</small></td>'
                )
            grid += f"<tr><th>pm={pm}</th>{cells}</tr>"
        note = (
            '<p class="watch">l=1.0（厳密版）ではノイズ入りデータの下方近似がほぼ空になり、'
            '規則が出ない・γ≈0 になります。<b>これは仕様どおり</b>で、'
            'だからこそ VC-DRSA（l を下げる）が必要です（docs/specs/05-drsa.md §2）。</p>'
            if not d.rules
            else ""
        )
        blocks += f"""<section class="card">
  <h3>一貫性水準 l = {d.consistency}　（決定表 {d.n} 行・ノイズ 10%）</h3>
  <p class="meta">近似の質 γ = <code>{d.gamma}</code>　規則数 = <code>{len(d.rules)}</code></p>
  {note}
  <div class="cols">
    <table><tr><th>id</th><th>規則</th><th>support</th><th>confidence</th></tr>{rule_rows}</table>
    <table class="applied"><tr><th></th><th>ra=1</th><th>ra=2</th><th>ra=3</th></tr>{grid}</table>
  </div>
</section>"""
    return blocks


def build_report_html() -> str:
    scenarios = run_scenarios()
    drsa = run_drsa_demo()
    total_checks = sum(len(s.checks) for s in scenarios)
    failed = sum(1 for s in scenarios for _, ok, _ in s.checks if not ok)
    overall = "🟢 すべて合格" if failed == 0 else f"🔴 {failed} / {total_checks} 件が不合格"
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    guard_rows = "".join(
        f"<tr><td>{_e(s.title)}</td><td>{s.rho:+.3f}</td>"
        f"<td>{'🟢 人気順でない' if s.rho <= 1e-9 else '🔴 人気順に相関'}</td></tr>"
        for s in scenarios
    )

    css = """
    :root { color-scheme: light dark; }
    body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0 auto; max-width: 1100px;
           padding: 24px; line-height: 1.6; }
    h1 { margin-bottom: 4px; }
    .sub { color: #888; margin-top: 0; }
    .card { border: 1px solid #8884; border-radius: 10px; padding: 16px 20px; margin: 18px 0;
            background: #8881; }
    .cols { display: flex; gap: 20px; flex-wrap: wrap; align-items: flex-start; }
    .cols > * { flex: 1 1 420px; }
    table { border-collapse: collapse; width: 100%; font-size: 13px; }
    th, td { border: 1px solid #8883; padding: 4px 8px; text-align: center; }
    tr.assigned { background: #2e7d3222; font-weight: 600; }
    .badge { color: #fff; padding: 1px 7px; border-radius: 999px; font-size: 11px; }
    .checks { list-style: none; padding-left: 0; }
    .checks li { margin: 3px 0; }
    .ok { color: #2e7d32; font-weight: 700; }
    .ng { color: #c62828; font-weight: 700; }
    .intent { color: #aaa; margin: 2px 0; }
    .watch { margin: 6px 0; }
    .meta { font-size: 12px; color: #999; }
    code { background: #8882; padding: 1px 5px; border-radius: 4px; }
    svg.scatter { width: 100%; height: auto; border: 1px solid #8883; border-radius: 8px; }
    .tick, .axis, .rho { font-size: 11px; fill: #999; }
    .rho { font-weight: 700; }
    table.applied td { font-size: 12px; color: #111; }
    .banner { padding: 10px 16px; border-radius: 8px; font-weight: 700; font-size: 18px;
              background: #8882; }
    """
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>推薦エンジン 目視確認レポート</title><style>{css}</style></head><body>
<h1>推薦エンジン 目視確認レポート</h1>
<p class="sub">生成: {ts}　/　engine 経路の戦略: <code>COVERAGE</code> のみ（SIMILARITY / DRSA は ADR 0002 待ちで未結線）</p>
<p class="banner">{overall}（{total_checks - failed} / {total_checks} 件）</p>

<h2>1. COVERAGE 推薦シナリオ</h2>
<p>行がハイライトされているものが <code>assigned</code>（マスに載る）。散布図の点にカーソルを合わせると詳細が出ます。
「見るべき点」と、その下の自動チェック（✓/✗）を照らし合わせてください。</p>
{''.join(_scenario_html(s) for s in scenarios)}

<h2>2. 人気順への退化ガード（全シナリオ総括）</h2>
<p>去年の失敗は「フォールバック解除が早すぎ、推薦が人気度ランキングに退化した」こと。
score と visitor_count の順位相関がすべて 0 以下であれば、その退化は起きていません。</p>
<table><tr><th>シナリオ</th><th>Spearman ρ</th><th>判定</th></tr>{guard_rows}</table>

<h2>3. DRSA コア（単体デモ）</h2>
<p>engine 経路には未結線ですが、コアは動きます。<b>pm≧3 なら HIGH / pm≦1 なら LOW</b> という規則を
埋め込んだ合成決定表（150行・ノイズ10%）から、規則が抽出できるかを確認します。
右の表は各 (preference_match, rating_affinity) に規則を適用したスコア (1+↑−↓)/2。
l を下げると確実側に取り込む範囲が広がり、規則数と γ が変わります。</p>
{_drsa_html(drsa)}

<h2>4. この画面で確認できること / できないこと</h2>
<ul>
<li>✅ COVERAGE が「訪問者数が少ない順 ＋ 関心分野一致」で並べ、人気ブースを優遇しないこと</li>
<li>✅ アンケート未回答・候補不足などの異常入力でも壊れず 200 相当を返すこと</li>
<li>✅ DRSA コアが順序尺度から人間可読な規則を抽出できること</li>
<li>⛔ 実データでの精度・セレンディピティ率（= event-support-analytics の担当）</li>
<li>⛔ SIMILARITY / DRSA の本番挙動（ADR 0002 でデータ入手経路が決まってから）</li>
</ul>
</body></html>"""
