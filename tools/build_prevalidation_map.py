"""事前検証 §8.1 の「地図」を生成する (docs/specs/07-testing.md §8.1)。

    .venv/Scripts/python.exe tools/build_prevalidation_map.py
    .venv/Scripts/python.exe tools/build_prevalidation_map.py path.md   # 出力先を指定

決定表の件数 × ノイズ率のグリッドで、埋め込んだ規則
(preference_match >= 3 -> HIGH / preference_match <= 1 -> LOW) が
DRSA で復元されるかを測る。シードを変えて複数試行し、ばらつき
（中央値・復元できた試行の割合）を出す。

**新しい判断はしない。** 品質ゲートのしきい値（3本 / γ 0.5 / 被覆率 0.5）も
フェーズしきい値（30 / 60）も変更しない (docs/README.md 未決定事項 RD-2,
docs/specs/03-phases.md §3.1)。埋め込み規則とノイズの作り方も動かさない。
出た数値をそのまま書く。

engine は合成データのみを使うので DB 接続も .env も不要。決定的（シード固定）。
同じコマンドで同じ表が出る。
"""

from __future__ import annotations

import random
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from event_support_recommend.drsa import DecisionTable, generate_rules  # noqa: E402
from event_support_recommend.drsa.approximation import gamma as gamma_of  # noqa: E402
from event_support_recommend.drsa.decision_table import DecisionRow  # noqa: E402
from event_support_recommend.models import DecisionClass  # noqa: E402
from event_support_recommend.settings import Settings  # noqa: E402

NAMES = ("preference_match", "rating_affinity")

# グリッド。件数は 03-phases.md のフェーズしきい値（30 / 60）と正本（180）に対応。
SIZES = (30, 60, 120, 180)
NOISES = (0.05, 0.20, 0.40)

# 1セルあたりの試行数。
#
# 25 では足りない。25 試行だと γ 中央値が件数方向に 0.79〜1.00 と乱高下し、
# 復元率も 20% ノイズ帯で 72% → 52% → 60% と非単調に見えるが、
# **これはすべて標本ばらつきで、試行数を上げると消える**（実測で確認）。
# 少ない試行で出た数字から「頭打ち」などと読むと、事実でないものを
# 事実として仕様書に書くことになる。
#
# 400 試行での split-half（試行 0-199 と 200-399 を別集計）では
# γ 中央値が ±0.02、ゲート通過率が数ポイント、復元率が最大 ±7pt
# （20% ノイズ帯が最もばらつく）で一致した。この粒度なら
# 「復元率 5% 刻みの大小」は語れないが、帯ごとの傾向は語れる。
#
# 全12セルでおよそ 100 秒かかる。イベント前に1回回すもの (07-testing.md §8) なので許容する。
TRIALS = 400

# DRSA のパラメータは本番の既定値をそのまま使う（settings.py = 08-architecture §4）。
# ここで別の値を使うと「品質ゲートの根拠」という §8.1 の目的から外れる。
_S = Settings(_env_file=None)
MIN_SUPPORT = _S.min_support          # 既定 5
CONSISTENCY = _S.drsa_consistency     # 既定 0.8
GATE_MIN_RULES = _S.drsa_min_rules    # 既定 3（判定材料。しきい値の変更はしない）
GATE_MIN_GAMMA = _S.drsa_min_gamma    # 既定 0.5


def _synth(n: int, noise: float, seed: int) -> DecisionTable:
    """正解の規則を埋め込む: pm>=3 -> HIGH, pm<=1 -> LOW（各 (1-noise) の確率）。

    tests/test_drsa_prevalidation.py の _synth と同一。ここを変えると測定が意味を失う。
    """
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        pm = rng.randint(0, 3)
        ra = rng.randint(1, 3)
        if pm >= 3:
            base = DecisionClass.HIGH
        elif pm <= 1:
            base = DecisionClass.LOW
        else:
            base = rng.choice([DecisionClass.HIGH, DecisionClass.LOW])
        if rng.random() < noise:
            base = DecisionClass.LOW if base == DecisionClass.HIGH else DecisionClass.HIGH
        rows.append(DecisionRow((pm, ra), base))
    return DecisionTable(NAMES, rows)


def _seed(n: int, noise: float, trial: int) -> int:
    """決定的なシード。件数・ノイズ・試行番号だけで決まる。"""
    return (n * 1_000_003) ^ (int(round(noise * 100)) * 9_973) ^ (trial * 31)


def _up_has_pm_ge3(rules) -> bool:
    return any(
        r.conclusion == ">=HIGH"
        and any(c.attribute == "preference_match" and c.threshold >= 3 for c in r.conditions)
        for r in rules
    )


def _down_has_pm_le1(rules) -> bool:
    return any(
        r.conclusion == "<=LOW"
        and any(c.attribute == "preference_match" and c.threshold <= 1 for c in r.conditions)
        for r in rules
    )


@dataclass
class Cell:
    n: int
    noise: float
    recovered: int          # 埋め込み規則を両方向とも復元できた試行数
    up_only: int            # 上方 (pm>=3 -> HIGH) だけ復元できた試行数
    down_only: int          # 下方 (pm<=1 -> LOW) だけ復元できた試行数
    gammas: list[float]
    n_up: list[int]
    n_down: list[int]
    n_rules: list[int]
    gate_pass: int          # rules>=3 かつ γ>=0.5 を満たした試行数（被覆率は候補が要るため対象外）

    @property
    def recover_rate(self) -> float:
        return self.recovered / TRIALS

    @property
    def gamma_med(self) -> float:
        return statistics.median(self.gammas)

    @property
    def up_med(self) -> float:
        return statistics.median(self.n_up)

    @property
    def down_med(self) -> float:
        return statistics.median(self.n_down)

    @property
    def rules_med(self) -> float:
        return statistics.median(self.n_rules)


def measure(n: int, noise: float) -> Cell:
    cell = Cell(n, noise, 0, 0, 0, [], [], [], [], 0)
    for t in range(TRIALS):
        table = _synth(n, noise, _seed(n, noise, t))
        rs = generate_rules(table, min_support=MIN_SUPPORT, consistency_level=CONSISTENCY)
        g = gamma_of(table, CONSISTENCY)
        up_ok = _up_has_pm_ge3(rs.rules)
        down_ok = _down_has_pm_le1(rs.rules)
        if up_ok and down_ok:
            cell.recovered += 1
        elif up_ok:
            cell.up_only += 1
        elif down_ok:
            cell.down_only += 1
        cell.gammas.append(g)
        cell.n_up.append(len(rs.certain_up))
        cell.n_down.append(len(rs.certain_down))
        cell.n_rules.append(len(rs.rules))
        if len(rs.rules) >= GATE_MIN_RULES and g >= GATE_MIN_GAMMA:
            cell.gate_pass += 1
    return cell


def build_markdown(cells: list[Cell]) -> str:
    by = {(c.n, c.noise): c for c in cells}
    L: list[str] = []
    L.append("# 事前検証 §8.1 — 埋め込み規則テストの地図")
    L.append("")
    L.append(
        "生成: `.venv/Scripts/python.exe tools/build_prevalidation_map.py`"
        "（決定的・シード固定。再実行すると同じ表が出る）"
    )
    L.append("")
    L.append(
        f"- 埋め込み規則: `preference_match >= 3 → HIGH` / `preference_match <= 1 → LOW`"
        f"（各 (1 − noise) の確率、`preference_match ∈ {{0,1,2,3}}` は一様、"
        f"`rating_affinity ∈ {{1,2,3}}` は無関係なダミー）"
    )
    L.append(f"- DRSA: `min_support={MIN_SUPPORT}` / `consistency_level={CONSISTENCY}`（本番の既定値）")
    L.append(
        f"- 1セルあたり {TRIALS} 試行（シードは件数・ノイズ・試行番号で決まる）。"
        "split-half で γ中央値 ±0.02・復元率 最大 ±7pt の一致。"
        "**復元率の数ポイント差は読まないこと**"
    )
    L.append(
        "- 「復元」= 上方規則に `preference_match >= 3 → >=HIGH` が **かつ** "
        "下方規則に `preference_match <= 1 → <=LOW` が現れた試行"
    )
    L.append("")
    L.append("## 復元率（両方向とも復元できた試行の割合）")
    L.append("")
    L.append("| 決定表の件数 | ノイズ 5% | ノイズ 20% | ノイズ 40% |")
    L.append("|---:|:-:|:-:|:-:|")
    for n in SIZES:
        cs = [by[(n, x)] for x in NOISES]
        L.append(
            f"| {n} | "
            + " | ".join(f"{c.recover_rate:.0%}" for c in cs)
            + " |"
        )
    L.append("")
    L.append("## γ の中央値")
    L.append("")
    L.append("| 決定表の件数 | ノイズ 5% | ノイズ 20% | ノイズ 40% |")
    L.append("|---:|:-:|:-:|:-:|")
    for n in SIZES:
        cs = [by[(n, x)] for x in NOISES]
        L.append(f"| {n} | " + " | ".join(f"{c.gamma_med:.2f}" for c in cs) + " |")
    L.append("")
    L.append("## 確実規則の本数（中央値 up / down）")
    L.append("")
    L.append("| 決定表の件数 | ノイズ 5% | ノイズ 20% | ノイズ 40% |")
    L.append("|---:|:-:|:-:|:-:|")
    for n in SIZES:
        cs = [by[(n, x)] for x in NOISES]
        L.append(
            f"| {n} | "
            + " | ".join(f"{c.up_med:g} / {c.down_med:g}" for c in cs)
            + " |"
        )
    L.append("")
    L.append("## セル詳細")
    L.append("")
    L.append(
        "| 件数 | ノイズ | 復元率 | 上方のみ | 下方のみ | γ中央値 | "
        "規則計(中央値) | up(中央値) | down(中央値) | 暫定ゲート通過 |"
    )
    L.append("|---:|---:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|")
    for n in SIZES:
        for x in NOISES:
            c = by[(n, x)]
            L.append(
                f"| {n} | {x:.0%} | {c.recover_rate:.0%} | "
                f"{c.up_only}/{TRIALS} | {c.down_only}/{TRIALS} | {c.gamma_med:.2f} | "
                f"{c.rules_med:g} | {c.up_med:g} | {c.down_med:g} | {c.gate_pass}/{TRIALS} |"
            )
    L.append("")
    L.append("> 「暫定ゲート通過」= 現在の暫定しきい値（規則 ≥ 3・γ ≥ 0.5）を満たした試行数。")
    L.append("> 被覆率（第4条件）は候補ベクトルが要るためここでは測っていない。")
    L.append("> **この列はしきい値の妥当性を判断するための材料であり、しきい値の提案ではない**")
    L.append("> （RD-2 は未決定 / `AGENTS.md` 禁止6）。")
    L.append("")
    return "\n".join(L)


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "out" / "prevalidation_map.md"
    out.parent.mkdir(parents=True, exist_ok=True)

    cells = [measure(n, x) for n in SIZES for x in NOISES]
    out.write_text(build_markdown(cells), encoding="utf-8")
    print(f"wrote {out.resolve()}")

    # 端末にも要約を出す。
    print(f"\n{'n':>4} {'noise':>6} {'recover':>8} {'gamma_med':>10} {'up_med':>7} {'down_med':>9} {'rules_med':>10}")
    for c in cells:
        print(
            f"{c.n:>4} {c.noise:>6.2f} {c.recover_rate:>7.0%} "
            f"{c.gamma_med:>10.3f} {c.up_med:>7g} {c.down_med:>9g} {c.rules_med:>10g}"
        )


if __name__ == "__main__":
    main()
