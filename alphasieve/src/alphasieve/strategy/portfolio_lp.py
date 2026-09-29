"""Linear-programming index enhancement (docs/18 G-3): maximise the score-weighted holding subject to linear risk
limits, instead of the heuristic fill-then-blend of ``portfolio.build_weights``.

Per rebalance date, with benchmark weights b (cap-weighted members) and previous weights p:

    max  z'w
    s.t. sum(w) = 1;  max(0, b - cap) <= w <= b + cap for members;  0 <= w <= p for non-members (sell only)
         b_g - dev <= sum_g(w) <= b_g + dev per industry g;  |(w - b)'s| <= size_limit (s = size z-score)
         lo <= w'beta <= hi;  sum|w - p| <= 2 * turnover_cap   (via w - p = u - v, u, v >= 0)

z is the cross-sectional z-score of the model score. When the turnover limit makes the problem infeasible (for
example after large index changes) the date is re-solved without it and counted in the diagnostics.
"""

import numpy as np
import pandas as pd
from scipy.optimize import linprog

from alphasieve.data.access import Panel
from alphasieve.strategy.portfolio import _size_z


def _solve(z, b, p, member, groups, size_z, beta, cap, dev, size_limit, beta_range, turnover_cap):
    n = len(z)
    lo = np.where(member, np.maximum(b - cap, 0.0), 0.0)
    hi = np.where(member, b + cap, p)
    c = np.concatenate([-z, np.zeros(2 * n)])
    budget = np.concatenate([np.ones(n), np.zeros(2 * n)])
    link = np.hstack([np.eye(n), -np.eye(n), np.eye(n)])          # w - u + v = p
    a_ub, b_ub = [], []
    for g in np.unique(groups[member]):
        row = np.concatenate([(groups == g).astype(float), np.zeros(2 * n)])
        bg = float(b[groups == g].sum())
        a_ub += [row, -row]
        b_ub += [bg + dev, -(max(bg - dev, 0.0))]
    srow = np.concatenate([size_z, np.zeros(2 * n)])
    bs = float(b @ size_z)
    a_ub += [srow, -srow]
    b_ub += [bs + size_limit, -(bs - size_limit)]
    if beta is not None and beta_range is not None:
        brow = np.concatenate([beta, np.zeros(2 * n)])
        a_ub += [brow, -brow]
        b_ub += [beta_range[1], -beta_range[0]]
    bounds = [(lo[i], hi[i]) for i in range(n)] + [(0, None)] * (2 * n)
    a_eq_m = np.vstack([budget, link])
    b_eq_v = np.concatenate([[1.0], p])
    for with_turnover in (True, False):
        ub, bb = list(a_ub), list(b_ub)
        if with_turnover and p.sum() > 0:
            ub.append(np.concatenate([np.zeros(n), np.ones(2 * n)]))
            bb.append(2 * turnover_cap)
        res = linprog(c, A_ub=np.vstack(ub), b_ub=np.array(bb), A_eq=a_eq_m, b_eq=b_eq_v, bounds=bounds,
                      method="highs")
        if res.status == 0:
            return np.clip(res.x[:n], 0, None), with_turnover or p.sum() == 0
    return None, False


def build_weights_lp(scores: pd.DataFrame, panel: Panel, universe_mask: np.ndarray, rebalance_every: int = 5,
                     industry_dev: float = 0.02, name_cap: float = 0.01, turnover_cap: float = 0.15,
                     size_limit: float = 0.2, beta: np.ndarray | None = None,
                     beta_range: tuple[float, float] | None = None) -> tuple[pd.DataFrame, dict]:
    dates, codes = panel.dates, panel.codes
    s = scores.reindex(index=dates, columns=codes).to_numpy(dtype=float)
    cap = panel.wide("circ_mv").to_numpy(dtype=float)
    groups = panel.industry().reindex(codes).fillna("unknown").to_numpy()
    active = np.flatnonzero(np.isfinite(np.where(universe_mask, s, np.nan)).any(axis=1))
    prev = np.zeros(len(codes))
    rows, relaxed, failed = {}, 0, 0
    for t in active[::rebalance_every]:
        member = universe_mask[t] & np.isfinite(cap[t]) & (cap[t] > 0)
        if member.sum() < 20:
            continue
        b = np.where(member, cap[t], 0.0) / cap[t][member].sum()
        raw = np.where(member & np.isfinite(s[t]), s[t], np.nan)
        z = np.nan_to_num((raw - np.nanmean(raw)) / (np.nanstd(raw) or 1.0), nan=0.0)
        keep = member | (prev > 0)
        idx = np.flatnonzero(keep)
        bt = beta[t][idx] if beta is not None else None
        size_z = _size_z(cap[t], member)[idx]
        bt = np.nan_to_num(bt, nan=1.0) if bt is not None else None
        w_sub, ok_turnover = _solve(z[idx], b[idx], prev[idx], member[idx], groups[idx], size_z, bt, name_cap,
                                    industry_dev, size_limit, beta_range, turnover_cap)
        if w_sub is None:
            failed += 1
            continue
        relaxed += 0 if ok_turnover else 1
        w = np.zeros(len(codes))
        w[idx] = w_sub
        w[w < 1e-6] = 0.0
        w /= w.sum()
        rows[dates[t]] = w
        prev = w
    frame = pd.DataFrame.from_dict(rows, orient="index", columns=codes)
    return frame, {"construction": "lp", "turnover_relaxed_dates": relaxed, "infeasible_dates": failed}
