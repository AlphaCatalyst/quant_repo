"""L3 search discount: Deflated Sharpe (Bailey & Lopez de Prado, 2014) on the daily IC series and BH-FDR.

The ICIR (mean / std of daily RankIC) plays the role of the Sharpe ratio. Trial count and the variance of
ICIR across trials come from the campaign's ledger, so every evaluation the search performed raises the bar.
Labels overlap across days, so the effective sample size is valid_dates / horizon.
"""

import math

import numpy as np
from scipy.stats import norm

EULER_GAMMA = 0.5772156649015329


def expected_max_sharpe(n_trials: int, variance: float) -> float:
    if n_trials < 2 or not variance or variance <= 0 or not math.isfinite(variance):
        return 0.0
    return math.sqrt(variance) * (
        (1 - EULER_GAMMA) * norm.ppf(1 - 1 / n_trials) + EULER_GAMMA * norm.ppf(1 - 1 / (n_trials * math.e))
    )


def deflated_sharpe(sr: float, sr0: float, t_obs: float, skew: float = 0.0, kurtosis: float = 3.0) -> float:
    if not all(math.isfinite(v) for v in (sr, sr0, t_obs)) or t_obs <= 1:
        return 0.0
    denom = 1 - skew * sr + (kurtosis - 1) / 4 * sr**2
    if denom <= 0:
        return 0.0
    return float(norm.cdf((sr - sr0) * math.sqrt(t_obs - 1) / math.sqrt(denom)))


def benjamini_hochberg(pvalues: dict[str, float], q: float) -> dict[str, dict]:
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    out = {}
    max_pass = 0
    for i, (_, p) in enumerate(items, start=1):
        if p <= q * i / m:
            max_pass = i
    for i, (key, p) in enumerate(items, start=1):
        out[key] = {"p": p, "rank": i, "threshold": q * i / m, "passed": i <= max_pass}
    return out


def trial_icirs(trials: list[dict]) -> list[float]:
    values = []
    for t in trials:
        v = t["metrics"].get("icir")
        if isinstance(v, (int, float)) and math.isfinite(v):
            values.append(float(v))
    return values


def search_discount(trials: list[dict]) -> dict:
    icirs = trial_icirs(trials)
    n = len(trials)
    variance = float(np.var(icirs, ddof=1)) if len(icirs) > 1 else 0.0
    return {"n_trials": n, "icir_variance": variance, "sr0": expected_max_sharpe(n, variance)}


def evaluate_candidate(icir: float, valid_dates: int, horizon: int, discount: dict, skew: float = 0.0,
                       kurtosis: float = 3.0) -> dict:
    t_eff = valid_dates / max(horizon, 1)
    dsr = deflated_sharpe(icir, discount["sr0"], t_eff, skew, kurtosis)
    return {"icir": icir, "t_eff": t_eff, "sr0": discount["sr0"], "dsr": dsr, "p_value": 1 - dsr}


def search_intensity_curve(trials: list[dict]) -> list[dict]:
    points, best, seen = [], None, []
    for i, t in enumerate(trials, start=1):
        v = t["metrics"].get("icir")
        if isinstance(v, (int, float)) and math.isfinite(v):
            seen.append(float(v))
            best = v if best is None else max(best, v)
        variance = float(np.var(seen, ddof=1)) if len(seen) > 1 else 0.0
        points.append({"n": i, "best_icir": best, "threshold": expected_max_sharpe(i, variance)})
    return points
