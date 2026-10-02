"""Frozen forward identity and human approval request."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from alphasieve.util import canonical_json, sha256_hex


class ForwardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_kind: Literal["strategy", "factor"]
    mode: Literal["diagnostic_shadow", "validation"]
    trial_id: str | None = None
    source_hash: str
    bundle: dict
    feature_names: list[str] = Field(min_length=1)
    chosen: dict[str, dict]
    seeds: list[int] = Field(min_length=1)
    horizons: list[int] = Field(min_length=1)
    primary_horizon: int
    train_window: Literal["rolling", "expanding"]
    train_years: float
    purge_days: int
    retrain: Literal["monthly", "quarterly", "weekly"]
    benchmark: str
    capital: float = Field(gt=0)
    start_date: str
    operational_refit: bool = True

    @property
    def digest(self) -> str:
        return sha256_hex(canonical_json(self.model_dump(mode="json")))
