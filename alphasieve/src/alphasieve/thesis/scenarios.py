"""Deterministic scenarios, one-at-a-time sensitivity, and implied values."""

import math

from alphasieve.thesis.formula import FormulaError, evaluate, references
from alphasieve.thesis.model import Thesis


def _base(thesis: Thesis) -> dict[str, float]:
    return {name: parameter.base for name, parameter in thesis.parameters.items()}


def evaluate_scenarios(thesis: Thesis) -> dict:
    """Return base, named scenarios, and sensitivity sorted by largest impact."""
    base_values = _base(thesis)
    formula = thesis.valuation.formula
    base = evaluate(formula, base_values)
    scenarios = {
        name: evaluate(formula, {**base_values, **overrides})
        for name, overrides in thesis.scenarios.items()
    }
    sensitivity = []
    for name, parameter in thesis.parameters.items():
        low = evaluate(formula, {**base_values, name: parameter.low})
        high = evaluate(formula, {**base_values, name: parameter.high})
        sensitivity.append({"parameter": name, "low": low, "high": high,
                            "low_impact": low - base, "high_impact": high - base,
                            "absolute_impact": max(abs(low - base), abs(high - base))})
    sensitivity.sort(key=lambda row: (-row["absolute_impact"], row["parameter"]))
    return {"base": base, "scenarios": scenarios, "sensitivity": sensitivity,
            "output_unit": thesis.valuation.output_unit}


def implied(thesis: Thesis, param: str | None = None, target: float | None = None) -> dict:
    """Solve one parameter by bracketing and bisection; raise on no root."""
    param = param or thesis.valuation.price_param
    if param not in thesis.parameters:
        raise FormulaError(f"unknown implied parameter: {param}")
    if target is None:
        target = thesis.valuation.market_price
    if target is None or not math.isfinite(target):
        raise FormulaError("a finite target or valuation.market_price is required")
    if param not in references(thesis.valuation.formula, set(thesis.parameters)):
        raise FormulaError(f"parameter {param} is not used by the formula")
    values = _base(thesis)

    def difference(value: float) -> float:
        return evaluate(thesis.valuation.formula, {**values, param: value}) - target

    bounds = thesis.parameters[param]
    low, high = bounds.low, bounds.high
    if low == high:
        low, high = low - 1, high + 1
    f_low, f_high = difference(low), difference(high)
    for _ in range(20):
        if f_low == 0:
            return {"parameter": param, "value": low, "target": target, "unit": bounds.unit}
        if f_high == 0:
            return {"parameter": param, "value": high, "target": target, "unit": bounds.unit}
        if f_low * f_high < 0:
            break
        width = high - low
        low, high = low - width, high + width
        f_low, f_high = difference(low), difference(high)
    else:
        raise FormulaError(f"no root bracketed for {param} at target {target}")
    for _ in range(100):
        mid = (low + high) / 2
        f_mid = difference(mid)
        if abs(f_mid) <= 1e-10 or (high - low) <= 1e-12 * max(1, abs(mid)):
            return {"parameter": param, "value": mid, "target": target, "unit": bounds.unit}
        if f_low * f_mid < 0:
            high = mid
        else:
            low, f_low = mid, f_mid
    raise FormulaError(f"bisection did not converge for {param}")
