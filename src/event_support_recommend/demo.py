"""目視確認用のデモ。

固定の合成シナリオを engine / drsa に流し、結果を可視化する。
- COVERAGE 推薦: 候補ごとの score・interest_match・coverage項/interest項を表に
- 散布図: score vs visitor_count（人気順への退化が起きていないことを目で確認）
- 自動チェック: docs/specs/07-testing.md の「起きてはいけないこと」を各シナリオで判定
- DRSA コア: 合成決定表から抽出された規則を人間可読で表示（engine 経路は未結線なので単体で）

2つの入口:
- ``build_report_html()``  … 既定パラメータの静的レポート（tools/build_report.py が使う）
- ``report_payload(overrides)`` … パラメータを上書きした結果を dict で返す（GET/POST /demo が使う）
  ＋ ``build_playground_html()`` … その dict をスライダーで叩く対話ページ

標準ライブラリのみ（numpy は既存依存だが未使用）。副作用なし。
"""

from __future__ import annotations

import html
import json
import random
from dataclasses import dataclass, field

from .api.schemas import RecommendRequest
from .cache import RuleCache
from .drsa import DecisionTable, approximate, generate_rules
from .drsa.decision_table import DecisionRow
from .engine import run_recommendation
from .models import DecisionClass
from .settings import Settings

_COLOR = {"MATCH": "#2e7d32", "PARTIAL": "#f9a825", "MISMATCH": "#c62828", "UNKNOWN": "#607d8b"}


# --------------------------------------------------------------------------- #
# 調整できるパラメータ（UI と入力サニタイズの両方がこの定義を使う）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ParamSpec:
    key: str
    label: str
    group: str
    lo: float
    hi: float
    step: float
    default: float
    note: str = ""

    def clamp(self, v: float) -> float:
        return max(self.lo, min(self.hi, float(v)))


PARAM_SPECS: tuple[ParamSpec, ...] = (
    ParamSpec("w_coverage", "w_coverage（訪問者少ない順の重み）", "COVERAGE", 0.0, 1.0, 0.05, 0.5),
    ParamSpec("w_interest", "w_interest（関心一致の重み）", "COVERAGE", 0.0, 1.0, 0.05, 0.5),
    ParamSpec("interest_partial", "interest 重み: PARTIAL（第2希望）", "COVERAGE", 0.0, 1.0, 0.05, 0.6),
    ParamSpec(
        "interest_mismatch", "interest 重み: MISMATCH / UNKNOWN", "COVERAGE", 0.0, 1.0, 0.05, 0.2,
        note="0.05 未満は P-5（セレンディピティ観測可能性）のため強制的に 0.05 に切り上げ",
    ),
    ParamSpec("max_per_category", "max_per_category（0=無効）", "割当", 0, 6, 1, 0),
    ParamSpec("drsa_consistency", "l（VC-DRSA 一貫性水準）", "DRSA", 0.5, 1.0, 0.05, 0.8),
    ParamSpec("min_support", "min_support（規則の最小サポート）", "DRSA", 1, 20, 1, 5),
    ParamSpec(
        "drsa_min_gamma", "γ ゲート（この値未満なら DRSA 不採用）", "DRSA", 0.0, 1.0, 0.05, 0.5,
        note="表示上の合否ラインのみ。エンジン挙動は変えない",
    ),
)
_SPEC_BY_KEY = {p.key: p for p in PARAM_SPECS}
_INT_KEYS = {"max_per_category", "min_support"}
# Settings に渡すキー（drsa_min_gamma は表示専用なので engine には渡すが挙動は不変）
_SETTINGS_KEYS = {
    "w_coverage", "w_interest", "interest_partial", "interest_mismatch",
    "max_per_category", "drsa_consistency", "min_support", "drsa_min_gamma",
}


def sanitize_overrides(raw: dict | None) -> dict:
    """UI / API から来た上書きを、既知キーだけ・範囲内に丸める。"""
    out: dict = {}
    for k, v in (raw or {}).items():
        spec = _SPEC_BY_KEY.get(k)
        if spec is None or v is None:
            continue
        try:
            val = spec.clamp(v)
        except (TypeError, ValueError):
            continue
        out[k] = int(round(val)) if k in _INT_KEYS else round(val, 4)
    return out


def effective_params(overrides: dict | None = None) -> dict:
    o = sanitize_overrides(overrides)
    return {p.key: o.get(p.key, p.default) for p in PARAM_SPECS}


def _settings(overrides: dict | None = None) -> Settings:
    o = sanitize_overrides(overrides)
    kwargs = {k: v for k, v in o.items() if k in _SETTINGS_KEYS}
    return Settings(
        _env_file=None,
        enabled_attributes=["preference_match", "rating_affinity"],
        **kwargs,
    )


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
    rows: list[dict] = field(default_factory=list)
    assigned_ids: list[str] = field(default_factory=list)
    phase: str = ""
    decision_table_size: int | None = None
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    rho: float = 0.0

    def to_dict(self) -> dict:
        return dict(
            key=self.key,
            title=self.title,
            intent=self.intent,
            watch=self.watch,
            n_candidates=len(self.request["candidate_booths"]),
            rows=self.rows,
            assigned_ids=self.assigned_ids,
            phase=self.phase,
            decision_table_size=self.decision_table_size,
            rho=round(self.rho, 4),
            checks=[dict(name=n, ok=ok, detail=d) for n, ok, d in self.checks],
        )


_BOOTHS = [
    {"booth_id": "b_pop", "category_id": "cat_a", "visitor_count": 500},
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
                user_id="u_alice", cell_count=4, candidate_booths=_BOOTHS,
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
            watch="PARTIAL が発生せず、cat_a も cat_b も MATCH になること（docs/specs/02-features.md §4）。",
            request=dict(
                user_id="u_carol", cell_count=4, candidate_booths=_BOOTHS,
                pre_survey={"interest_categories": ["cat_a", "cat_b"]},
            ),
        ),
        dict(
            key="s4a",
            title="④-A 同じ候補・参加者 alice（第1希望 cat_a）",
            intent="参加者ごとに結果が変わることの確認（その1）。",
            watch="④-B と assigned が異なること。cat_a が上位。",
            request=dict(
                user_id="u_alice", cell_count=4, candidate_booths=_BOOTHS,
                pre_survey={"interest_categories": ["cat_a"], "top_interest_category": "cat_a"},
            ),
        ),
        dict(
            key="s4b",
            title="④-B 同じ候補・参加者 dave（第1希望 cat_x）",
            intent="参加者ごとに結果が変わることの確認（その2）。",
            watch="④-A と assigned が異なること。cat_x が上位。",
            request=dict(
                user_id="u_dave", cell_count=4, candidate_booths=_BOOTHS,
                pre_survey={"interest_categories": ["cat_x"], "top_interest_category": "cat_x"},
            ),
        ),
        dict(
            key="s5",
            title="⑤ 候補が cell_count より少ない",
            intent="候補2件・cell_count=6。",
            watch="assigned が2件のまま返ること（人気ブースで無理に埋めない・O-4）。",
            request=dict(
                user_id="u_erin", cell_count=6,
                candidate_booths=[
                    {"booth_id": "only_a", "category_id": "cat_a", "visitor_count": 30},
                    {"booth_id": "only_b", "category_id": "cat_b", "visitor_count": 5},
                ],
                pre_survey={"interest_categories": ["cat_a"], "top_interest_category": "cat_a"},
            ),
        ),
    ]


def run_scenarios(overrides: dict | None = None) -> list[ScenarioResult]:
    settings = _settings(overrides)
    results: list[ScenarioResult] = []
    by_key: dict[str, ScenarioResult] = {}

    for d in _scenario_defs():
        req = RecommendRequest.model_validate(d["request"])
        # kind を分け、合成 ID の推薦が研究ログ (kind: "recommend") に混ざらないようにする
        # (ADR 0008 §1)。
        resp = run_recommendation(
            req, settings=settings, rule_cache=RuleCache(), log_kind="recommend_demo"
        )
        rows = []
        for s in sorted(resp.scores, key=lambda s: s.rank_in_event):
            raw = s.attributes.get("raw", {})
            rows.append(
                dict(
                    rank=s.rank_in_event, booth_id=s.booth_id, category=raw.get("category_id"),
                    visitor_count=raw.get("visitor_count"), interest_match=s.interest_match,
                    coverage_term=raw.get("coverage_term"), interest_term=raw.get("interest_term"),
                    score=s.score, assigned=s.was_assigned,
                )
            )
        sr = ScenarioResult(
            key=d["key"], title=d["title"], intent=d["intent"], watch=d["watch"],
            request=d["request"], rows=rows, assigned_ids=[a.booth_id for a in resp.assigned],
            phase=resp.phase, decision_table_size=resp.decision_table_size,
        )
        sr.rho = spearman([r["visitor_count"] for r in rows], [r["score"] for r in rows])
        results.append(sr)
        by_key[d["key"]] = sr

    _attach_checks(by_key)
    return results


def _monotonic_within_group(rows: list[dict]) -> bool:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["interest_match"], []).append(r)
    for g in groups.values():
        vc = [r["visitor_count"] for r in sorted(g, key=lambda r: r["rank"])]
        if any(a > b for a, b in zip(vc, vc[1:])):
            return False
    return True


def _attach_checks(by_key: dict[str, ScenarioResult]) -> None:
    for sr in by_key.values():
        c = sr.checks
        c.append(("score と visitor_count の順位相関が正でない (P-1)", sr.rho <= 1e-9,
                  f"Spearman ρ = {sr.rho:+.3f}"))
        c.append(("MISMATCH 候補の score が 0 でない (P-5)",
                  all(r["score"] > 0 for r in sr.rows if r["interest_match"] == "MISMATCH")
                  or not any(r["interest_match"] == "MISMATCH" for r in sr.rows),
                  "不一致カテゴリが構造的に排除されていない"))
        c.append(("同じ interest_match 内で訪問者数が少ない順 (P-6)", _monotonic_within_group(sr.rows), ""))
        c.append(("scores は候補全件 (C-2)", len(sr.rows) == len(sr.request["candidate_booths"]),
                  f"candidates={len(sr.request['candidate_booths'])}, scores={len(sr.rows)}"))

    if "s1" in by_key:
        sr = by_key["s1"]
        first = sr.assigned_ids[0] if sr.assigned_ids else None
        sr.checks.append(("突出人気ブース b_pop が assigned の先頭でない (P-3)", first != "b_pop",
                          f"先頭 = {first}"))
    if "s2" in by_key:
        sr = by_key["s2"]
        sr.checks.append(("未回答なら全候補 UNKNOWN",
                          all(r["interest_match"] == "UNKNOWN" for r in sr.rows), ""))
        by_vc = [r["booth_id"] for r in sorted(sr.rows, key=lambda r: r["visitor_count"])]
        by_rank = [r["booth_id"] for r in sorted(sr.rows, key=lambda r: r["rank"])]
        sr.checks.append(("未回答なら rank が visitor_count 昇順に完全一致", by_vc == by_rank, ""))
    if "s3" in by_key:
        kinds = {r["interest_match"] for r in by_key["s3"].rows}
        by_key["s3"].checks.append(("第1希望の設問が無ければ PARTIAL が出ない (F-5)", "PARTIAL" not in kinds, ""))
    if "s4a" in by_key and "s4b" in by_key:
        a, b = by_key["s4a"], by_key["s4b"]
        detail = f"A={a.assigned_ids} / B={b.assigned_ids}"
        for sr in (a, b):
            sr.checks.append(("参加者が違えば assigned が変わる (P-4)", a.assigned_ids != b.assigned_ids, detail))
    if "s5" in by_key:
        sr = by_key["s5"]
        sr.checks.append(("候補不足でも無理に埋めない (O-4)", len(sr.assigned_ids) == 2,
                          f"assigned={sr.assigned_ids}"))


# --------------------------------------------------------------------------- #
# DRSA コアのデモ
# --------------------------------------------------------------------------- #
_DRSA_NAMES = ("preference_match", "rating_affinity")


def _synth_decision_table(n: int, noise: float, seed: int) -> DecisionTable:
    """埋め込む正解規則: pm>=3 -> HIGH, pm<=1 -> LOW（各 1-noise の確率）。"""
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
    consistency: float
    gamma: float
    gamma_ok: bool
    rules: list[dict]
    applied: list[dict]

    def to_dict(self) -> dict:
        return dict(n=self.n, consistency=self.consistency, gamma=self.gamma,
                    gamma_ok=self.gamma_ok, rules=self.rules, applied=self.applied)


def run_drsa_demo(
    levels: tuple[float, ...] = (1.0, 0.8, 0.7),
    *,
    min_support: int = 5,
    min_gamma: float = 0.5,
) -> list[DrsaDemo]:
    table = _synth_decision_table(n=150, noise=0.1, seed=20261016)
    demos: list[DrsaDemo] = []
    for l in levels:
        ap = approximate(table, consistency_level=l)
        rs = generate_rules(table, min_support=min_support, consistency_level=l)
        rules = [
            dict(id=r.id, text=r.as_text(), conclusion=r.conclusion, support=r.support,
                 confidence=round(r.confidence, 3))
            for r in rs.rules
        ]
        applied = []
        for pm in (3, 2, 1, 0):
            for ra in (1, 2, 3):
                up, down, matched = rs.apply({"preference_match": pm, "rating_affinity": ra})
                applied.append(dict(pm=pm, ra=ra, up=round(up, 3), down=round(down, 3),
                                    score=round((1 + up - down) / 2, 3), n_rules=len(matched)))
        demos.append(DrsaDemo(n=len(table), consistency=round(l, 3), gamma=round(ap.gamma, 3),
                              gamma_ok=ap.gamma >= min_gamma, rules=rules, applied=applied))
    return demos


# --------------------------------------------------------------------------- #
# ペイロード（JSON）
# --------------------------------------------------------------------------- #
def report_payload(overrides: dict | None = None) -> dict:
    params = effective_params(overrides)
    scenarios = run_scenarios(overrides)
    l = params["drsa_consistency"]
    levels = tuple(sorted({round(l, 2), 1.0}, reverse=True))
    drsa = run_drsa_demo(levels, min_support=int(params["min_support"]),
                         min_gamma=params["drsa_min_gamma"])
    total = sum(len(s.checks) for s in scenarios)
    passed = sum(1 for s in scenarios for _, ok, _ in s.checks if ok)
    return dict(
        params=params,
        param_specs=[vars(p) for p in PARAM_SPECS],
        overall=dict(passed=passed, total=total),
        scenarios=[s.to_dict() for s in scenarios],
        guard=[dict(title=s.title, rho=round(s.rho, 4), ok=s.rho <= 1e-9) for s in scenarios],
        drsa=[d.to_dict() for d in drsa],
    )


# --------------------------------------------------------------------------- #
# 静的 HTML（既定パラメータ）
# --------------------------------------------------------------------------- #
def _e(x: object) -> str:
    return html.escape(str(x))


def _fmt(x: object) -> str:
    return f"{x:.3f}" if isinstance(x, float) else _e(x)


def _badge(kind: str) -> str:
    return f'<span class="badge" style="background:{_COLOR.get(kind, "#777")}">{_e(kind)}</span>'


def _scatter_svg(rows: list[dict], rho: float) -> str:
    w, h, pad = 460, 260, 44
    xs = [r["visitor_count"] or 0 for r in rows]
    xmax = max(xs + [1])
    plot_w, plot_h = w - pad * 2, h - pad * 2

    def px(v: float) -> float:
        return pad + (v / xmax) * plot_w if xmax else pad

    def py(v: float) -> float:
        return pad + plot_h - v * plot_h

    dots = []
    for r in rows:
        col = _COLOR.get(r["interest_match"], "#777")
        dots.append(
            f'<circle cx="{px(r["visitor_count"] or 0):.1f}" cy="{py(r["score"] or 0):.1f}" r="6" '
            f'fill="{col}" fill-opacity="0.85" stroke="#0006"><title>{_e(r["booth_id"])}  '
            f'visitor={r["visitor_count"]}  score={r["score"]:.3f}  {_e(r["interest_match"])}</title></circle>'
        )
    grid = []
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        y = py(frac)
        grid.append(f'<line x1="{pad}" y1="{y:.1f}" x2="{w - pad}" y2="{y:.1f}" stroke="#8883"/>')
        grid.append(f'<text x="{pad - 6}" y="{y + 4:.1f}" text-anchor="end" class="tick">{frac:.2f}</text>')
    verdict = "OK（負 or 0：人気順ではない）" if rho <= 1e-9 else "要注意（正：人気順に相関）"
    vcol = "#2e7d32" if rho <= 1e-9 else "#c62828"
    return f"""<svg viewBox="0 0 {w} {h}" class="scatter" xmlns="http://www.w3.org/2000/svg">
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
        "<tr><th>rank</th><th>booth_id</th><th>category</th><th>visitor</th>"
        "<th>interest_match</th><th>coverage項</th><th>interest項</th><th>score</th><th>assigned</th></tr>"
    )
    body = ""
    for r in sr.rows:
        cls = ' class="assigned"' if r["assigned"] else ""
        body += (
            f"<tr{cls}><td>{r['rank']}</td><td>{_e(r['booth_id'])}</td><td>{_e(r['category'])}</td>"
            f"<td>{_e(r['visitor_count'])}</td><td>{_badge(r['interest_match'])}</td>"
            f"<td>{_fmt(r['coverage_term'])}</td><td>{_fmt(r['interest_term'])}</td>"
            f"<td><b>{_fmt(r['score'])}</b></td><td>{'✅' if r['assigned'] else ''}</td></tr>"
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
  <div class="cols"><table>{head}{body}</table><div>{_scatter_svg(sr.rows, sr.rho)}</div></div>
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
        note = (
            '<p class="watch">l=1.0（厳密版）ではノイズ入りデータの下方近似がほぼ空になり、'
            '規則が出ない・γ≈0 になります。<b>これは仕様どおり</b>で、だからこそ VC-DRSA'
            '（l を下げる）が必要です（docs/specs/05-drsa.md §2）。</p>' if not d.rules else ""
        )
        grid = ""
        for pm in (3, 2, 1, 0):
            cells = ""
            for ra in (1, 2, 3):
                a = next(x for x in d.applied if x["pm"] == pm and x["ra"] == ra)
                sh = int(255 - a["score"] * 120)
                cells += (
                    f'<td style="background:rgb({sh},{255 if a["score"]>=0.5 else sh},{sh})">'
                    f'{a["score"]:.2f}<br><small>↑{a["up"]:.2f} ↓{a["down"]:.2f}</small></td>'
                )
            grid += f"<tr><th>pm={pm}</th>{cells}</tr>"
        gate = "🟢 γ ゲート通過" if d.gamma_ok else "🔴 γ ゲート未達（DRSA 不採用）"
        blocks += f"""<section class="card">
  <h3>一貫性水準 l = {d.consistency}　（決定表 {d.n} 行・ノイズ 10%）</h3>
  <p class="meta">近似の質 γ = <code>{d.gamma}</code> — {gate}　規則数 = <code>{len(d.rules)}</code></p>
  {note}
  <div class="cols">
    <table><tr><th>id</th><th>規則</th><th>support</th><th>confidence</th></tr>{rule_rows}</table>
    <table class="applied"><tr><th></th><th>ra=1</th><th>ra=2</th><th>ra=3</th></tr>{grid}</table>
  </div>
</section>"""
    return blocks


_CSS = """
:root { color-scheme: light dark; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0 auto; max-width: 1100px;
       padding: 24px; line-height: 1.6; }
h1 { margin-bottom: 4px; } .sub { color: #888; margin-top: 0; }
.card { border: 1px solid #8884; border-radius: 10px; padding: 16px 20px; margin: 18px 0; background: #8881; }
.cols { display: flex; gap: 20px; flex-wrap: wrap; align-items: flex-start; }
.cols > * { flex: 1 1 420px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { border: 1px solid #8883; padding: 4px 8px; text-align: center; }
tr.assigned { background: #2e7d3222; font-weight: 600; }
.badge { color: #fff; padding: 1px 7px; border-radius: 999px; font-size: 11px; }
.checks { list-style: none; padding-left: 0; } .checks li { margin: 3px 0; }
.ok { color: #2e7d32; font-weight: 700; } .ng { color: #c62828; font-weight: 700; }
.intent { color: #999; margin: 2px 0; } .watch { margin: 6px 0; }
.meta { font-size: 12px; color: #999; }
code { background: #8882; padding: 1px 5px; border-radius: 4px; }
svg.scatter { width: 100%; height: auto; border: 1px solid #8883; border-radius: 8px; }
.tick, .axis, .rho { font-size: 11px; fill: #999; } .rho { font-weight: 700; }
table.applied td { font-size: 12px; color: #111; }
.banner { padding: 10px 16px; border-radius: 8px; font-weight: 700; font-size: 18px; background: #8882; }
"""


def build_report_html() -> str:
    payload = report_payload(None)
    scenarios = run_scenarios(None)
    drsa = run_drsa_demo()
    failed = payload["overall"]["total"] - payload["overall"]["passed"]
    overall = "🟢 すべて合格" if failed == 0 else f"🔴 {failed} / {payload['overall']['total']} 件が不合格"
    guard_rows = "".join(
        f"<tr><td>{_e(g['title'])}</td><td>{g['rho']:+.3f}</td>"
        f"<td>{'🟢 人気順でない' if g['ok'] else '🔴 人気順に相関'}</td></tr>"
        for g in payload["guard"]
    )
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>推薦エンジン 目視確認レポート</title><style>{_CSS}</style></head><body>
<h1>推薦エンジン 目視確認レポート</h1>
<p class="sub">既定パラメータでの静的スナップショット。パラメータを動かすには <code>/demo</code>（対話版）。</p>
<p class="banner">{overall}（{payload['overall']['passed']} / {payload['overall']['total']} 件）</p>
<h2>1. COVERAGE 推薦シナリオ</h2>
{''.join(_scenario_html(s) for s in scenarios)}
<h2>2. 人気順への退化ガード（全シナリオ総括）</h2>
<table><tr><th>シナリオ</th><th>Spearman ρ</th><th>判定</th></tr>{guard_rows}</table>
<h2>3. DRSA コア（単体デモ）</h2>
{_drsa_html(drsa)}
</body></html>"""


# --------------------------------------------------------------------------- #
# 対話ページ（/demo）
# --------------------------------------------------------------------------- #
def build_playground_html() -> str:
    specs_json = json.dumps([vars(p) for p in PARAM_SPECS], ensure_ascii=False)
    colors_json = json.dumps(_COLOR, ensure_ascii=False)
    css = _CSS + """
.layout { display: flex; gap: 20px; align-items: flex-start; }
.panel { position: sticky; top: 12px; flex: 0 0 300px; border: 1px solid #8884; border-radius: 10px;
         padding: 14px 16px; background: #8881; }
.panel h3 { margin: 12px 0 6px; font-size: 13px; color: #888; text-transform: uppercase; letter-spacing: .05em; }
.prow { margin: 10px 0; font-size: 13px; }
.prow label { display: flex; justify-content: space-between; gap: 8px; }
.prow input[type=range] { width: 100%; }
.pnote { color: #999; font-size: 11px; }
#results { flex: 1 1 auto; min-width: 0; }
button { font: inherit; padding: 6px 12px; border-radius: 6px; border: 1px solid #8886; cursor: pointer;
         background: #8882; }
.busy { opacity: .5; }
.warn { border: 1px solid #c62828; border-left-width: 5px; border-radius: 8px; padding: 10px 14px;
        margin: 12px 0 18px; background: #c628281a; font-size: 13px; line-height: 1.6; }
"""
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>推薦エンジン パラメータ プレイグラウンド</title><style>{css}</style></head><body>
<h1>推薦エンジン パラメータ プレイグラウンド</h1>
<div class="warn"><b>これはシミュレータです。ここで動かした値は本番に反映されません。</b><br>
本番のパラメータは Cloud Run の環境変数が正であり、変更するには新しいリビジョンのデプロイが必要です
（docs/decisions/adrs/0008 §3・parameter-tuning X-1）。<br>
また<b>当日に本番の観測を見ながらパラメータを変えてはいけません</b>。実験そのものが壊れます
（docs/specs/10-observability.md §5・X-2）。</div>
<p class="sub">合成シナリオ（実データではない）。スライダーを動かすと本物の Python エンジンで再計算します。
<b>目的は精度の最適化ではなく</b>、docs/specs/07-testing.md の不変条件（P-1〜P-6 など）が
どのパラメータ範囲まで崩れないかを見ることです。</p>
<div class="layout">
  <div class="panel" id="panel">
    <div id="controls"></div>
    <button id="reset">既定値に戻す</button>
    <p class="pnote" id="paramline"></p>
  </div>
  <div id="results">読み込み中…</div>
</div>
<script>
const SPECS = {specs_json};
const COLOR = {colors_json};
const state = {{}};
SPECS.forEach(s => state[s.key] = s.default);

const controls = document.getElementById('controls');
let currentGroup = '';
SPECS.forEach(s => {{
  if (s.group !== currentGroup) {{
    currentGroup = s.group;
    const h = document.createElement('h3'); h.textContent = s.group; controls.appendChild(h);
  }}
  const row = document.createElement('div'); row.className = 'prow';
  row.innerHTML = `<label><span>${{s.label}}</span><b id="v_${{s.key}}"></b></label>
    <input type="range" id="r_${{s.key}}" min="${{s.lo}}" max="${{s.hi}}" step="${{s.step}}" value="${{s.default}}">
    ${{s.note ? `<div class="pnote">${{s.note}}</div>` : ''}}`;
  controls.appendChild(row);
  const input = row.querySelector('input');
  input.addEventListener('input', () => {{ state[s.key] = parseFloat(input.value); refreshLabels(); schedule(); }});
}});
document.getElementById('reset').addEventListener('click', () => {{
  SPECS.forEach(s => {{ state[s.key] = s.default; document.getElementById('r_'+s.key).value = s.default; }});
  refreshLabels(); schedule();
}});

function refreshLabels() {{
  SPECS.forEach(s => {{
    const n = s.step >= 1 ? 0 : 2;
    document.getElementById('v_'+s.key).textContent = Number(state[s.key]).toFixed(n);
  }});
  document.getElementById('paramline').textContent = JSON.stringify(state);
}}

let timer = null;
function schedule() {{ clearTimeout(timer); timer = setTimeout(run, 180); }}

async function run() {{
  const results = document.getElementById('results');
  results.classList.add('busy');
  try {{
    const res = await fetch('demo/run', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{overrides: state}})
    }});
    render(await res.json());
  }} catch (e) {{
    results.textContent = 'エラー: ' + e;
  }}
  results.classList.remove('busy');
}}

function esc(x) {{ return String(x).replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c])); }}
function f3(x) {{ return (x==null) ? '' : Number(x).toFixed(3); }}
function badge(k) {{ return `<span class="badge" style="background:${{COLOR[k]||'#777'}}">${{k}}</span>`; }}

function scatter(rows, rho) {{
  const w=460,h=260,pad=44, xmax=Math.max(1,...rows.map(r=>r.visitor_count||0));
  const px=v=> pad + (v/xmax)*(w-2*pad), py=v=> pad + (h-2*pad) - v*(h-2*pad);
  let g='';
  [0,.25,.5,.75,1].forEach(fr=>{{ const y=py(fr);
    g+=`<line x1="${{pad}}" y1="${{y}}" x2="${{w-pad}}" y2="${{y}}" stroke="#8883"/>`;
    g+=`<text x="${{pad-6}}" y="${{y+4}}" text-anchor="end" class="tick">${{fr.toFixed(2)}}</text>`; }});
  const dots = rows.map(r=>`<circle cx="${{px(r.visitor_count||0).toFixed(1)}}" cy="${{py(r.score||0).toFixed(1)}}" r="6"
     fill="${{COLOR[r.interest_match]||'#777'}}" fill-opacity="0.85" stroke="#0006">
     <title>${{esc(r.booth_id)}}  visitor=${{r.visitor_count}}  score=${{f3(r.score)}}  ${{r.interest_match}}</title></circle>`).join('');
  const okColor = rho<=1e-9 ? '#2e7d32' : '#c62828';
  const verdict = rho<=1e-9 ? 'OK（負 or 0：人気順ではない）' : '要注意（正：人気順に相関）';
  return `<svg viewBox="0 0 ${{w}} ${{h}}" class="scatter" xmlns="http://www.w3.org/2000/svg">${{g}}
    <line x1="${{pad}}" y1="${{pad}}" x2="${{pad}}" y2="${{h-pad}}" stroke="#8886"/>
    <line x1="${{pad}}" y1="${{h-pad}}" x2="${{w-pad}}" y2="${{h-pad}}" stroke="#8886"/>
    <text x="${{w/2}}" y="${{h-8}}" text-anchor="middle" class="axis">visitor_count →（0〜${{xmax}}）</text>
    <text x="14" y="${{h/2}}" text-anchor="middle" class="axis" transform="rotate(-90 14 ${{h/2}})">score ↑</text>
    ${{dots}}
    <text x="${{w-pad}}" y="${{pad-14}}" text-anchor="end" class="rho" fill="${{okColor}}">Spearman ρ = ${{rho>=0?'+':''}}${{rho.toFixed(3)}} — ${{verdict}}</text>
  </svg>`;
}}

function scenarioCard(s) {{
  const rows = s.rows.map(r=>`<tr class="${{r.assigned?'assigned':''}}">
    <td>${{r.rank}}</td><td>${{esc(r.booth_id)}}</td><td>${{esc(r.category)}}</td><td>${{r.visitor_count}}</td>
    <td>${{badge(r.interest_match)}}</td><td>${{f3(r.coverage_term)}}</td><td>${{f3(r.interest_term)}}</td>
    <td><b>${{f3(r.score)}}</b></td><td>${{r.assigned?'✅':''}}</td></tr>`).join('');
  const checks = s.checks.map(c=>`<li><span class="${{c.ok?'ok':'ng'}}">${{c.ok?'✓':'✗'}}</span> ${{esc(c.name)}}
    ${{c.detail?` — <code>${{esc(c.detail)}}</code>`:''}}</li>`).join('');
  return `<section class="card"><h3>${{esc(s.title)}}</h3>
    <p class="intent">${{esc(s.intent)}}</p>
    <p class="watch"><b>見るべき点：</b>${{esc(s.watch)}}</p>
    <p class="meta">phase=<code>${{esc(s.phase)}}</code>　assigned=<code>${{esc(JSON.stringify(s.assigned_ids))}}</code></p>
    <div class="cols">
      <table><tr><th>rank</th><th>booth_id</th><th>category</th><th>visitor</th><th>interest_match</th>
        <th>coverage項</th><th>interest項</th><th>score</th><th>assigned</th></tr>${{rows}}</table>
      <div>${{scatter(s.rows, s.rho)}}</div>
    </div>
    <ul class="checks">${{checks}}</ul></section>`;
}}

function drsaCard(d) {{
  const rules = d.rules.length ? d.rules.map(r=>`<tr><td><code>${{esc(r.id)}}</code></td><td>${{esc(r.text)}}</td>
    <td>${{r.support}}</td><td>${{r.confidence}}</td></tr>`).join('')
    : '<tr><td colspan="4"><i>規則なし</i></td></tr>';
  let grid='';
  [3,2,1,0].forEach(pm=>{{ let cells='';
    [1,2,3].forEach(ra=>{{ const a=d.applied.find(x=>x.pm===pm&&x.ra===ra); const sh=Math.round(255-a.score*120);
      cells+=`<td style="background:rgb(${{sh}},${{a.score>=0.5?255:sh}},${{sh}})">${{a.score.toFixed(2)}}
        <br><small>↑${{a.up.toFixed(2)}} ↓${{a.down.toFixed(2)}}</small></td>`; }});
    grid+=`<tr><th>pm=${{pm}}</th>${{cells}}</tr>`; }});
  const gate = d.gamma_ok ? '🟢 γ ゲート通過' : '🔴 γ ゲート未達（DRSA 不採用）';
  const note = d.rules.length ? '' : `<p class="watch">l=1.0（厳密版）ではノイズ入りデータの下方近似がほぼ空になり、
    規則が出ず γ≈0 になります。<b>これは仕様どおり</b>で、だからこそ VC-DRSA（l を下げる）が要ります。</p>`;
  return `<section class="card"><h3>一貫性水準 l = ${{d.consistency}}　（決定表 ${{d.n}} 行・ノイズ 10%）</h3>
    <p class="meta">近似の質 γ = <code>${{d.gamma}}</code> — ${{gate}}　規則数 = <code>${{d.rules.length}}</code></p>
    ${{note}}
    <div class="cols">
      <table><tr><th>id</th><th>規則</th><th>support</th><th>confidence</th></tr>${{rules}}</table>
      <table class="applied"><tr><th></th><th>ra=1</th><th>ra=2</th><th>ra=3</th></tr>${{grid}}</table>
    </div></section>`;
}}

function render(p) {{
  const failed = p.overall.total - p.overall.passed;
  const banner = failed === 0
    ? `🟢 すべて合格（${{p.overall.passed}} / ${{p.overall.total}} 件）`
    : `🔴 ${{failed}} / ${{p.overall.total}} 件が不合格 — この設定は不変条件を壊します`;
  const guard = p.guard.map(g=>`<tr><td>${{esc(g.title)}}</td><td>${{g.rho>=0?'+':''}}${{g.rho.toFixed(3)}}</td>
    <td>${{g.ok?'🟢 人気順でない':'🔴 人気順に相関'}}</td></tr>`).join('');
  document.getElementById('results').innerHTML = `
    <p class="banner">${{banner}}</p>
    <h2>1. COVERAGE 推薦シナリオ</h2>
    ${{p.scenarios.map(scenarioCard).join('')}}
    <h2>2. 人気順への退化ガード（全シナリオ総括）</h2>
    <table><tr><th>シナリオ</th><th>Spearman ρ</th><th>判定</th></tr>${{guard}}</table>
    <h2>3. DRSA コア（単体デモ）</h2>
    ${{p.drsa.map(drsaCard).join('')}}`;
}}

refreshLabels();
run();
</script>
</body></html>"""
