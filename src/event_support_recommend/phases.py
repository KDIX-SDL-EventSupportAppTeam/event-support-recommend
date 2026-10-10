"""フェーズ判定 (docs/specs/03-phases.md)。純関数。

この仕組みは精度を上げるためではなく、「データが足りないのに賢いふりをする」ことを
構造的に禁じるためにある。迷ったら上げない側に倒す。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .models import Phase
from .settings import Settings


@dataclass(frozen=True)
class GateDetail:
    size: bool
    rules: bool
    gamma: bool
    coverage: bool

    def as_dict(self) -> dict[str, bool]:
        return {"size": self.size, "rules": self.rules, "gamma": self.gamma, "coverage": self.coverage}


@dataclass(frozen=True)
class GateResult:
    passed: bool
    detail: GateDetail


def _finite_int(value: object) -> int | None:
    """decision_table_size を安全に int へ。測れなかった/壊れた入力は None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        value = int(value)
    if isinstance(value, int):
        return value
    return None


# 浮動小数の丸め（0.6 - 0.5 = 0.0999…）で境界の 0.10 ちょうどを落とさないための許容。
_LIFT_EPS = 1e-9


def count_effective_rules(
    ruleset: object | None,
    base_rate_high: float | None,
    base_rate_low: float | None,
    min_lift: float,
) -> int:
    """品質ゲートが数える「有効な確実規則」の本数。

    上方規則（>=HIGH）は 確信度 − 全体の HIGH 率、下方規則（<=LOW）は 確信度 − 全体の LOW 率 が
    `min_lift` 以上のものだけを数える。基準率が無い（決定表が空）なら 0 本。

    γ は予測力を測らない。基準率を上回らない規則（どの条件でも同じくらい当たる規則）は、
    好みと無関係な「偽の規則」でありうるので数えない (docs/specs/03-phases.md §3.3)。
    `ruleset` は `certain_up` / `certain_down`（各 Rule は `confidence` を持つ）を備えたもの。
    """
    if ruleset is None:
        return 0
    n = 0
    if base_rate_high is not None:
        n += sum(
            1 for r in ruleset.certain_up if r.confidence - base_rate_high >= min_lift - _LIFT_EPS
        )
    if base_rate_low is not None:
        n += sum(
            1 for r in ruleset.certain_down if r.confidence - base_rate_low >= min_lift - _LIFT_EPS
        )
    return n


def evaluate_quality_gate(
    decision_table_size: object,
    effective_rules_count: int,
    gamma: float,
    candidate_coverage: float,
    settings: Settings,
) -> GateResult:
    """DRSA へ昇格する4条件 (AND) を項目別に評価する (docs/specs/03-phases.md §3.3)。

    1. 決定表の件数         >= PHASE_DRSA_MIN
    2. 有効な確実規則の本数 >= DRSA_MIN_RULES（`count_effective_rules`: 上下合わせ、リフト条件つき）
    3. 近似の質 γ           >= DRSA_MIN_GAMMA
    4. 規則が候補を覆う割合 >= DRSA_MIN_COVERAGE
    """
    size = _finite_int(decision_table_size)
    detail = GateDetail(
        size=size is not None and size >= settings.phase_drsa_min,
        rules=effective_rules_count >= settings.drsa_min_rules,
        gamma=(not math.isnan(gamma)) and gamma >= settings.drsa_min_gamma,
        coverage=(not math.isnan(candidate_coverage))
        and candidate_coverage >= settings.drsa_min_coverage,
    )
    passed = detail.size and detail.rules and detail.gamma and detail.coverage
    return GateResult(passed=passed, detail=detail)


def decide_phase(
    decision_table_size: object,
    settings: Settings,
    *,
    gate: GateResult | None = None,
) -> Phase:
    """決定表の件数から判定フェーズを返す。

    - None / 負数 / NaN / 0 …… COVERAGE（例外を投げない）
    - < PHASE_SIMILARITY_MIN …… COVERAGE
    - < PHASE_DRSA_MIN …………… SIMILARITY
    - >= PHASE_DRSA_MIN ………… 品質ゲート通過なら DRSA、そうでなければ SIMILARITY

    ここが返すのは「判定結果」。実際に使えた戦略は退避のあとで決まる
    (docs/specs/04-strategies.md §5)。
    """
    size = _finite_int(decision_table_size)
    if size is None or size < settings.phase_similarity_min:
        return Phase.COVERAGE
    if size < settings.phase_drsa_min:
        return Phase.SIMILARITY
    if gate is not None and gate.passed:
        return Phase.DRSA
    return Phase.SIMILARITY
