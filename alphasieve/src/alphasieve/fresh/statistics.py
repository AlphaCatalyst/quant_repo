"""Pre-registered forward evidence on the original trading-day clock."""

import math
from collections.abc import Mapping, Sequence

from scipy.stats import norm


def hac(series: Sequence[tuple[int, float | None]], horizon: int = 1) -> dict:
    """Newey-West mean test; ordinals remain spaced across missing observations."""
    if horizon < 1:
        raise ValueError("horizon must be positive")
    observed: dict[int, float] = {}
    for ordinal, value in series:
        if value is not None:
            if not math.isfinite(value):
                raise ValueError("non-finite observation")
            observed[ordinal] = float(value)
    # A missing day must also be unique, even though it is absent from covariance pairs.
    if len({day for day, _ in series}) != len(series):
        raise ValueError("duplicate trading-day ordinal")
    n = len(observed)
    lag = max(horizon - 1, math.ceil(4 * (n / 100) ** (2 / 9))) if n else horizon - 1
    out = {"n": n, "lag": lag, "mean": None, "se": None, "ci_low": None,
           "ci_high": None, "p_one_sided": None}
    if n < 2:
        return out
    mean = sum(observed.values()) / n
    residuals = {day: value - mean for day, value in observed.items()}
    long_run = sum(value * value for value in residuals.values()) / n
    for k in range(1, lag + 1):
        covariance = sum(value * residuals.get(day - k, 0.0) for day, value in residuals.items()) / n
        long_run += 2 * (1 - k / (lag + 1)) * covariance
    se = math.sqrt(max(0.0, long_run) / n)
    if se == 0:
        p = 0.0 if mean > 0 else 1.0 if mean < 0 else 0.5
    else:
        p = float(norm.sf(mean / se))
    half_width = float(norm.ppf(0.975)) * se
    out.update(mean=mean, se=se, ci_low=mean - half_width, ci_high=mean + half_width,
               p_one_sided=p)
    return out


def benjamini_hochberg(pvalues: Mapping[str, float | None], q: float = 0.10) -> dict[str, dict]:
    """Test every locked member; absent or invalid p-values remain in the family at p=1."""
    if not 0 < q < 1:
        raise ValueError("q must be between zero and one")
    items = []
    for key, value in pvalues.items():
        p = 1.0 if value is None or not math.isfinite(value) else float(value)
        if not 0 <= p <= 1:
            raise ValueError("p-value outside [0, 1]")
        items.append((key, p))
    items.sort(key=lambda item: (item[1], item[0]))
    m = len(items)
    max_pass = max((i for i, (_, p) in enumerate(items, 1) if p <= q * i / m), default=0)
    return {key: {"p": p, "rank": i, "threshold": q * i / m, "passed": i <= max_pass}
            for i, (key, p) in enumerate(items, 1)}


def verdict(*, kind: str, mode: str, series: Sequence[tuple[int, float | None]], horizon: int,
            eligible_days: int, required_days: int | None = None, coverage_min: float = 0.95,
            bh_pass: bool = False, marginal_ic: float | None = None,
            excess_drawdown: float | None = None, tracking_error: float | None = None,
            hard_violation: bool = False, endpoint_reached: bool = False,
            p_threshold: float = 0.05, drawdown_floor: float = -0.08,
            te_min: float = 0.04, te_max: float = 0.06) -> dict:
    """Judge a locked primary series once its pre-registered endpoint is reached.

    Factor values must already have their locked direction applied. Strategy values
    are daily net excess returns; tracking_error is annualized.
    """
    if kind not in {"factor", "strategy"} or mode not in {"validation", "diagnostic_shadow"}:
        raise ValueError("invalid forward kind or mode")
    if eligible_days < 0 or not 0 < coverage_min <= 1:
        raise ValueError("invalid coverage denominator or threshold")
    required = required_days if required_days is not None else (60 if kind == "factor" else 120)
    if required < 1:
        raise ValueError("required_days must be positive")
    stats = hac(series, horizon)
    if stats["n"] > eligible_days:
        raise ValueError("valid observations exceed eligible days")
    coverage = stats["n"] / eligible_days if eligible_days else 0.0
    result = {"verdict": None, "promotion_eligible": False, "stats": stats,
              "valid_days": stats["n"], "eligible_days": eligible_days,
              "required_days": required, "coverage": coverage, "reasons": []}
    if not endpoint_reached:
        result["status"] = "observing"
        return result
    if mode == "diagnostic_shadow":
        result["status"] = "shadow_complete"
        return result
    reasons = result["reasons"]
    if stats["n"] < required:
        reasons.append("insufficient_days")
    if coverage < coverage_min:
        reasons.append("low_coverage")
    if stats["p_one_sided"] is None:
        reasons.append("statistics_unavailable")
    if hard_violation:
        reasons.append("hard_violation")
    if kind == "strategy":
        if excess_drawdown is not None and excess_drawdown < drawdown_floor:
            reasons.append("drawdown_limit")
        if tracking_error is not None and not te_min <= tracking_error <= te_max:
            reasons.append("tracking_error_limit")
        if excess_drawdown is None or tracking_error is None:
            reasons.append("risk_unavailable")
    if hard_violation or "drawdown_limit" in reasons or "tracking_error_limit" in reasons:
        decision = "fresh_failed"
    elif stats["ci_high"] is not None and stats["ci_high"] < 0:
        decision = "fresh_failed"
        reasons.append("significant_negative")
    elif reasons:
        decision = "fresh_inconclusive"
    elif stats["mean"] > 0 and stats["p_one_sided"] <= p_threshold and bh_pass and (
            kind == "strategy" or marginal_ic is not None and marginal_ic > 0):
        decision = "fresh_supported"
    else:
        decision = "fresh_inconclusive"
        reasons.append("support_not_established")
    result["verdict"] = result["status"] = decision
    result["promotion_eligible"] = decision == "fresh_supported"
    return result
