"""YAML thesis schema with evidence and formula validation."""

import math
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from alphasieve.thesis.formula import FormulaError, references


class Asset(BaseModel):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)


class Evidence(BaseModel):
    id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    value: float | str | None = None
    source: str = Field(min_length=1)
    grade: Literal["A", "B", "C", "D"]
    accessed: date

    @field_validator("source")
    @classmethod
    def source_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence source must not be blank")
        return value

    @model_validator(mode="after")
    def missing_is_not_numeric(self):
        if self.grade == "D" and isinstance(self.value, (float, int)):
            raise ValueError("grade D evidence cannot carry a numeric value")
        return self


class Parameter(BaseModel):
    base: float
    unit: str = Field(min_length=1)
    low: float
    high: float
    evidence: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def bounds(self):
        if not all(math.isfinite(v) for v in (self.base, self.low, self.high)):
            raise ValueError("parameter values must be finite")
        if self.low > self.high:
            raise ValueError("parameter low exceeds high")
        return self


class Valuation(BaseModel):
    formula: str = Field(min_length=1)
    output_unit: str = Field(min_length=1)
    price_param: str | None = None
    market_price: float | None = None
    shares: float | None = None


class Falsifier(BaseModel):
    id: str = Field(min_length=1)
    condition: str = Field(min_length=1)
    variable: str = Field(min_length=1)
    action: str = Field(min_length=1)


class Revision(BaseModel):
    date: date
    note: str = Field(min_length=1)


class Thesis(BaseModel):
    thesis_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")
    title: str = Field(min_length=1)
    assets: list[Asset] = Field(min_length=1)
    status: Literal["researching", "holding", "falsified", "realized", "abandoned"]
    as_of: date
    core_variables: list[str]
    argument: list[str]
    parameters: dict[str, Parameter]
    evidence: list[Evidence]
    valuation: Valuation
    scenarios: dict[str, dict[str, float]]
    falsifiers: list[Falsifier]
    position_limit: float = Field(ge=0, le=1)
    proposed_forecasts: list[dict] = Field(default_factory=list)
    revisions: list[Revision] = Field(default_factory=list)

    @model_validator(mode="after")
    def cross_references(self):
        ids = [item.id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate evidence id")
        grades = {item.id: item.grade for item in self.evidence}
        for name, parameter in self.parameters.items():
            unknown = set(parameter.evidence) - grades.keys()
            if unknown:
                raise ValueError(f"parameter {name} has unknown evidence ids: {sorted(unknown)}")
            if all(grades[item] == "D" for item in parameter.evidence):
                raise ValueError(f"parameter {name} has only grade D evidence for its base")
        for name, overrides in self.scenarios.items():
            unknown = set(overrides) - self.parameters.keys()
            if unknown:
                raise ValueError(f"scenario {name} overrides unknown parameters: {sorted(unknown)}")
            if not all(math.isfinite(value) for value in overrides.values()):
                raise ValueError(f"scenario {name} has non-finite overrides")
        try:
            references(self.valuation.formula, set(self.parameters))
        except FormulaError as exc:
            raise ValueError(str(exc)) from None
        if self.valuation.price_param and self.valuation.price_param not in self.parameters:
            raise ValueError("valuation price_param must name a parameter")
        return self


def load_thesis(path: str | Path) -> Thesis:
    """Load and validate a thesis from a YAML file."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Thesis.model_validate(data)
