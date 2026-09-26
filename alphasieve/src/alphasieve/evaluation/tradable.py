"""B2: long-only top-quantile evaluation with tradability constraints and approximate costs."""

import numpy as np
import pandas as pd


def long_only_excess(
    factor: pd.DataFrame,
    valid: pd.DataFrame,
    open_px: pd.DataFrame,
    tradable_buy: pd.DataFrame,
    tradable_sell: pd.DataFrame,
    window: pd.Series,
    rebalance_every: int = 5,
    top_fraction: float = 0.2,
    one_way_cost: float = 0.0015,
) -> dict:
    dates = factor.index
    positions = np.flatnonzero(window.to_numpy())
    rebal = [p for p in positions[::rebalance_every] if p + 1 < len(dates)]
    weights = pd.Series(dtype=float)
    rows = []
    for i, t in enumerate(rebal):
        nxt = rebal[i + 1] if i + 1 < len(rebal) else None
        if nxt is None:
            break
        entry, exit_ = t + 1, nxt + 1
        if exit_ >= len(dates):
            break
        scores = factor.iloc[t].where(valid.iloc[t]).dropna()
        buyable = tradable_buy.iloc[entry].reindex(scores.index).fillna(False).astype(bool)
        n_top = max(int(len(scores) * top_fraction), 1)
        chosen = scores[buyable].nlargest(n_top).index
        stuck = [c for c in weights.index if c not in chosen and not bool(tradable_sell.iloc[entry].get(c, False))]
        held = list(dict.fromkeys(list(chosen) + stuck))
        new_w = pd.Series(1.0 / len(held), index=held) if held else pd.Series(dtype=float)
        turnover = float(new_w.sub(weights, fill_value=0).abs().sum())
        period_ret = (open_px.iloc[exit_] / open_px.iloc[entry] - 1).replace([np.inf, -np.inf], np.nan)
        port = float((new_w * period_ret.reindex(new_w.index).fillna(0)).sum())
        bench_names = valid.iloc[t][valid.iloc[t]].index
        bench_names = [c for c in bench_names if bool(tradable_buy.iloc[entry].get(c, False))]
        bench = float(period_ret.reindex(bench_names).fillna(0).mean()) if bench_names else 0.0
        rows.append({"date": dates[t], "port": port, "bench": bench, "turnover": turnover,
                     "cost": turnover * one_way_cost})
        weights = new_w
    if not rows:
        return {"periods": 0}
    df = pd.DataFrame(rows)
    df["excess"] = df["port"] - df["bench"]
    df["excess_net"] = df["excess"] - df["cost"]
    per_year = 252 / rebalance_every
    curve = (1 + df["excess_net"]).cumprod()
    drawdown = float((curve / curve.cummax() - 1).min())
    std = float(df["excess_net"].std())
    return {
        "periods": int(len(df)),
        "annual_excess_gross": float(df["excess"].mean() * per_year),
        "annual_excess_net": float(df["excess_net"].mean() * per_year),
        "excess_sharpe_net": float(df["excess_net"].mean() / std * np.sqrt(per_year)) if std > 0 else float("nan"),
        "max_drawdown_excess": drawdown,
        "avg_turnover": float(df["turnover"].mean()),
    }
