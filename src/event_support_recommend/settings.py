"""実行時設定。しきい値をコードに直書きしない (docs/rules/coding.md)。

既定値は docs/specs/08-architecture.md §4 と一致させること。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import __version__

# ENABLED_ATTRIBUTES に3個目を足したときの PHASE_DRSA_MIN の既定
# (docs/specs/03-phases.md §3.1: 4 x 3 x 3 = 36 パターン x 各5件 = 180)。
_DRSA_MIN_FOR_3_ATTRS = 180
_DRSA_MIN_FOR_2_ATTRS = 60


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- フェーズ ---
    phase_similarity_min: int = 30
    phase_drsa_min: int = 60
    enabled_attributes: list[str] = Field(
        default_factory=lambda: ["preference_match", "rating_affinity"]
    )

    # --- 品質ゲート (docs/specs/03-phases.md §3.3) ---
    drsa_min_rules: int = 3
    drsa_min_gamma: float = 0.5
    drsa_min_coverage: float = 0.5

    # --- 実験 (docs/specs/09-research-design.md) ---
    experiment_split_enabled: bool = True
    experiment_arm_a: str = "DRSA"
    experiment_arm_b: str = "COVERAGE"

    # --- 評価 ---
    rating_scale_default: int = 4
    high_rating_ratio: float = 0.75
    low_rating_ratio: float = 0.25

    # --- 戦略 ---
    w_coverage: float = 0.5
    w_interest: float = 0.5
    # interest_match ごとのスコア寄与 (docs/specs/04-strategies.md §2)。
    # mismatch は 0 にしない (P-5): 0 だと不一致ブースが構造排除されセレンディピティが観測不能になる。
    interest_partial: float = 0.6
    interest_mismatch: float = 0.2
    similarity_neighbors: int = 20
    similarity_shrinkage: float = 5.0
    similarity_coverage_floor: float = 0.2
    drsa_coverage_floor: float = 0.2
    max_per_category: int = 0  # 0 = 無効

    # --- DRSA ---
    drsa_consistency: float = 0.8
    min_support: int = 5

    # --- 性能・キャッシュ ---
    snapshot_ttl_sec: int = 300
    rule_cache_ttl_sec: int = 300
    response_budget_ms: int = 600

    # --- 運用 ---
    log_raw_request: bool = False
    log_level: str = "info"
    ops_token: str = ""
    engine_version: str = ""

    @field_validator("enabled_attributes", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @property
    def resolved_engine_version(self) -> str:
        return self.engine_version or __version__

    @property
    def default_phase_drsa_min(self) -> int:
        """属性個数に連動した PHASE_DRSA_MIN の「既定」。

        明示的に環境変数で設定された値があればそちらが優先されるが、
        属性構成と齟齬がないかの点検に使う (docs/specs/03-phases.md §3.1)。
        """
        return (
            _DRSA_MIN_FOR_3_ATTRS
            if len(self.enabled_attributes) >= 3
            else _DRSA_MIN_FOR_2_ATTRS
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """テスト用。環境変数を変えたあとに呼ぶ。"""
    get_settings.cache_clear()
