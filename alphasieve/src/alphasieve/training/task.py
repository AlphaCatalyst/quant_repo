"""TrainingTask: the declarative definition of a model-training task for one mandate (docs/19-training-tasks.md).

A task fixes the label, the sample, the features, the dev-only split, the candidate grid and the output contract
before any result is seen. Every full run of a task is one strategy-layer trial; changing any field is a new trial.
"""

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


class Features(_Model):
    factor_refs: list[str] | Literal["library"] = "library"
    panel_fields: list[str] = Field(default_factory=list)
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
    aum: float = 5e8
    max_participation: float = 0.10
    top_k: int = 5
    holding_days: int = 20
    top_fraction: float = 0.1
    hedge: str | None = None
    margin: float = 0.15
    cash_buffer: float = 0.25
    basis_head: Literal["enabled", "disabled"] = "disabled"


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
            errors.append("basis_head needs index-futures data, which is not available (docs/17)")
        if errors:
            raise ValueError("; ".join(errors))
        return self

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
        return sha256_hex(canonical_json(self.model_dump(mode="json")))[:16]


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
