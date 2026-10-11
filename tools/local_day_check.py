"""ローカルで 1 日分を流す通し確認 (issue #43)。

    python tools/local_day_check.py                 # 来場者 190 人・正常運転
    python tools/local_day_check.py --outage         # データ取得（プロキシ）が終日止まる場合
    python tools/local_day_check.py --visitors 120 --seed 7

DB も .env も不要。合成の来場者を、本番と同じコード経路（5 分ごとの `refresh_caches`、
解放ごとの `run_recommendation`）へ流して、次を表示する。

  - 開場直後はランダム、データが溜まると DRSA に切り替わる時刻
  - 関心外枠の数、マスを選んだ方法（RANDOM / DRSA / SER / EXPLORE）の内訳
  - 上位 5 ブースへの推薦の集中、スコアと訪問者数の順位相関（資料 6章 S-2〜S-4 の目安）
  - エラー件数、応答時間

**これは動作確認の道具であり、研究のシミュレーターではない。** 来場モデルは去年金曜の集計値
（訪問中央値 6 ブース・間隔中央値 9 分・13:30 ピーク）に粗く合わせただけで、数値は目安である。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from event_support_recommend.api.schemas import RecommendRequest  # noqa: E402
from event_support_recommend.cache import RuleCache, SnapshotCache  # noqa: E402
from event_support_recommend.demo import spearman  # noqa: E402
from event_support_recommend.engine import run_recommendation  # noqa: E402
from event_support_recommend.settings import Settings  # noqa: E402
from event_support_recommend.snapshot_build import refresh_caches  # noqa: E402

N_BOOTHS, N_CATS = 40, 8
OPEN, CLOSE = 9.5, 16.0  # 時
UNLOCK_AT_VISIT = {2: 2, 3: 4, 4: 6}  # 訪問 N 件目で外周を開ける: マス数
EVENT = "E"


class Snap:
    built = True
    built_at = None

    def __init__(self, tables):
        self.tables = tables


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--visitors", type=int, default=190)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--outage", action="store_true", help="データ取得が終日止まる（規則キャッシュが空のまま）")
    ap.add_argument("--follow", type=float, default=0.25, help="推薦マスに従って訪問する割合")
    a = ap.parse_args()
    rng = random.Random(a.seed)

    settings = Settings(_env_file=None, enabled_attributes=["preference_match", "rating_affinity"], ops_token="")
    rc, sc = RuleCache(), SnapshotCache()

    # --- 会場: 40 ブース・8 カテゴリ・人気差は最多/最少 約 5 倍 -------------------------------
    cats = [f"cat{c}" for c in range(N_CATS)]
    booths = [{"id": f"b{i}", "event_id": EVENT, "category_id": cats[i % N_CATS], "is_active": 1}
              for i in range(N_BOOTHS)]
    pop = {b["id"]: math.exp(rng.uniform(0, math.log(5.12))) for b in booths}
    quality = {b["id"]: rng.gauss(0, 0.5) for b in booths}
    cat_of = {b["id"]: b["category_id"] for b in booths}
    by_cat = defaultdict(list)
    for b in booths:
        by_cat[b["category_id"]].append(b["id"])

    # --- 来場者: 13:30 ピークの到着・関心 1〜3 分野（第 1 希望つき）・訪問数（中央値 6）---------------
    people = []
    for u in range(a.visitors):
        t = min(CLOSE - 1.5, max(OPEN, rng.gauss(13.0, 1.6)))
        interests = rng.sample(cats, rng.choice([1, 2, 3]))
        n_visits = max(2, min(14, int(rng.lognormvariate(math.log(6), 0.45))))
        # 「意外な好み」: 第 1 希望の人のうち一部は、特定の関心外カテゴリを意外と高く評価する
        surprise = rng.choice([c for c in cats if c not in interests]) if rng.random() < 0.6 else None
        people.append(dict(uid=f"u{u}", t=t, interests=interests, top=interests[0], n=n_visits,
                           surprise=surprise, visits=[], cells=[], rated=0))

    day0 = datetime(2026, 10, 16, tzinfo=timezone.utc)
    events = []  # (時刻[時], 種別, user_index)
    for i, p in enumerate(people):
        t = p["t"]
        for v in range(p["n"]):
            events.append((t, "visit", i))
            t += max(3 / 60, rng.lognormvariate(math.log(9 / 60), 0.5))
    events.sort()

    visit_count = Counter()
    checkins, ratings, cards, cells = [], [], [], []

    def rate(p, bid):
        c = cat_of[bid]
        util = quality[bid] + (1.0 if c in p["interests"] else 0.0) + (0.5 if c == p["top"] else 0.0)
        if c == p["surprise"]:
            util += 1.6
        util += rng.gauss(0, 1.0)
        cut = (-1.4, -0.3, 0.9)  # 4 段階。★4 は 3 つ目の閾値より上（★4 率は会場全体で 3〜4 割になるよう粗く合わせた）
        return 1 + sum(util > x for x in cut)

    def tables(now_h):
        users = [{"id": p["uid"], "role": "participant"} for p in people]
        surveys = [{"user_id": p["uid"], "event_id": EVENT, "age_range": "20s", "occupation": "student",
                    "custom_answers": {"interest_categories": p["interests"], "top_interest_category": p["top"]}}
                   for p in people if p["t"] <= now_h]
        return {"users": users, "booths": booths, "categories": [], "check_ins": list(checkins),
                "booth_ratings": list(ratings), "user_survey_answers": surveys,
                "bingo_cards": list(cards), "bingo_cells": list(cells)}

    # --- 1 日を流す -------------------------------------------------------------------------
    log = io.StringIO()
    errors = 0
    latencies = []
    recs = []  # 推薦ごとの結果
    phase_timeline = []  # (時刻, phase, decision_table_size, strategy)
    next_refresh = OPEN
    cid = 0
    for now_h, _kind, i in events:
        while not a.outage and next_refresh <= now_h:  # 5 分ごとの更新
            with contextlib.redirect_stdout(log):
                refresh_caches(Snap(tables(next_refresh)), settings=settings, rule_cache=rc, snapshot_cache=sc)
            next_refresh += 5 / 60
        p = people[i]
        visited = {v for v in p["visits"]}
        remaining = [b["id"] for b in booths if b["id"] not in visited]
        if not remaining:
            continue
        # 次に訪れるブース: 推薦マスに従う（follow）か、関心と人気で自発的に選ぶ
        open_cells = [c for c in p["cells"] if c not in visited]
        free = True
        if open_cells and rng.random() < a.follow:
            bid, free = rng.choice(open_cells), False
        else:
            w = [pop[b] * (3.0 if cat_of[b] in p["interests"] else 1.0) for b in remaining]
            bid = rng.choices(remaining, weights=w)[0]
        cid += 1
        when = (day0 + timedelta(hours=now_h)).isoformat()
        checkins.append({"id": f"c{cid}", "user_id": p["uid"], "booth_id": bid, "event_id": EVENT,
                         "visit_order": len(p["visits"]) + 1, "checked_in_at": when})
        cards.append({"id": f"card{i}", "event_id": EVENT, "user_id": p["uid"]}) if not p["visits"] else None
        cells.append({"card_id": f"card{i}", "booth_id": bid, "source": "FREE_VISIT" if free else "PRESURVEY"})
        visit_count[bid] += 1
        p["visits"].append(bid)
        if p["rated"] < 3 and rng.random() < (0.85 if not free else 0.6):
            ratings.append({"checkin_id": f"c{cid}", "user_id": p["uid"], "booth_id": bid, "event_id": EVENT,
                            "rating": rate(p, bid), "scale": 4})
            p["rated"] += 1

        k = len(p["visits"])
        if k in UNLOCK_AT_VISIT:  # 解放: 外周 2 → 4 → 6 マス
            cand = [b["id"] for b in booths if b["id"] not in p["visits"]]
            req = RecommendRequest.model_validate({
                "user_id": p["uid"], "cell_count": UNLOCK_AT_VISIT[k], "rating_scale": 4,
                "unlock_context": f"card{i}:{k}",
                "candidate_booths": [{"booth_id": b, "category_id": cat_of[b], "visitor_count": visit_count[b]}
                                     for b in cand],
                "pre_survey": {"interest_categories": p["interests"], "top_interest_category": p["top"]},
                "visited_booths": [
                    {"booth_id": c["booth_id"], "visit_order": n + 1,
                     "source": c["source"], "category_id": cat_of[c["booth_id"]]}
                    for n, c in enumerate(x for x in cells if x["card_id"] == f"card{i}")
                ],
            })
            state = type("S", (), {})()
            t0 = time.perf_counter()
            try:
                with contextlib.redirect_stdout(log):
                    resp = run_recommendation(req, settings=settings, rule_cache=rc, snapshot_cache=sc,
                                              app_state=state)
            except Exception as exc:  # 500 を返さない前提。出たら数える
                errors += 1
                print("ERROR", repr(exc))
                continue
            latencies.append((time.perf_counter() - t0) * 1000)
            strategies = [x.reason["strategy"] for x in resp.assigned]
            p["cells"] = [x.booth_id for x in resp.assigned]
            mm_n = sum(1 for x in resp.assigned if next(s for s in resp.scores if s.booth_id == x.booth_id)
                       .interest_match == "MISMATCH")
            recs.append(dict(
                t=now_h, k=UNLOCK_AT_VISIT[k], phase=resp.phase, size=resp.decision_table_size,
                strategies=strategies, mismatch=mm_n, n_scores=len(resp.scores), n_cand=len(cand),
                assigned=[x.booth_id for x in resp.assigned],
                rho=spearman([s.attributes["raw"]["visitor_count"] for s in resp.scores],
                             [s.score for s in resp.scores]),
            ))
    report(a, recs, errors, latencies, rc, log, people, visit_count, state if recs else None)


def hhmm(h: float) -> str:
    return f"{int(h):02d}:{int(round((h % 1) * 60)) % 60:02d}"


def report(a, recs, errors, latencies, rc, log, people, visit_count, last_state=None) -> None:
    print(f"== 通し確認（来場者 {a.visitors} 人・seed {a.seed}{'・データ取得が終日停止' if a.outage else ''}）==")
    print(f"推薦 {len(recs)} 回 / エラー {errors} 件 / scores 欠落 {sum(r['n_scores'] != r['n_cand'] for r in recs)} 回")
    if latencies:
        lat = sorted(latencies)
        print(f"応答時間: 中央値 {statistics.median(lat):.1f}ms / p95 {lat[int(len(lat) * .95) - 1]:.1f}ms / 最大 {lat[-1]:.1f}ms（1000ms 未満: {lat[-1] < 1000}）")

    # フェーズの切り替わり
    first_drsa = next((r for r in recs if "DRSA" in r["strategies"] or r["phase"] == "DRSA"), None)
    print("\n-- フェーズ --")
    print(f"契約の phase: {dict(Counter(r['phase'] for r in recs))}")
    print(f"DRSA が最初に動いた時刻: {hhmm(first_drsa['t']) if first_drsa else 'なし'}"
          f"（そのときの決定表 {first_drsa['size'] if first_drsa else '-'} 件）")
    drsa_phase = [r for r in recs if r["phase"] == "DRSA"]
    print(f"DRSA 期の解放: {len(drsa_phase)} / {len(recs)} = {len(drsa_phase) / max(1, len(recs)):.1%}")
    by_hour = defaultdict(lambda: [0, 0])
    for r in recs:
        by_hour[int(r["t"])][0] += 1
        by_hour[int(r["t"])][1] += r["phase"] == "DRSA"
    print("時間帯別 DRSA 割合: " + " ".join(f"{h}時 {v[1]}/{v[0]}" for h, v in sorted(by_hour.items())))

    lg = getattr(last_state, "last_gate", None)
    if lg is not None:
        print(f"最後の推薦時点のゲート: 通過={lg.gate.passed} 項目={lg.gate.detail.as_dict()} "
              f"有効規則={lg.effective_rules_count}/生の規則={lg.certain_rules_count} γ={lg.gamma:.2f} "
              f"被覆率={lg.candidate_coverage:.2f} 基準率(★4)={lg.base_rate_high}")

    # 関心外枠・方法の内訳
    print("\n-- 関心外枠とマスの選び方 --")
    exp = {2: 1, 4: 2, 6: 3}
    bad = [r for r in recs if r["mismatch"] < min(exp[r["k"]], r["n_cand"])]
    print(f"関心外枠の数: 解放 2/4/6 マスに対し 関心外マスが " + ", ".join(
        f"{k}マス→平均{statistics.mean([r['mismatch'] for r in recs if r['k'] == k]):.2f}" for k in (2, 4, 6)
        if any(r['k'] == k for r in recs)) + f"（期待 1/2/3 以上。下回った解放 {len(bad)} 回）")
    c = Counter(s for r in recs for s in r["strategies"])
    tot = sum(c.values())
    print("マスの内訳（全体）: " + " / ".join(f"{k} {c[k]} ({c[k] / tot:.0%})" for k in ("RANDOM", "DRSA", "SER", "EXPLORE")))
    cd = Counter(s for r in drsa_phase for s in r["strategies"])
    if drsa_phase:
        td = sum(cd.values())
        print("DRSA 期のマス内訳: " + " / ".join(f"{k} {cd[k]}" for k in ("RANDOM", "DRSA", "SER", "EXPLORE")))
    ser = rc.ser_state()
    print(f"セレンディピティ規則: {ser['count']} 本（関心外の評価 {ser['mismatch_rows']} 件）")
    for r in ser["rules"][:8]:
        print(f"  第1希望 {r['top_category']} × {r['booth_category']}: ★4率 {r['p']:.2f}（{r['n']}件・{r['persons']}人、カテゴリ自体 {r['category_rate']:.2f}）")
    snap = rc.snapshot_state()
    print(f"最終的な決定表 {snap['decision_table_size']} 件・γ {snap['gamma']}・基準率(★4) {snap['base_rate_high']}")

    # 人気ガード（資料 6章 S-2〜S-4 の目安）
    print("\n-- 人気順になっていないか（目安 S-2/S-3/S-4）--")
    assigned_all = [b for r in recs for b in r["assigned"]]
    top5 = {b for b, _ in visit_count.most_common(5)}  # 最終的に訪問者の多かった上位 5 ブース
    share = sum(b in top5 for b in assigned_all) / max(1, len(assigned_all))
    print(f"訪問者が最多の上位5ブースへの推薦の割合: {share:.1%}（目安 S-2: 25% 以下。均等なら 12.5%）")
    rhos = [r["rho"] for r in recs]
    print(f"スコアと訪問者数の順位相関（解放ごとの平均）: {statistics.mean(rhos):+.3f}（目安 S-4: +0.15 以下）")
    for ph in ("COVERAGE", "DRSA"):
        rr = [r["rho"] for r in recs if r["phase"] == ph]
        if rr:
            print(f"  {ph} 期: {statistics.mean(rr):+.3f}（{len(rr)} 回）")
    amount = Counter(assigned_all)
    pop_corr = spearman([visit_count[b["id"]] for b in [{"id": f"b{i}"} for i in range(N_BOOTHS)]],
                        [amount[f"b{i}"] for i in range(N_BOOTHS)])
    print(f"推薦された回数と最終訪問者数の順位相関: {pop_corr:+.3f}（参考。負なら人気ブースほど薦められていない。訪問済みは候補から外れるので自然に負へ寄る）")
    lines = [json.loads(l) for l in log.getvalue().splitlines() if l.startswith("{")]
    kinds = Counter(l["kind"] for l in lines)
    print(f"\nログ: {dict(kinds)}")


if __name__ == "__main__":
    main()
