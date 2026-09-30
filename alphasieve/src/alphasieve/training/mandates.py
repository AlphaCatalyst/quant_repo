"""Portfolio construction, backtests and dev acceptance per mandate (docs/18 §3–6, docs/19 §2.5, §5).

Acceptance thresholds are the dev thresholds written in docs/18; results are reported against them, they never
change a gate. The futures-hedged mandate shorts index futures from Sina's contract bars (``data.futures``); there
is no futures data before 2017, so those years use the index proxy and are reported but not judged.
"""

import numpy as np
import pandas as pd

from alphasieve.data.access import Panel
from alphasieve.strategy.execution import simulate
from alphasieve.strategy.portfolio import build_weights, weight_diagnostics

ACCEPTANCE = {
    "A": {"annual_excess_min": 0.06, "information_ratio_min": 1.0, "max_drawdown_excess_min": -0.08,
          "positive_years_share_min": 5 / 7, "capacity_drop_max": 0.015},
    "D": {"annual_return_min": 0.05, "annual_vol_max": 0.06, "max_drawdown_min": -0.05, "sharpe_min": 1.0},
    "C": {"car_spread_t_min": 3.0, "decile_monotonicity_min": 0.8, "annual_excess_min": 0.05},
    "B": {"rank_ic_min": 0.03, "annual_excess_min": 0.05, "sharpe_min": 0.8},
}
# approved dev strategy-trial budgets: docs/20 §1 (B, C, D) and docs/21 §3 (A)
STRATEGY_TRIAL_BUDGET = {"A": 13, "B": 5, "C": 4, "D": 4}
CAPACITY_AUMS = (1e8, 5e8, 2e9)
FUTURES_ROLL_COST = 0.0002   # open the next contract, slippage and the settlement fee, per unit of notional
TE_TARGET = (0.04, 0.06)
ANN = 252


def _clean(sim: dict) -> dict:
    return {k: v for k, v in sim.items() if not k.startswith("_")}


def proxy_tracking(panel: Panel, members: np.ndarray, index_returns: np.ndarray | None) -> dict:
    """How well the cap-weighted member proxy tracks the real index (docs/18 G-2)."""
    if index_returns is None:
        return {"available": False}
    cap = panel.wide("circ_mv").to_numpy(dtype=float)
    close = panel.wide("close").to_numpy(dtype=float)
    r = np.full(close.shape, np.nan)
    r[1:] = close[1:] / close[:-1] - 1
    w = np.where(members & np.isfinite(cap) & (cap > 0), cap, 0.0)
    w_prev = np.zeros_like(w)
    w_prev[1:] = w[:-1]
    denom = w_prev.sum(axis=1)
    safe = np.where(denom > 0, denom, 1.0)
    proxy = np.where(denom > 0, np.nansum(w_prev * np.nan_to_num(r), axis=1) / safe, np.nan)
    window = panel.window_mask().to_numpy()
    ok = window & np.isfinite(proxy) & np.isfinite(index_returns) & (denom > 0)
    diff = proxy[ok] - index_returns[ok]
    return {"available": True, "days": int(ok.sum()), "tracking_error": float(diff.std() * np.sqrt(ANN)),
            "annual_gap": float(diff.mean() * ANN),
            "correlation": float(np.corrcoef(proxy[ok], index_returns[ok])[0, 1])}


def _years_positive(sim: dict) -> tuple[int, int]:
    years = sim.get("excess_by_year", {})
    return sum(1 for v in years.values() if v > 0), len(years)


def neutral_score(panel: Panel, score: pd.DataFrame, members: np.ndarray, factors: list[str]) -> pd.DataFrame:
    """Per-date residual of the score on industry dummies and log float cap inside the benchmark members, so the
    size limit does not have to be met by blending the whole portfolio back towards the benchmark."""
    from alphasieve.training.samples import industry_codes, residualize

    if not factors:
        return score
    extra = [np.log(panel.wide("circ_mv").to_numpy(dtype=float).clip(min=1.0))] if "log_circ_mv" in factors else []
    ind = industry_codes(panel) if "industry" in factors else np.zeros(len(panel.codes), dtype=int)
    values = score.reindex(index=panel.dates, columns=panel.codes).to_numpy(dtype=float)
    res = residualize(values, members & np.isfinite(values), ind, extra, min_names=50)
    return pd.DataFrame(res, index=panel.dates, columns=panel.codes)


def ema_scores(score: pd.DataFrame, half_life: float) -> pd.DataFrame:
    """Per-name exponential smoothing; a missing day (or leaving the pool) clears the state, nothing is filled."""
    alpha = 1 - 2 ** (-1 / half_life)
    x = score.to_numpy(dtype=float)
    out = np.full(x.shape, np.nan)
    state = np.full(x.shape[1], np.nan)
    for t in range(len(x)):
        ok = np.isfinite(x[t])
        state = np.where(ok, np.where(np.isfinite(state), alpha * x[t] + (1 - alpha) * state, x[t]), np.nan)
        out[t] = state
    return pd.DataFrame(out, index=score.index, columns=score.columns)


PERIODS = {"all": ("2016", "2022"), "ex_2016": ("2017", "2022"), "2016-2018": ("2016", "2018"),
           "2019-2020": ("2019", "2020"), "2021-2022": ("2021", "2022")}


def _excess_stats(excess: pd.Series) -> dict:
    if excess.empty:
        return {"days": 0}
    te = float(excess.std() * np.sqrt(ANN))
    curve = (1 + excess).cumprod()
    return {"days": int(len(excess)), "annual_excess": float(excess.mean() * ANN), "tracking_error": te,
            "information_ratio": float(excess.mean() * ANN / te) if te > 0 else float("nan"),
            "max_drawdown_excess": float((curve / curve.cummax() - 1).min())}


def robustness(main: dict, large: dict, base: dict) -> dict:
    """Slices of one continuous simulation (docs/21 §4.2) and the extra screens of the docs/21 §4.3 winner rule."""
    excess = main["_daily"]["ret"] - main["_daily"]["bench"]
    periods = {k: _excess_stats(excess.loc[lo:hi]) for k, (lo, hi) in PERIODS.items()}
    large_excess = large["_daily"]["ret"] - large["_daily"]["bench"]
    at_2e9 = _excess_stats(large_excess)
    subs = [periods[k].get("annual_excess", float("nan")) for k in ("2016-2018", "2019-2020", "2021-2022")]
    ex = periods["ex_2016"]
    screens = {
        "ex_2016_excess": [ex["annual_excess"], 0.03, ex["annual_excess"] >= 0.03],
        "ex_2016_ir": [ex["information_ratio"], 0.5, ex["information_ratio"] >= 0.5],
        "sub_periods_positive": [f"{sum(v > 0 for v in subs)}/3", 2, sum(v > 0 for v in subs) >= 2],
        "excess_2021_2022": [subs[2], 0.0, subs[2] > 0],
        "excess_at_2e9": [at_2e9["annual_excess"], 0.0, at_2e9["annual_excess"] > 0],
        "ir_at_2e9": [at_2e9["information_ratio"], 0.8, at_2e9["information_ratio"] >= 0.8],
        "no_impact_minus_2e9": [base["annual_excess"] - at_2e9["annual_excess"], 0.015,
                                base["annual_excess"] - at_2e9["annual_excess"] <= 0.015]}
    return {"periods": periods, "at_2e9": at_2e9, "screens": screens,
            "screens_passed": all(s[2] for s in screens.values())}


def index_enhancement(panel: Panel, score: pd.DataFrame, members: np.ndarray, beta: np.ndarray, cfg, costs: dict,
                      benchmark: str, index_returns: np.ndarray | None) -> dict:
    score = neutral_score(panel, score, members, list(cfg.neutralize_score))
    if cfg.score_ema_half_life is not None:
        score = ema_scores(score, cfg.score_ema_half_life)
    if cfg.construction == "lp":
        from alphasieve.strategy.portfolio_lp import build_weights_lp

        weights, lp_info = build_weights_lp(score, panel, members, cfg.rebalance_every, cfg.industry_dev, cfg.name_cap,
                                            cfg.turnover_cap, cfg.size_limit, beta, tuple(cfg.beta_range), cfg=cfg,
                                            costs=costs)
    else:
        weights = build_weights(score, panel, cfg.rebalance_every, cfg.industry_dev, cfg.name_cap, cfg.turnover_cap,
                                cfg.size_limit, universe_mask=members, beta=beta, beta_range=tuple(cfg.beta_range),
                                active_scale=cfg.active_scale)
        lp_info = {"construction": "heuristic"}
    diag = weight_diagnostics(weights, panel, cfg.turnover_cap, universe_mask=members, beta=beta) | lp_info
    # Portfolio returns use adjusted (total-return) prices while the index is a price index, so acceptance is
    # judged against the total-return member proxy; the price-index comparison is reported alongside.
    base = simulate(weights, panel, costs, None, universe_mask=members)
    runs = {f"{aum:.0e}": simulate(weights, panel, costs, None, aum=aum, max_participation=cfg.max_participation,
                                   universe_mask=members) for aum in CAPACITY_AUMS}
    main = simulate(weights, panel, costs, None, aum=cfg.aum, max_participation=cfg.max_participation,
                    universe_mask=members)
    vs_index = simulate(weights, panel, costs, benchmark, aum=cfg.aum, max_participation=cfg.max_participation,
                        universe_mask=members)
    capacity = {k: {"annual_excess": v["annual_excess"], "information_ratio": v["information_ratio"],
                    "tracking_error": v["tracking_error"], "max_drawdown_excess": v["max_drawdown_excess"],
                    "annual_cost": v["annual_cost"], "annual_impact_cost": v["annual_impact_cost"],
                    "annual_turnover": v["annual_turnover"], "capped_trade_share": v["capped_trade_share"],
                    "invested_mean": v["invested_mean"]}
                for k, v in runs.items()}
    pos, n_years = _years_positive(main)
    rule = ACCEPTANCE["A"]
    drop = base["annual_excess"] - main["annual_excess"]
    checks = {
        "annual_excess": [main["annual_excess"], rule["annual_excess_min"],
                          main["annual_excess"] >= rule["annual_excess_min"]],
        "information_ratio": [main["information_ratio"], rule["information_ratio_min"],
                              main["information_ratio"] >= rule["information_ratio_min"]],
        "max_drawdown_excess": [main["max_drawdown_excess"], rule["max_drawdown_excess_min"],
                                main["max_drawdown_excess"] >= rule["max_drawdown_excess_min"]],
        "positive_years": [f"{pos}/{n_years}", rule["positive_years_share_min"],
                           n_years > 0 and pos / n_years >= rule["positive_years_share_min"] - 1e-9],
        "capacity_drop_at_aum": [drop, rule["capacity_drop_max"], drop <= rule["capacity_drop_max"]],
    }
    te = main["tracking_error"]
    diag["tracking_error_target"] = {"value": te, "range": TE_TARGET, "inside": TE_TARGET[0] <= te <= TE_TARGET[1]}
    return {"weights": weights, "portfolio": diag, "benchmark_basis": "total-return member proxy (cap-weighted)",
            "execution": _clean(main), "execution_vs_price_index": _clean(vs_index),
            "execution_no_impact": _clean(base),
            "capacity": capacity, "benchmark_proxy": proxy_tracking(panel, members, index_returns),
            "robustness": robustness(main, runs[f"{CAPACITY_AUMS[-1]:.0e}"], base),
            "acceptance": {"checks": checks, "passed": all(c[2] for c in checks.values())},
            "_daily": main["_daily"], "_excess_nav": main["_excess_nav"], "_nav": main["_nav"]}


def _return_stats(ret: pd.Series) -> dict:
    if len(ret) < 2:
        return {"days": int(len(ret))}
    nav = (1 + ret).cumprod()
    vol = float(ret.std() * np.sqrt(ANN))
    return {"days": int(len(ret)), "first": str(ret.index[0].date()), "last": str(ret.index[-1].date()),
            "annual_return": float(nav.iloc[-1] ** (ANN / len(ret)) - 1), "annual_vol": vol,
            "max_drawdown": float((nav / nav.cummax() - 1).min()),
            "sharpe": float(ret.mean() * ANN / vol) if vol > 0 else float("nan")}


def futures_hedged(panel: Panel, long_result: dict, beta: np.ndarray, cfg, index_returns: np.ndarray | None,
                   leg: pd.DataFrame | None = None) -> dict:
    """Long the A portfolio, short ``ratio x beta_hat`` index futures; capital also funds margin and a cash buffer.

    ``leg`` is the futures hedge leg (``data.futures.hedge_leg``). On days without futures data the short leg is
    the total-return member proxy, which omits the basis. Only the exact single-contract span is judged (docs/20
    §5.4); the ratio 1.0 is the headline, the other ``cfg.hedge_ratios`` are pre-registered and reported.
    """
    daily = long_result["_daily"]
    weights = long_result["weights"]
    b = pd.DataFrame(beta, index=panel.dates, columns=panel.codes).reindex(
        index=weights.index, columns=weights.columns).fillna(1.0)
    beta_hat = (weights * b).sum(axis=1).reindex(daily.index, method="ffill").shift(1).bfill()
    proxy = daily["bench"].fillna(0.0)
    long_share = 1.0 / (1.0 + cfg.margin + cfg.cash_buffer)
    if leg is None:
        leg = pd.DataFrame({"fut_ret": np.nan, "roll": False, "source": "none"}, index=daily.index)
    leg = leg.reindex(daily.index)
    covered = leg["fut_ret"].notna() & leg["source"].isin(["contract", "continuous"])
    exact = covered & (leg["source"] == "contract")
    short = leg["fut_ret"].where(covered, proxy)
    rolls = (leg["roll"].fillna(False).astype(bool) & covered).astype(float)
    price_ret = pd.Series(index_returns, index=panel.dates).reindex(daily.index) if index_returns is not None \
        else pd.Series(np.nan, index=daily.index)
    carry = -(leg["fut_ret"] - price_ret)

    def hedged(ratio: float) -> pd.Series:
        return long_share * (daily["ret"] - ratio * beta_hat * (short + FUTURES_ROLL_COST * rolls))

    def segments(ret: pd.Series) -> dict:
        return {"contract_exact": _return_stats(ret[exact]), "futures": _return_stats(ret[covered]),
                "inferred_ic0": _return_stats(ret[covered & ~exact]), "no_futures_proxy": _return_stats(ret[~covered]),
                "all": _return_stats(ret)}

    ret = hedged(1.0)
    nav = (1 + ret).cumprod()
    on = exact
    attribution = {"long_leg": float(long_share * daily["ret"][on].mean() * ANN),
                   "short_index_price": float(-long_share * (beta_hat * price_ret)[on].mean() * ANN),
                   "basis_carry": float(long_share * (beta_hat * carry)[on].mean() * ANN),
                   "roll_cost": float(-long_share * (beta_hat * FUTURES_ROLL_COST * rolls)[on].mean() * ANN),
                   "note": "arithmetic annual means on the exact span at ratio 1.0; long_leg + short_index_price is"
                           " the total-return excess over the price index, basis_carry is minus the futures return"
                           " over the price index"}
    judged = segments(ret)["contract_exact"]
    rule = ACCEPTANCE["D"]
    checks = {}
    if judged.get("days", 0) >= ANN:
        checks = {"annual_return": [judged["annual_return"], rule["annual_return_min"],
                                    judged["annual_return"] >= rule["annual_return_min"]],
                  "annual_vol": [judged["annual_vol"], rule["annual_vol_max"],
                                 judged["annual_vol"] <= rule["annual_vol_max"]],
                  "max_drawdown": [judged["max_drawdown"], rule["max_drawdown_min"],
                                   judged["max_drawdown"] >= rule["max_drawdown_min"]],
                  "sharpe": [judged["sharpe"], rule["sharpe_min"], judged["sharpe"] >= rule["sharpe_min"]]}
    by_year = ret.groupby(ret.index.year).apply(lambda s: float((1 + s).prod() - 1))
    carry_by_year = carry[covered].groupby(carry[covered].index.year).sum()
    passed = bool(checks) and all(c[2] for c in checks.values())
    return {"basis_included": bool(covered.any()),
            "note": "short leg: held front-month futures (exact from Sina single contracts, inferred rolls on the"
                    " IC0 splice before them); days without futures use the total-return member proxy",
            "futures_days_share": float(covered.mean()), "roll_cost_per_roll": FUTURES_ROLL_COST,
            "long_share_of_capital": long_share, "segments": segments(ret),
            "by_hedge_ratio": {str(r): segments(hedged(r))["contract_exact"] for r in cfg.hedge_ratios},
            "attribution_exact": attribution,
            "correlation_with_index": float(np.corrcoef(ret, proxy)[0, 1]) if len(ret) > 2 else float("nan"),
            "beta_hat_mean": float(beta_hat.mean()),
            "hedge_carry_vs_price_index_annual": float(carry[covered].mean() * ANN) if covered.any() else None,
            "hedge_carry_vs_total_return_proxy_annual":
                float(-(leg["fut_ret"] - proxy)[covered].mean() * ANN) if covered.any() else None,
            "hedge_carry_by_year": {str(k): float(v) for k, v in carry_by_year.items()},
            "return_by_year": {str(k): v for k, v in by_year.items()},
            "acceptance": {"judged_on": "exact single-contract span, hedge ratio 1.0", "checks": checks,
                           "passed": passed,
                           **({} if checks else {"blocked": "less than a year of exact futures days"})},
            "_nav": nav}
