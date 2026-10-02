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


def initial_state(codes: list[str], capital: float = 1.0) -> dict:
    """A cash book whose holdings are values in the same unit as capital."""
    if capital <= 0:
        raise ValueError("capital must be positive")
    return {"codes": list(codes), "holdings": [0.0] * len(codes), "cash": float(capital),
            "nav": float(capital), "previous_goal": [0.0] * len(codes)}


def step_day(state: dict, *, prev_close: np.ndarray, open_px: np.ndarray, close_px: np.ndarray,
             buy_ok: np.ndarray, sell_ok: np.ndarray, goal: np.ndarray | None = None,
             costs: dict | None = None, adv: np.ndarray | None = None, vol: np.ndarray | None = None,
             aum: float | None = None, max_participation: float = 0.10,
             hold_unchanged: bool = False, strict_prices: bool = True) -> tuple[dict, dict]:
    """Settle one trading day. Liquidity inputs must be frozen at the preceding close.

    Missing prices of a held name are errors; explicit suspensions supply an unchanged
    valuation price and false trade flags. Callers must not turn an unknown price into a halt.
    """
    h = np.asarray(state["holdings"], dtype=float).copy()
    cash = float(state["cash"])
    previous = np.asarray(prev_close, dtype=float)
    opening = np.asarray(open_px, dtype=float)
    closing = np.asarray(close_px, dtype=float)
    if not strict_prices:
        previous = np.where(np.isfinite(previous) & (previous > 0), previous,
                            np.where(np.isfinite(opening) & (opening > 0), opening, 1.0))
        opening = np.where(np.isfinite(opening) & (opening > 0), opening, previous)
        closing = np.where(np.isfinite(closing) & (closing > 0), closing, opening)
    if any(x.shape != h.shape for x in (previous, opening, closing)):
        raise ValueError("price shape does not match book")
    held = h > 1e-12
    halted = held & ~np.asarray(buy_ok, dtype=bool) & ~np.asarray(sell_ok, dtype=bool)
    if strict_prices:
        opening = np.where(halted & ~np.isfinite(opening), previous, opening)
        closing = np.where(halted & ~np.isfinite(closing), opening, closing)
    if not (np.isfinite(previous[held]).all() and np.isfinite(opening[held]).all()
            and np.isfinite(closing[held]).all() and (previous[held] > 0).all()
            and (opening[held] > 0).all()):
        raise ValueError("missing held-name valuation price")
    overnight = np.ones_like(h)
    overnight[held] = opening[held] / previous[held]
    h *= overnight
    value = float(h.sum() + cash)
    fills = []
    costs_used = {**DEFAULT_COSTS, **(costs or {})}
    totals = {"commission": 0.0, "stamp_duty": 0.0, "slippage": 0.0, "impact": 0.0}
    requested_turnover = actual_turnover = 0.0
    capped = rejected = 0
    prior_goal = np.asarray(state.get("previous_goal", np.zeros(len(h))), dtype=float)
    if goal is not None:
        target = np.asarray(goal, dtype=float)
        if target.shape != h.shape or not np.isfinite(target).all() or (target < 0).any() or target.sum() > 1 + 1e-9:
            raise ValueError("invalid target weights")
        current = h / value
        wanted = target.copy()
        if hold_unchanged:
            same = np.isclose(wanted, prior_goal) & (wanted > 0)
            wanted = np.where(same, current, wanted)
        requested_turnover = float(np.abs(wanted - current).sum() / 2)
        blocked = ((wanted > current) & ~np.asarray(buy_ok, dtype=bool)) | \
            ((wanted < current) & ~np.asarray(sell_ok, dtype=bool))
        new = np.where(blocked, current, wanted)
        rejected = int((blocked & (np.abs(wanted - current) > 1e-12)).sum())
        participation = np.zeros(len(h))
        impact_by_name = np.zeros(len(h))
        if aum:
            if adv is None or vol is None:
                raise ValueError("frozen ADV and volatility required with AUM")
            adv_arr = np.asarray(adv, dtype=float)
            vol_arr = np.asarray(vol, dtype=float)
            limit = max_participation * np.nan_to_num(adv_arr, nan=0.0) / max(value * aum, 1.0)
            delta = new - current
            over = np.abs(delta) > limit + 1e-12
            capped = int((over & (np.abs(delta) > 1e-9)).sum())
            new = np.where(over, current + np.sign(delta) * limit, new)
        if new.sum() > 1:
            free = ~blocked
            new = np.where(free, new * max(0.0, 1 - (new.sum() - 1) / max(new[free].sum(), 1e-12)), new)
        delta = new - current
        if aum:
            traded = np.abs(delta)
            with np.errstate(invalid="ignore", divide="ignore"):
                participation = np.where(np.nan_to_num(adv_arr) > 0, traded * value * aum / adv_arr, 0.0)
            impact_by_name = (costs_used.get("impact_k", 0.5) * np.nan_to_num(vol_arr, nan=0.02)
                              * np.sqrt(participation) * traded)
        buys = np.clip(delta, 0, None)
        sells = np.clip(-delta, 0, None)
        actual_turnover = float((buys.sum() + sells.sum()) / 2)
        totals["commission"] = float((buys.sum() + sells.sum()) * costs_used["commission"] * value)
        totals["stamp_duty"] = float(sells.sum() * costs_used["stamp_duty_sell"] * value)
        totals["slippage"] = float((buys.sum() + sells.sum()) * costs_used["slippage"] * value)
        totals["impact"] = float(impact_by_name.sum() * value)
        for i, change in enumerate(delta):
            if abs(change) > 1e-12 or (blocked[i] and abs(wanted[i] - current[i]) > 1e-12):
                reason = "blocked" if blocked[i] else (
                    "participation_cap" if aum and abs(new[i] - wanted[i]) > 1e-12 else "filled")
                fills.append({"code": state["codes"][i], "side": "buy" if wanted[i] > current[i] else "sell",
                              "open": float(opening[i]), "value": float(change * value), "reason": reason,
                              "commission": float(abs(change) * value * costs_used["commission"]),
                              "stamp_duty": float(sells[i] * value * costs_used["stamp_duty_sell"]),
                              "slippage": float(abs(change) * value * costs_used["slippage"]),
                              "impact": float(impact_by_name[i] * value), "participation": float(participation[i])})
        h = new * value
        cash = value - float(h.sum()) - sum(totals.values())
        prior_goal = target
    active = h > 1e-12
    if not (np.isfinite(opening[active]).all() and np.isfinite(closing[active]).all()
            and (opening[active] > 0).all() and (closing[active] > 0).all()):
        raise ValueError("missing active-name valuation price")
    intraday = np.ones_like(h)
    intraday[active] = closing[active] / opening[active]
    h *= intraday
    nav = float(h.sum() + cash)
    next_state = {"codes": list(state["codes"]), "holdings": h.tolist(), "cash": cash,
                  "nav": nav, "previous_goal": prior_goal.tolist()}
    detail = {"nav": nav, "ret": nav / float(state["nav"]) - 1, "open_nav": value,
              "costs": totals, "cost_ratio": sum(totals.values()) / value,
              "requested_turnover": requested_turnover, "actual_turnover": actual_turnover,
              "capped_trades": capped, "rejected_trades": rejected, "fills": fills,
              "weights": (h / nav).tolist() if nav > 0 else [0.0] * len(h),
              "stale_codes": [state["codes"][i] for i in np.where(halted)[0]]}
    return next_state, detail


def trailing_liquidity(panel: Panel) -> tuple[np.ndarray, np.ndarray]:
    """20-day mean traded amount and daily-return volatility through each day's close. A trade decided at the close
    of day t and executed at t+1 sees exactly this window, in the simulation and in the optimiser."""
    amount = pd.DataFrame(panel.wide("amount").to_numpy(dtype=float))
    rets = pd.DataFrame(panel.wide("ret_1d").to_numpy(dtype=float))
    return amount.rolling(20, min_periods=5).mean().to_numpy(), rets.rolling(20, min_periods=5).std().to_numpy()


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
        adv, vol = (np.vstack([np.full((1, x.shape[1]), np.nan), x[:-1]]) for x in trailing_liquidity(panel))
    impact_total, capped_trades, trades_total, traded_total = 0.0, 0, 0, 0.0
    targets = {dates.get_loc(d): row.to_numpy() for d, row in w_target.iterrows()}
    if not targets:
        return {"days": 0}
    start = min(targets) + 1
    state = initial_state(codes)
    rows = []
    bench_close = None
    equal_weight = benchmark == "equal_weight"
    named = benchmark and not equal_weight and panel.benchmark is not None
    if named and f"{benchmark}_close" in panel.benchmark.columns:
        b = panel.benchmark.set_index(pd.to_datetime(panel.benchmark["date"]))[f"{benchmark}_close"]
        bench_close = b.reindex(dates).ffill().to_numpy(dtype=float)
    for t in range(start, len(dates)):
        goal = targets.get(t - 1)
        state, detail = step_day(state, prev_close=close_px[t - 1], open_px=open_px[t], close_px=close_px[t],
                                 buy_ok=buy_ok[t], sell_ok=sell_ok[t], goal=goal, costs=c,
                                 adv=adv[t] if aum and goal is not None else None,
                                 vol=vol[t] if aum and goal is not None else None,
                                 aum=aum if goal is not None else None,
                                 max_participation=max_participation, hold_unchanged=hold_unchanged,
                                 strict_prices=False)
        impact_total += detail["costs"]["impact"] / detail["open_nav"]
        traded_total += detail["actual_turnover"]
        capped_trades += detail["capped_trades"]
        trades_total += sum(f["reason"] != "blocked" for f in detail["fills"])
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
        rows.append({"date": dates[t], "ret": detail["ret"], "bench": bench_ret,
                     "cost": detail["cost_ratio"], "invested": sum(detail["weights"])})
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
        "annual_turnover": float(traded_total * ann / len(df)),
        "capped_trade_share": float(capped_trades / trades_total) if trades_total else 0.0,
        "_daily": df,
        "_nav": df["ret"].add(1).cumprod(), "_excess_nav": curve,
    }
