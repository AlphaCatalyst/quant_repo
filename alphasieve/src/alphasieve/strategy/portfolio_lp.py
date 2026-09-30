"""Linear-programming index enhancement (docs/18 G-3): maximise the score-weighted holding subject to linear risk
limits, instead of the heuristic fill-then-blend of ``portfolio.build_weights``.

Per rebalance date, with benchmark weights b (cap-weighted members) and previous weights p:

    max  z'w
    s.t. sum(w) = 1;  max(0, b - cap) <= w <= b + cap for members;  0 <= w <= p for non-members (sell only)
         b_g - dev <= sum_g(w) <= b_g + dev per industry g;  |(w - b)'s| <= size_limit (s = size z-score)
         lo <= w'beta <= hi;  sum|w - p| <= 2 * turnover_cap   (via w - p = u - v, u, v >= 0)

z is the cross-sectional z-score of the model score. When the turnover limit makes the problem infeasible (for
example after large index changes) the date is re-solved without it and counted in the diagnostics.

Two options from docs/21 §2: ``cap`` may be a per-name active limit (liquidity-scaled), and the objective may be
net of trading costs, ``alpha_scale * z'w - c_buy * sum(u) - c_sell * sum(v) - sum(e)``, where e_i bounds the
square-root impact ``kappa_i * q_i ** 1.5`` of the traded weight q_i = u_i + v_i from above by its chords on
0, 1/4, ..., 1 of the largest feasible trade (kappa_i = impact_k * vol_i * sqrt(aum / adv_i), as in execution).
"""

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import linprog

from alphasieve.data.access import Panel
from alphasieve.strategy.portfolio import _size_z

SEGMENTS = 4


def _impact_rows(kappa: np.ndarray, qmax: np.ndarray, n: int) -> tuple[sparse.csr_matrix, np.ndarray]:
    """Rows ``slope * (u_i + v_i) - e_i <= -intercept`` of the chord envelope, over variables [w, u, v, e]."""
    rows, cols, vals, rhs = [], [], [], []
    r = 0
    for i in np.flatnonzero((kappa > 0) & (qmax > 0)):
        nodes = np.linspace(0.0, qmax[i], SEGMENTS + 1)
        cost = kappa[i] * nodes ** 1.5
        for j in range(SEGMENTS):
            slope = (cost[j + 1] - cost[j]) / (nodes[j + 1] - nodes[j])
            rows += [r, r, r]
            cols += [n + i, 2 * n + i, 3 * n + i]
            vals += [slope, slope, -1.0]
            rhs.append(slope * nodes[j] - cost[j])
            r += 1
    return sparse.csr_matrix((vals, (rows, cols)), shape=(r, 4 * n)), np.array(rhs)


def _solve(z, b, p, member, groups, size_z, beta, cap, dev, size_limit, beta_range, turnover_cap, cost=None):
    """``cost``: None for the score objective, else dict(alpha_scale, c_buy, c_sell, kappa, frozen) per name."""
    n = len(z)
    lo = np.where(member, np.maximum(b - cap, 0.0), 0.0)
    hi = np.where(member, b + cap, p)
    k = 3 if cost is None else 4
    if cost is None:
        c = np.concatenate([-z, np.zeros(2 * n)])
    else:
        lo, hi = np.where(cost["frozen"], p, lo), np.where(cost["frozen"], p, hi)
        c = np.concatenate([-cost["alpha_scale"] * z, np.full(n, cost["c_buy"]), np.full(n, cost["c_sell"]),
                            np.ones(n)])

    def pad(row):
        return np.concatenate([row, np.zeros((k - 1) * n)])

    budget = pad(np.ones(n))
    link = np.hstack([np.eye(n), -np.eye(n), np.eye(n)] + ([np.zeros((n, n))] if k == 4 else []))   # w - u + v = p
    a_ub, b_ub = [], []
    for g in np.unique(groups[member]):
        row = pad((groups == g).astype(float))
        bg = float(b[groups == g].sum())
        a_ub += [row, -row]
        b_ub += [bg + dev, -(max(bg - dev, 0.0))]
    srow = pad(size_z)
    bs = float(b @ size_z)
    a_ub += [srow, -srow]
    b_ub += [bs + size_limit, -(bs - size_limit)]
    if beta is not None and beta_range is not None:
        brow = pad(beta)
        a_ub += [brow, -brow]
        b_ub += [beta_range[1], -beta_range[0]]
    extra_ub, extra_b = sparse.csr_matrix((0, k * n)), np.zeros(0)
    if cost is not None:
        qmax = np.maximum(np.abs(lo - p), np.abs(hi - p))
        extra_ub, extra_b = _impact_rows(cost["kappa"], qmax, n)
    bounds = [(lo[i], hi[i]) for i in range(n)] + [(0, None)] * ((k - 1) * n)
    a_eq_m = np.vstack([budget, link])
    b_eq_v = np.concatenate([[1.0], p])
    for with_turnover in (True, False):
        ub, bb = list(a_ub), list(b_ub)
        if with_turnover and p.sum() > 0:
            ub.append(np.concatenate([np.zeros(n), np.ones(2 * n), np.zeros((k - 3) * n)]))
            bb.append(2 * turnover_cap)
        a = sparse.vstack([sparse.csr_matrix(np.vstack(ub)), extra_ub]) if cost is not None else np.vstack(ub)
        res = linprog(c, A_ub=a, b_ub=np.concatenate([bb, extra_b]), A_eq=a_eq_m, b_eq=b_eq_v, bounds=bounds,
                      method="highs")
        if res.status == 0:
            expected = float(res.x[3 * n:].sum()) if cost is not None else 0.0
            return np.clip(res.x[:n], 0, None), with_turnover or p.sum() == 0, expected
    return None, False, 0.0


def build_weights_lp(scores: pd.DataFrame, panel: Panel, universe_mask: np.ndarray, rebalance_every: int = 5,
                     industry_dev: float = 0.02, name_cap: float = 0.01, turnover_cap: float = 0.15,
                     size_limit: float = 0.2, beta: np.ndarray | None = None,
                     beta_range: tuple[float, float] | None = None, cfg=None,
                     costs: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """``cfg`` (a PortfolioLink) switches on the docs/21 options: ``objective="net_alpha_pwl"`` and
    ``active_liquidity_adv_fraction``; both read liquidity through the decision day's close only."""
    dates, codes = panel.dates, panel.codes
    s = scores.reindex(index=dates, columns=codes).to_numpy(dtype=float)
    cap = panel.wide("circ_mv").to_numpy(dtype=float)
    groups = panel.industry().reindex(codes).fillna("unknown").to_numpy()
    net = cfg is not None and cfg.objective == "net_alpha_pwl"
    liquidity_cap = cfg is not None and cfg.active_liquidity_adv_fraction is not None
    if net or liquidity_cap:
        from alphasieve.strategy.execution import DEFAULT_COSTS, trailing_liquidity

        adv, vol = trailing_liquidity(panel)
        c = {**DEFAULT_COSTS, **(costs or {})}
    active = np.flatnonzero(np.isfinite(np.where(universe_mask, s, np.nan)).any(axis=1))
    prev = np.zeros(len(codes))
    rows, relaxed, failed = {}, 0, 0
    expected, frozen_names, capped_names = [], [], []
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
        names_cap, cost = name_cap, None
        if net or liquidity_cap:
            a = adv[t][idx]
            known = np.isfinite(a) & (a > 0)
        if liquidity_cap:
            names_cap = np.where(known, np.minimum(name_cap, cfg.active_liquidity_adv_fraction
                                                   * np.where(known, a, 0.0) / cfg.liquidity_design_aum), 0.0)
            capped_names.append(float((names_cap < name_cap).mean()))
        if net:
            safe = np.where(known, a, 1.0)
            kappa = np.where(known, c.get("impact_k", 0.5) * np.nan_to_num(vol[t][idx], nan=0.02)
                             * np.sqrt(cfg.aum / safe), 0.0)
            cost = {"alpha_scale": cfg.alpha_return_scale, "c_buy": c["commission"] + c["slippage"],
                    "c_sell": c["commission"] + c["slippage"] + c["stamp_duty_sell"], "kappa": kappa,
                    "frozen": ~known}
            frozen_names.append(int((~known).sum()))
        w_sub, ok_turnover, exp_impact = _solve(z[idx], b[idx], prev[idx], member[idx], groups[idx], size_z, bt,
                                                names_cap, industry_dev, size_limit, beta_range, turnover_cap, cost)
        if w_sub is None:
            failed += 1
            continue
        relaxed += 0 if ok_turnover else 1
        expected.append(exp_impact)
        w = np.zeros(len(codes))
        w[idx] = w_sub
        w[w < 1e-6] = 0.0
        w /= w.sum()
        rows[dates[t]] = w
        prev = w
    frame = pd.DataFrame.from_dict(rows, orient="index", columns=codes)
    info = {"construction": "lp", "turnover_relaxed_dates": relaxed, "infeasible_dates": failed}
    if net:
        info |= {"objective": "net_alpha_pwl", "expected_impact_per_rebalance_mean": float(np.mean(expected)),
                 "names_without_liquidity_mean": float(np.mean(frozen_names))}
    if liquidity_cap:
        info["names_below_uniform_cap_share"] = float(np.mean(capped_names))
    return frame, info
