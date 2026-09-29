"""Simplified B3 execution simulation (S-6): daily loop with A-share trading rules and itemised costs.

Target weights decided at a rebalance date's close are executed at the next day's open. A name that cannot be
bought (suspended or opening limit-up) keeps its old weight when the target is higher; a name that cannot be sold
(suspended or opening limit-down) keeps its old weight when the target is lower; unfilled weight stays in cash.
Costs follow configs/costs.yaml B3 values when present (commission both sides, stamp duty on sells, slippage).
With ``aum`` set, each trade is capped at ``max_participation`` of the trailing 20-day average traded amount
(the rest waits for the next rebalance) and pays square-root impact ``impact_k * vol_20d * sqrt(participation)``.
"""

import numpy as np
import pandas as pd

from alphasieve.data.access import Panel

DEFAULT_COSTS = {"commission": 0.00025, "stamp_duty_sell": 0.0005, "slippage": 0.0005}


def simulate(weights: pd.DataFrame, panel: Panel, costs: dict | None = None, benchmark: str | None = None,
             aum: float | None = None, max_participation: float = 0.10,
             universe_mask: np.ndarray | None = None, hold_unchanged: bool = False) -> dict:
    """``hold_unchanged``: on a target day, names whose target equals their previous target are not traded (their
    weight drifts), which is how an event book is run; ``benchmark="equal_weight"`` uses the equal-weighted
    universe (total return) instead of a named index."""
    c = {**DEFAULT_COSTS, **(costs or {})}
    dates, codes = panel.dates, panel.codes
    w_target = weights.reindex(columns=codes).fillna(0.0)
    open_px = panel.wide("open").to_numpy(dtype=float)
    close_px = panel.wide("close").to_numpy(dtype=float)
    buy_ok = panel.mask("tradable_buy").to_numpy()
    sell_ok = panel.mask("tradable_sell").to_numpy()
    cap = panel.wide("circ_mv").to_numpy(dtype=float)
    universe = panel.mask("in_universe").to_numpy() if universe_mask is None else universe_mask
    if aum:
        amount = pd.DataFrame(panel.wide("amount").to_numpy(dtype=float), index=dates)
        adv = amount.rolling(20, min_periods=5).mean().shift(1).to_numpy()
        rets = pd.DataFrame(panel.wide("ret_1d").to_numpy(dtype=float), index=dates)
        vol = rets.rolling(20, min_periods=5).std().shift(1).to_numpy()
    impact_total, capped_trades, trades_total = 0.0, 0, 0
    targets = {dates.get_loc(d): row.to_numpy() for d, row in w_target.iterrows()}
    if not targets:
        return {"days": 0}
    start = min(targets) + 1
    holdings = np.zeros(len(codes))
    cash, nav_prev, rows = 1.0, 1.0, []
    bench_close = None
    equal_weight = benchmark == "equal_weight"
    prev_goal = np.zeros(len(codes))
    named = benchmark and not equal_weight and panel.benchmark is not None
    if named and f"{benchmark}_close" in panel.benchmark.columns:
        b = panel.benchmark.set_index(pd.to_datetime(panel.benchmark["date"]))[f"{benchmark}_close"]
        bench_close = b.reindex(dates).ffill().to_numpy(dtype=float)
    for t in range(start, len(dates)):
        with np.errstate(invalid="ignore", divide="ignore"):
            overnight = np.nan_to_num(open_px[t] / close_px[t - 1] - 1, nan=0.0, posinf=0.0, neginf=0.0)
            intraday = np.nan_to_num(close_px[t] / open_px[t] - 1, nan=0.0, posinf=0.0, neginf=0.0)
        holdings = holdings * (1 + overnight)
        cost = 0.0
        if (t - 1) in targets:
            value = holdings.sum() + cash
            current = holdings / value
            goal = targets[t - 1]
            if hold_unchanged:
                same = np.isclose(goal, prev_goal) & (goal > 0)
                goal = np.where(same, current, goal)
                prev_goal = targets[t - 1]
            blocked = ((goal > current) & ~buy_ok[t]) | ((goal < current) & ~sell_ok[t])
            new = np.where(blocked, current, goal)
            impact = 0.0
            if aum:
                limit = max_participation * np.nan_to_num(adv[t], nan=0.0) / max(value * aum, 1.0)
                delta = new - current
                over = np.abs(delta) > limit + 1e-12
                trades_total += int((np.abs(delta) > 1e-9).sum())
                capped_trades += int((over & (np.abs(delta) > 1e-9)).sum())
                new = np.where(over, current + np.sign(delta) * limit, new)
                traded = np.abs(new - current)
                with np.errstate(invalid="ignore", divide="ignore"):
                    part = np.where(np.nan_to_num(adv[t]) > 0, traded * value * aum / adv[t], 0.0)
                impact = float((c.get("impact_k", 0.5) * np.nan_to_num(vol[t], nan=0.02) * np.sqrt(part)
                                * traded).sum())
            if new.sum() > 1:
                free = ~blocked
                new = np.where(free, new * max(0.0, 1 - (new.sum() - 1) / max(new[free].sum(), 1e-12)), new)
            buys = np.clip(new - current, 0, None).sum()
            sells = np.clip(current - new, 0, None).sum()
            cost = buys * (c["commission"] + c["slippage"]) + sells * (c["commission"] + c["slippage"]
                                                                        + c["stamp_duty_sell"]) + impact
            impact_total += impact
            holdings = new * value
            cash = value - holdings.sum() - cost * value
        holdings = holdings * (1 + intraday)
        nav = holdings.sum() + cash
        day_ret = nav / nav_prev - 1
        nav_prev = nav
        if bench_close is not None and np.isfinite(bench_close[t]) and np.isfinite(bench_close[t - 1]):
            bench_ret = bench_close[t] / bench_close[t - 1] - 1
        else:
            ok = universe[t - 1] & np.isfinite(cap[t - 1]) & (cap[t - 1] > 0)
            with np.errstate(invalid="ignore", divide="ignore"):
                r = np.nan_to_num(close_px[t] / close_px[t - 1] - 1, nan=0.0, posinf=0.0, neginf=0.0)
            if equal_weight:
                bench_ret = float(r[ok].mean()) if ok.any() else 0.0
            else:
                bench_ret = float((cap[t - 1, ok] * r[ok]).sum() / cap[t - 1, ok].sum()) if ok.any() else 0.0
        rows.append({"date": dates[t], "ret": day_ret, "bench": bench_ret, "cost": cost,
                     "invested": holdings.sum() / nav if nav > 0 else 0.0})
    df = pd.DataFrame(rows).set_index("date")
    excess = df["ret"] - df["bench"]
    ann = 252
    curve = (1 + excess).cumprod()
    te = float(excess.std() * np.sqrt(ann))
    by_year = excess.groupby(excess.index.year).apply(lambda s: float((1 + s).prod() - 1))
    return {
        "days": int(len(df)),
        "annual_return": float((1 + df["ret"]).prod() ** (ann / len(df)) - 1),
        "annual_benchmark": float((1 + df["bench"]).prod() ** (ann / len(df)) - 1),
        "annual_excess": float(excess.mean() * ann),
        "tracking_error": te,
        "information_ratio": float(excess.mean() * ann / te) if te > 0 else float("nan"),
        "max_drawdown_excess": float((curve / curve.cummax() - 1).min()),
        "annual_cost": float(df["cost"].sum() * ann / len(df)),
        "invested_mean": float(df["invested"].mean()),
        "excess_by_year": {str(k): v for k, v in by_year.items()},
        "benchmark": benchmark if bench_close is not None else ("equal_weight_universe" if equal_weight
                                                                else "cap_weighted_universe"),
        "aum": aum, "annual_impact_cost": float(impact_total * ann / len(df)),
        "capped_trade_share": float(capped_trades / trades_total) if trades_total else 0.0,
        "_daily": df,
        "_nav": df["ret"].add(1).cumprod(), "_excess_nav": curve,
    }
