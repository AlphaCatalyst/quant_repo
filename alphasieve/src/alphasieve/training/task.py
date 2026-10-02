"""TrainingTask: the declarative definition of a model-training task for one mandate (docs/19-training-tasks.md).

A task fixes the label, the sample, the features, the dev-only split, the candidate grid and the output contract
before any result is seen. Every full run of a task is one strategy-layer trial; changing any field is a new trial.
"""

import math
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from alphasieve.config import Settings
from alphasieve.errors import validation_error
from alphasieve.util import canonical_json, sha256_hex

MAX_CONFIGS = 12
MAX_SEEDS = 3
FORBIDDEN = re.compile(r"holdout|fresh", re.IGNORECASE)
LABEL_KINDS = ("regression_residual", "rank_residual", "event_car", "etf_relative_return", "residual_plus_basis")
MANDATE_LABELS = {"A": ("regression_residual", "rank_residual"), "C": ("event_car",),
                  "B": ("etf_relative_return",), "D": ("residual_plus_basis",)}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Label(_Model):
    kind: Literal["regression_residual", "rank_residual", "event_car", "etf_relative_return", "residual_plus_basis"]
    horizons: list[int] = Field(min_length=1)
    formula_version: str
    neutralize: list[Literal["industry", "log_circ_mv", "log_circ_mv_sq", "beta_60d"]] = Field(default_factory=list)
    mask: list[str] = Field(default_factory=list)
    winsorize: tuple[float, float] = (0.01, 0.99)
    standardize: Literal["cross_sectional_zscore", "cross_sectional_rank", "none"] = "cross_sectional_zscore"
    event_types: list[Literal["ws_report", "express", "forecast"]] = Field(default_factory=list)
    benchmark: str | None = None


class Sample(_Model):
    frequency: Literal["daily", "event", "weekly"]
    membership_asof: Literal["month_start", "date", "event_date"] = "month_start"
    min_history_years: float = 3.0
    min_names_per_date: int = 100
    min_train_rows: int = 50_000
    overlap: Literal["keep_with_purge", "non_overlapping"] = "keep_with_purge"
    train_stride: int = Field(1, ge=1, le=20)   # use every n-th date for training rows; scoring stays daily
    purge_days: int
    embargo_days: int


class EventSource(_Model):
    task_id: str
    trial_id: str = Field(pattern=r"^S-[0-9a-f]{12}$")


class ScoreSource(_Model):
    """Walk-forward scores of a completed dev run reused as-is, so a portfolio trial changes construction only."""
    task_id: str
    trial_id: str = Field(pattern=r"^S-[0-9a-f]{12}$")


class EtfMapping(_Model):
    """Current ETF holdings mapped to industries or used as a basket (docs/20 §4.1); fixed at ``mapping_asof``."""
    mode: Literal["industry_map", "basket_map"]
    mapping_asof: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class Features(_Model):
    factor_refs: list[str] | Literal["library"] = "library"
    panel_fields: list[str] = Field(default_factory=list)
    derived_fields: list[str] = Field(default_factory=list)
    event_source: EventSource | None = None
    event_lag_days: int = 1
    event_half_lives: list[int] = Field(default_factory=lambda: [5, 20, 60])
    etf_mapping: EtfMapping | None = None
    preprocess: Literal["industry_rank_then_zscore", "rank_zscore", "event_type_zscore", "residual_zscore"] = \
        "industry_rank_then_zscore"
    missing_policy: Literal["median_plus_indicator", "semantic_neutral", "drop"] = "median_plus_indicator"
    min_feature_coverage: float = 0.8


class Split(_Model):
    tier: Literal["dev"] = "dev"
    window: Literal["rolling", "expanding"] = "rolling"
    train_years: float = 5.0
    warmup_years: float = 2.0
    retrain: Literal["monthly", "quarterly", "weekly"] = "monthly"
    select_every: Literal["yearly", "never"] = "yearly"
    inner_folds: int = Field(3, ge=2, le=5)


class ModelSpec(_Model):
    family: Literal["ridge", "lgbm", "lambdarank"]
    grid: dict[str, list] = Field(default_factory=dict)


class Search(_Model):
    max_configs: int = Field(MAX_CONFIGS, ge=1, le=MAX_CONFIGS)
    selection_metric: Literal["rank_icir", "rank_ic", "car_spread_t", "residual_icir"] = "rank_icir"
    seeds: list[int] = Field(default_factory=lambda: [0])


class Ensemble(_Model):
    horizon_weights: dict[int, float] = Field(default_factory=dict)
    seed_aggregation: Literal["mean_zscore"] = "mean_zscore"


class Output(_Model):
    score_field: str
    decay_lags: list[int] = Field(default_factory=lambda: [1, 5, 20])


class PortfolioLink(_Model):
    kind: Literal["index_enhancement", "event_calendar", "etf_rotation", "futures_hedged"]
    benchmark: str | None = None
    rebalance_every: int = 5
    industry_dev: float = 0.02
    name_cap: float = 0.01
    size_limit: float = 0.2
    beta_range: tuple[float, float] = (0.95, 1.05)
    turnover_cap: float = 0.15
    active_scale: float = 1.0
    neutralize_score: list[Literal["industry", "log_circ_mv"]] = Field(default_factory=list)
    hold_unchanged: bool = False
    construction: Literal["heuristic", "lp"] = "heuristic"
    aum: float = 5e8
    max_participation: float = 0.10
    top_k: int = 5
    holding_days: int = 20
    top_fraction: float = 0.1
    hedge: str | None = None
    margin: float = 0.15
    cash_buffer: float = 0.25
    hedge_ratios: list[float] = Field(default_factory=lambda: [1.0])
    basis_head: Literal["enabled", "disabled"] = "disabled"
    objective: Literal["score", "net_alpha_pwl"] = "score"
    alpha_return_scale: float = 0.005
    alpha_scale_mode: Literal["fixed", "trailing_10d"] = "fixed"
    impact_design_aum: float | None = None
    impact_segments: int = 4
    score_ema_half_life: float | None = None
    active_liquidity_adv_fraction: float | None = None
    liquidity_design_aum: float = 2e9


class Platform(_Model):
    cluster: str = "http://28.83.35.117:8081"
    processes: int = 32
    runlab_project: str = "alphasieve"


class TrainingTask(_Model):
    task_id: str = Field(pattern=r"^[a-z0-9_-]{3,64}$")
    mandate: Literal["A", "B", "C", "D"]
    description: str = ""
    universe_train: str
    universe_predict: str
    label: Label
    sample: Sample
    features: Features
    split: Split
    models: list[ModelSpec] = Field(min_length=1)
    search: Search = Field(default_factory=Search)
    ensemble: Ensemble = Field(default_factory=Ensemble)
    output: Output
    portfolio: PortfolioLink
    platform: Platform = Field(default_factory=Platform)
    score_source: ScoreSource | None = None

    @model_validator(mode="after")
    def _rules(self) -> "TrainingTask":
        errors = []
        text = canonical_json(self.model_dump(mode="json"))
        if FORBIDDEN.search(text):
            errors.append("tasks may not reference holdout or fresh data")
        if self.label.kind not in MANDATE_LABELS[self.mandate]:
            errors.append(f"mandate {self.mandate} needs label kind in {MANDATE_LABELS[self.mandate]}")
        if any(h <= 0 for h in self.label.horizons):
            errors.append("horizons must be positive")
        need = max(self.label.horizons) + 1
        if self.sample.purge_days < need or self.sample.embargo_days < need:
            errors.append(f"purge_days and embargo_days must be >= max(horizons) + 1 = {need}")
        if self.label.kind == "event_car" and not self.label.event_types:
            errors.append("event tasks must list event_types")
        if self.label.kind == "event_car" and "dedupe_same_period" not in self.label.mask:
            errors.append("event tasks must declare event de-duplication (dedupe_same_period)")
        if len(self.search.seeds) > MAX_SEEDS or len(set(self.search.seeds)) != len(self.search.seeds):
            errors.append(f"at most {MAX_SEEDS} distinct seeds")
        n = self.candidate_count()
        if n > self.search.max_configs:
            errors.append(f"candidate grid has {n} configs > max_configs {self.search.max_configs}")
        weights = self.ensemble.horizon_weights
        bad_cover = set(weights) != set(self.label.horizons)
        if weights and (bad_cover or abs(sum(weights.values()) - 1) > 1e-9):
            errors.append("horizon_weights must cover every horizon and sum to 1")
        if self.mandate == "D" and self.portfolio.basis_head == "enabled":
            errors.append("basis_head is not implemented (docs/20 §5.3)")
        if self.features.event_source is not None:
            if self.mandate not in ("A", "D"):
                errors.append("event-score features are for the cross-sectional mandates A and D")
            if self.features.event_lag_days < 1:
                errors.append("event_lag_days must be >= 1 (docs/20 §2.1)")
            if not self.features.event_half_lives or any(h <= 0 for h in self.features.event_half_lives):
                errors.append("event_half_lives must be positive")
        if self.features.etf_mapping is not None and self.mandate != "B":
            errors.append("etf_mapping is for the ETF-rotation mandate B")
        ratios = self.portfolio.hedge_ratios
        if not ratios or ratios[0] != 1.0 or any(not 0 < r <= 1 for r in ratios):
            errors.append("hedge_ratios must start with the headline ratio 1.0 and lie in (0, 1]")
        errors += self._portfolio_rules()
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def _portfolio_rules(self) -> list[str]:
        """docs/21 §5: the construction mechanisms exist only in the LP and the frozen-score mode only for A."""
        pf, errors = self.portfolio, []
        uses_new = (pf.objective != "score" or pf.score_ema_half_life is not None
                    or pf.active_liquidity_adv_fraction is not None)
        if uses_new and (pf.kind != "index_enhancement" or pf.construction != "lp"):
            errors.append("objective, score_ema_half_life and active_liquidity_adv_fraction need the LP"
                          " index-enhancement construction")
        if pf.alpha_return_scale <= 0 or pf.liquidity_design_aum <= 0:
            errors.append("alpha_return_scale and liquidity_design_aum must be positive")
        if not all(math.isfinite(v) for v in (pf.alpha_return_scale, pf.liquidity_design_aum, pf.aum)):
            errors.append("portfolio scales and AUM must be finite")
        if pf.impact_design_aum is not None and (not math.isfinite(pf.impact_design_aum)
                                                  or pf.impact_design_aum <= 0):
            errors.append("impact_design_aum must be finite and positive")
        if pf.alpha_scale_mode != "fixed" or pf.impact_design_aum is not None:
            if (self.mandate != "A" or self.score_source is None or pf.kind != "index_enhancement"
                    or pf.construction != "lp" or pf.objective != "net_alpha_pwl"
                    or (pf.alpha_scale_mode != "fixed" and (pf.rebalance_every != 10
                                                        or pf.alpha_return_scale != 0.005))):
                errors.append("cost-aware fields require frozen A net-alpha LP; trailing_10d needs 10-day rebalance"
                              " and 0.005 fallback")
        if pf.impact_segments != 4:
            errors.append("impact_segments is fixed at 4 (docs/21 §2.1)")
        if pf.score_ema_half_life is not None and pf.score_ema_half_life <= 0:
            errors.append("score_ema_half_life must be positive")
        frac = pf.active_liquidity_adv_fraction
        if frac is not None and not 0 < frac <= 1:
            errors.append("active_liquidity_adv_fraction must lie in (0, 1]")
        if self.score_source is not None and (self.mandate != "A" or pf.kind != "index_enhancement"):
            errors.append("score_source reuses A scores for an index-enhancement portfolio only")
        return errors

    def candidate_count(self) -> int:
        total = 0
        for m in self.models:
            n = 1
            for values in m.grid.values():
                n *= max(1, len(values))
            total += n
        return total

    def candidates(self) -> list[dict]:
        out = []
        for m in self.models:
            combos = [{}]
            for key, values in sorted(m.grid.items()):
                combos = [{**c, key: v} for c in combos for v in values]
            out += [{"family": m.family, "params": c} for c in combos]
        return out

    @property
    def config_hash(self) -> str:
        data = self.model_dump(mode="json")
        for section, name in LATER_FIELDS:
            part = data if section is None else data[section]
            model = type(self) if section is None else type(getattr(self, section))
            if part.get(name) == model.model_fields[name].get_default(call_default_factory=True):
                part.pop(name)
        return sha256_hex(canonical_json(data))[:16]


# fields added after the first training tasks were recorded; at their defaults they leave the hash unchanged
LATER_FIELDS = (("features", "derived_fields"), ("features", "event_source"), ("features", "event_lag_days"),
                ("features", "event_half_lives"), ("features", "etf_mapping"), ("portfolio", "hedge_ratios"),
                ("portfolio", "objective"), ("portfolio", "alpha_return_scale"), ("portfolio", "impact_segments"),
                ("portfolio", "alpha_scale_mode"), ("portfolio", "impact_design_aum"),
                ("portfolio", "score_ema_half_life"), ("portfolio", "active_liquidity_adv_fraction"),
                ("portfolio", "liquidity_design_aum"), (None, "score_source"))


def tasks_dir(settings: Settings) -> Path:
    return settings.config_dir / "training_tasks"


def parse_task(data: dict) -> TrainingTask:
    try:
        return TrainingTask.model_validate(data)
    except ValueError as exc:
        raise validation_error(f"invalid training task: {exc}") from exc


def load_task(settings: Settings, ref: str) -> TrainingTask:
    path = Path(ref) if ref.endswith((".yaml", ".yml")) else tasks_dir(settings) / f"{ref}.yaml"
    if not path.exists():
        raise validation_error(f"training task {ref} not found", path=str(path))
    return parse_task(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def list_tasks(settings: Settings) -> list[TrainingTask]:
    return [load_task(settings, str(p)) for p in sorted(tasks_dir(settings).glob("*.yaml"))]
