import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EvidenceTier = Literal["dev", "holdout", "fresh"]
Role = Literal["agent", "human", "system"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Cell(_Model):
    domain: str
    form: str
    scale: Literal["short", "medium", "long", "quarterly"]


class ResearchQuestion(_Model):
    id: str
    title: str
    market: str = "cn_a_share"
    universe: str = "csi800"
    frequency: Literal["daily"] = "daily"
    horizon: int = 5
    hypothesis_family: str | None = None
    expected_direction: Literal[1, -1] | None = None
    constraints: dict = Field(default_factory=dict)
    created_by: str
    created_at: str
    status: str = "open"


class DataContract(_Model):
    id: str
    market: str = "cn_a_share"
    universe: str
    provider: str
    tables: list[str]
    fields: list[str]
    adjustment: str
    pit_policy: str
    lag_policy: str
    missing_policy: str
    tradability_policy: str
    provenance: dict = Field(default_factory=dict)
    blocked_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FactorSpec(_Model):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    expression: str
    hypothesis: str
    cell: Cell
    direction: Literal[1, -1] = 1
    horizon: Literal[1, 5, 10, 20] = 5
    universe: str = "csi800"
    params_source: Literal["default", "neighborhood"] = "default"
    neighborhood_of: str | None = None
    created_from: str | None = None


class TrialLedgerEntry(_Model):
    trial_id: str
    record_kind: Literal["started", "completed", "failed", "void"]
    campaign_id: str | None = None
    factor_id: str | None = None
    version: int | None = None
    candidate_hash: str | None = None
    evidence_tier: EvidenceTier = "dev"
    data_window: str | None = None
    gate_policy_version: int | None = None
    search_space_version: str | None = None
    metrics: dict = Field(default_factory=dict)
    gate_results: dict = Field(default_factory=dict)
    outcome: str | None = None
    created_by: Role
    artifact_id: str | None = None


class Campaign(_Model):
    campaign_id: str
    question_id: str
    universe: str = "csi800"
    horizon: int = 5
    search_space_id: str
    budgets: dict = Field(default_factory=dict)
    stop_conditions: dict = Field(default_factory=dict)
    status: str = "draft"


MODELS = (ResearchQuestion, DataContract, FactorSpec, TrialLedgerEntry, Campaign, Cell)


def export_schemas(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for model in MODELS:
        path = out_dir / f"{model.__name__}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False), encoding="utf-8")
        paths.append(path)
    return paths
