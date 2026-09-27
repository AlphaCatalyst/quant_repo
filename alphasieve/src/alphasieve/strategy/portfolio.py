"""Portfolio layer (S-6): index-enhancement weights from a score matrix.

Per rebalance date: the benchmark is the cap-weighted universe; industry weights stay within ``industry_dev``
of the benchmark and tilt towards industries with higher average score rank; inside an industry the highest
scores are held with equal weights capped at ``name_cap``; the move from the previous weights is scaled down so
one-way turnover stays within ``turnover_cap``. Long only, fully invested.
"""

import math

import numpy as np
import pandas as pd

from alphasieve.data.access import Panel


def _industry_targets(bench_g: np.ndarray, tilt_score: np.ndarray, dev: float) -> np.ndarray:
    lo, hi = np.maximum(bench_g - dev, 0.0), bench_g + dev
    target = np.clip(bench_g + dev * np.clip(2 * tilt_score, -1, 1), lo, hi)
    for _ in range(50):
        gap = 1.0 - target.sum()
        if abs(gap) < 1e-12:
            break
        room = (hi - target) if gap > 0 else (target - lo)
        if room.sum() <= 0:
            break
        target = np.clip(target + gap * room / room.sum(), lo, hi)
    return target / target.sum()


def _fill_industry(weight: float, scores: np.ndarray, cap: float) -> np.ndarray:
    order = np.argsort(-scores, kind="stable")
    n = min(len(order), max(1, math.ceil(weight / cap - 1e-9)))
    w = np.zeros(len(scores))
    w[order[:n]] = min(cap, weight / n)
    return w


def target_weights(score: np.ndarray, cap_mv: np.ndarray, groups: np.ndarray, eligible: np.ndarray,
                   industry_dev: float, name_cap: float) -> np.ndarray:
    w = np.zeros(len(score))
    ok = eligible & np.isfinite(score) & np.isfinite(cap_mv) & (cap_mv > 0)
    if ok.sum() == 0:
        return w
    bench = np.where(ok, cap_mv, 0.0) / cap_mv[ok].sum()
    labels = np.unique(groups[ok])
    rank = pd.Series(score[ok]).rank(pct=True).to_numpy() - 0.5
    rank_full = np.zeros(len(score))
    rank_full[ok] = rank
    bench_g = np.array([bench[ok & (groups == g)].sum() for g in labels])
    tilt = np.array([rank_full[ok & (groups == g)].mean() for g in labels])
    targets = _industry_targets(bench_g, tilt, industry_dev)
    leftover = 0.0
    for g, tw in zip(labels, targets, strict=True):
        members = np.flatnonzero(ok & (groups == g))
        filled = _fill_industry(tw, score[members], name_cap)
        w[members] = filled
        leftover += tw - filled.sum()
    if leftover > 1e-9:
        room = np.where(ok, name_cap - w, 0.0).clip(min=0)
        room[w == 0] = 0.0
        if room.sum() > 0:
            w += room * min(1.0, leftover / room.sum())
    return w / w.sum() if w.sum() > 0 else w


def _size_z(cap_mv: np.ndarray, ok: np.ndarray) -> np.ndarray:
    size = np.log(np.where(ok & (cap_mv > 0), cap_mv, np.nan))
    z = (size - np.nanmean(size)) / (np.nanstd(size) or 1.0)
    return np.nan_to_num(z)


def enforce_size(w: np.ndarray, bench: np.ndarray, z: np.ndarray, limit: float) -> np.ndarray:
    """Blend towards the benchmark just enough to bring active size exposure within +-limit."""
    exposure = float(((w - bench) * z).sum())
    if abs(exposure) <= limit:
        return w
    alpha = 1 - limit / abs(exposure)
    return (1 - alpha) * w + alpha * bench


def enforce_industries(w: np.ndarray, bench: np.ndarray, groups: np.ndarray, dev: float) -> np.ndarray:
    """Scale each industry's total into [bench - dev, bench + dev] and renormalise."""
    labels = np.unique(groups)
    for _ in range(20):
        totals = {g: w[groups == g].sum() for g in labels}
        worst = 0.0
        for g in labels:
            b = bench[groups == g].sum()
            lo, hi = max(b - dev, 0.0), b + dev
            t = totals[g]
            target = min(max(t, lo), hi)
            worst = max(worst, abs(t - target))
            if t > 0 and target != t:
                w[groups == g] *= target / t
            elif t == 0 and target > 0:
                members = (groups == g) & (bench > 0)
                if members.any():
                    w[members] = bench[members] / bench[members].sum() * target
        w = w / w.sum()
        if worst < 1e-9:
            break
    return w


def build_weights(scores: pd.DataFrame, panel: Panel, rebalance_every: int = 5, industry_dev: float = 0.03,
                  name_cap: float = 0.02, turnover_cap: float = 0.30, size_limit: float = 0.3) -> pd.DataFrame:
    dates, codes = panel.dates, panel.codes
    s = scores.reindex(index=dates, columns=codes).to_numpy(dtype=float)
    cap = panel.wide("circ_mv").to_numpy(dtype=float)
    universe = panel.mask("in_universe").to_numpy()
    groups = panel.industry().reindex(codes).fillna("unknown").to_numpy()
    active = np.flatnonzero(np.isfinite(s).any(axis=1))
    rebal = active[::rebalance_every]
    prev = np.zeros(len(codes))
    rows = {}
    for t in rebal:
        target = target_weights(s[t], cap[t], groups, universe[t], industry_dev, name_cap)
        if target.sum() == 0:
            continue
        ok = universe[t] & np.isfinite(cap[t]) & (cap[t] > 0)
        bench = np.where(ok, cap[t], 0.0) / cap[t][ok].sum()
        z = _size_z(cap[t], ok)
        target = enforce_size(target, bench, z, size_limit)
        oneway = 0.5 * np.abs(target - prev).sum()
        lam = 1.0 if prev.sum() == 0 or oneway <= turnover_cap else turnover_cap / oneway
        w = np.clip(prev + lam * (target - prev), 0, None)
        w = enforce_industries(w / w.sum(), bench, groups, industry_dev)
        w = enforce_size(w, bench, z, size_limit)
        rows[dates[t]] = w
        prev = w
    return pd.DataFrame.from_dict(rows, orient="index", columns=codes)


def weight_diagnostics(weights: pd.DataFrame, panel: Panel, turnover_cap: float = 0.30) -> dict:
    if weights.empty:
        return {"rebalances": 0}
    groups = panel.industry().reindex(weights.columns).fillna("unknown")
    cap = panel.wide("circ_mv").reindex(index=weights.index, columns=weights.columns)
    uni = panel.mask("in_universe").reindex(index=weights.index, columns=weights.columns).fillna(False)
    bench = cap.where(uni).div(cap.where(uni).sum(axis=1), axis=0).fillna(0.0)
    ind_dev = (weights.T.groupby(groups).sum() - bench.T.groupby(groups).sum()).abs().max().max()
    turnover = 0.5 * weights.diff().abs().sum(axis=1).iloc[1:]
    size = np.log(cap.where(cap > 0))
    size_z = size.sub(size.where(uni).mean(axis=1), axis=0).div(size.where(uni).std(axis=1), axis=0)
    active_size = ((weights - bench) * size_z.fillna(0)).sum(axis=1)
    raw_turnover = turnover
    return {"rebalances": int(len(weights)), "names_held_mean": float((weights > 0).sum(axis=1).mean()),
            "max_name_weight": float(weights.max().max()), "max_industry_deviation": float(ind_dev),
            "one_way_turnover_mean": float(turnover.mean()) if len(turnover) else 0.0,
            "one_way_turnover_max": float(turnover.max()) if len(turnover) else 0.0,
            "active_size_exposure_mean": float(active_size.mean()),
            "active_size_exposure_max_abs": float(active_size.abs().max()),
            "turnover_over_cap_share": float((raw_turnover > turnover_cap + 1e-6).mean()) if len(raw_turnover) else 0.0}
