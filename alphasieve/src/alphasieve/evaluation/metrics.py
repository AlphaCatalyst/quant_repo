import numpy as np
import pandas as pd

from alphasieve.factors.ops import cs_neutralize

MIN_NAMES = 20


def rank_corr_series(a: pd.DataFrame, b: pd.DataFrame, valid: pd.DataFrame, min_names: int = MIN_NAMES) -> pd.Series:
    both = valid & a.notna() & b.notna()
    ar = a.where(both).rank(axis=1)
    br = b.where(both).rank(axis=1)
    ac = ar.sub(ar.mean(axis=1), axis=0)
    bc = br.sub(br.mean(axis=1), axis=0)
    denom = np.sqrt((ac**2).sum(axis=1) * (bc**2).sum(axis=1))
    corr = (ac * bc).sum(axis=1) / denom.replace(0, np.nan)
    corr[both.sum(axis=1) < min_names] = np.nan
    return corr


def summarize_ic(ic: pd.Series) -> dict:
    ic = ic.dropna()
    std = float(ic.std()) if len(ic) > 1 else float("nan")
    mean = float(ic.mean()) if len(ic) else float("nan")
    return {
        "ic_mean": mean,
        "ic_std": std,
        "icir": mean / std if std and std > 0 else float("nan"),
        "ic_positive_ratio": float((ic > 0).mean()) if len(ic) else float("nan"),
        "valid_dates": int(len(ic)),
    }


def coverage(factor: pd.DataFrame, universe: pd.DataFrame) -> float:
    counts = universe.sum(axis=1)
    covered = (factor.notna() & universe).sum(axis=1)
    ratio = covered[counts > 0] / counts[counts > 0]
    return float(ratio.mean()) if len(ratio) else 0.0


def quantile_returns(factor: pd.DataFrame, label: pd.DataFrame, valid: pd.DataFrame, q: int = 5) -> dict:
    both = valid & factor.notna() & label.notna()
    pct = factor.where(both).rank(axis=1, pct=True)
    bucket = np.ceil(pct * q).clip(1, q)
    out = {}
    for b in range(1, q + 1):
        out[f"q{b}"] = float(label.where(bucket == b).mean(axis=1).mean())
    out["long_short"] = out[f"q{q}"] - out["q1"]
    return out


def turnover_proxy(factor: pd.DataFrame, valid: pd.DataFrame) -> float:
    auto = rank_corr_series(factor, factor.shift(1), valid)
    return float(1 - auto.mean()) if auto.notna().any() else float("nan")


def library_correlations(factor: pd.DataFrame, library: dict[str, pd.DataFrame], valid: pd.DataFrame) -> dict:
    corrs = {}
    for name, other in library.items():
        series = rank_corr_series(factor, other, valid)
        corrs[name] = float(series.mean()) if series.notna().any() else 0.0
    if not corrs:
        return {"max_abs_corr": 0.0, "max_corr_with": None, "correlations": {}}
    top = max(corrs, key=lambda k: abs(corrs[k]))
    return {"max_abs_corr": abs(corrs[top]), "max_corr_with": top, "correlations": corrs}


def neutralized_ic(factor: pd.DataFrame, label: pd.DataFrame, valid: pd.DataFrame, groups: pd.Series,
                   size: pd.DataFrame) -> dict:
    residual = cs_neutralize(factor.where(valid), groups, size)
    return summarize_ic(rank_corr_series(residual, label, valid))


def subwindow_ics(ic: pd.Series, n: int) -> list[float]:
    ic = ic.dropna()
    if len(ic) < n:
        return []
    return [float(chunk.mean()) for chunk in np.array_split(ic, n)]
