"""PD-2 decision backtester and PD-3 matched-random baseline, vectorised over synthetic accounts.

A decision taken at a review-day close executes at the next open: sells first, then buys from the cash on hand,
in whole lots of 100 shares; a limit-up open blocks buys, a limit-down open blocks sells, suspended names do not
trade. A name that stops trading (suspension or delisting) is carried at its last close and cannot be sold.
"""

from dataclasses import dataclass

import numpy as np

from alphasieve.decisions.bars import Bars
from alphasieve.decisions.synthetic import Accounts


@dataclass(frozen=True)
class Costs:
    commission: float = 0.00025
    min_commission: float = 5.0
    stamp_sell: float = 0.001
    slippage: float = 0.0005
    lot: int = 100


@dataclass(frozen=True)
class Rule:
    target: str  # "equal" | "inverse_vol"
    cap: float | None
    band: float  # absolute weight deviation that triggers a full rebalance; 0 = every review
    review_days: int  # 5 = weekly, 21 = monthly

    @property
    def rule_id(self) -> str:
        cap = f"cap{round(self.cap * 100)}" if self.cap else "nocap"
        return f"{self.target}-{cap}-b{round(self.band * 100)}-{'w' if self.review_days == 5 else 'm'}"


@dataclass(frozen=True)
class Window:
    open: np.ndarray  # [A, H+1, K] adjusted, forward-filled
    close: np.ndarray
    open_raw: np.ndarray
    can_buy: np.ndarray  # bool
    can_sell: np.ndarray
    vol: np.ndarray  # trailing 60-day daily volatility
    mask: np.ndarray  # [A, K]
    weights: np.ndarray  # [A, K] initial weights
    start: np.ndarray  # [A]
    capital: float

    @property
    def horizon(self) -> int:
        return self.open.shape[1] - 1


def trailing_vol(bars: Bars, window: int = 60) -> np.ndarray:
    close = bars["close"].astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        ret = np.log(close[1:] / close[:-1])
    ret = np.vstack([np.full((1, close.shape[1]), np.nan), ret])
    ok = np.isfinite(ret)
    r = np.where(ok, ret, 0.0)
    c1, c2, cn = np.cumsum(r, 0), np.cumsum(r * r, 0), np.cumsum(ok, 0).astype(np.float64)
    for arr in (c1, c2, cn):
        arr[window:] = arr[window:] - arr[:-window].copy()
    with np.errstate(invalid="ignore", divide="ignore"):
        var = (c2 - c1 * c1 / cn) / (cn - 1)
    return np.where(cn >= window * 2 // 3, np.sqrt(np.maximum(var, 0)), np.nan).astype(np.float32)


def _ffill(values: np.ndarray) -> np.ndarray:
    """Forward-fill along axis 1 of [A, H+1, K]; leading gaps take the first valid value."""
    out = values.copy()
    for t in range(1, out.shape[1]):
        gap = ~np.isfinite(out[:, t])
        out[:, t][gap] = out[:, t - 1][gap]
    for t in range(out.shape[1] - 2, -1, -1):
        gap = ~np.isfinite(out[:, t])
        out[:, t][gap] = out[:, t + 1][gap]
    return out


def gather(bars: Bars, accounts: Accounts, vol: np.ndarray) -> Window:
    h = accounts.spec.horizon_days
    rows = accounts.start[:, None] + np.arange(h + 1)[None, :]  # [A, H+1]
    cols = np.where(accounts.mask, accounts.names, 0)  # [A, K]
    take = (rows[:, :, None], cols[:, None, :])
    pad = ~accounts.mask[:, None, :]

    def field(name, fill=1.0):
        values = bars[name][take].astype(np.float64)
        values[np.broadcast_to(pad, values.shape)] = fill
        return values

    open_, close = field("open"), field("close")
    traded = np.isfinite(open_) & np.isfinite(close) & ~np.broadcast_to(pad, open_.shape)
    close = _ffill(close)
    open_ = np.where(np.isfinite(open_), open_, close)
    open_raw = _ffill(field("open_raw"))
    can_buy = bars["tradable_buy"][take] & traded
    can_sell = bars["tradable_sell"][take] & traded
    v = vol[take].astype(np.float64)
    v[np.broadcast_to(pad, v.shape)] = np.nan
    return Window(open_, close, open_raw, can_buy, can_sell, v, accounts.mask, accounts.weights,
                  accounts.start, accounts.spec.capital)


def target_weights(rule: Rule, mask: np.ndarray, vol: np.ndarray) -> np.ndarray:
    if rule.target == "equal":
        raw = mask.astype(np.float64)
    elif rule.target == "inverse_vol":
        usable = mask & np.isfinite(vol) & (vol > 0)
        count = usable.sum(axis=1, keepdims=True)
        mean = np.where(usable, vol, 0.0).sum(axis=1, keepdims=True) / np.maximum(count, 1)
        filled = np.where(usable, vol, np.where(count > 0, mean, 1.0))
        raw = np.where(mask, 1.0 / filled, 0.0)
    else:
        raise ValueError(rule.target)
    w = raw / raw.sum(axis=1, keepdims=True)
    if rule.cap is None:
        return w
    # Water-filling: names above the cap are pinned there; the excess goes to uncapped names, the rest to cash.
    pinned = np.zeros_like(mask)
    for _ in range(mask.shape[1]):
        over = (w > rule.cap + 1e-12) & ~pinned
        if not over.any():
            break
        pinned |= over
        free = mask & ~pinned
        budget = 1.0 - rule.cap * pinned.sum(axis=1, keepdims=True)
        share = np.where(free, raw, 0.0)
        total = share.sum(axis=1, keepdims=True)
        w = np.where(pinned, rule.cap, share / np.where(total > 0, total, 1.0) * budget)
    return w


def simulate(win: Window, rule: Rule | None, costs: Costs = Costs(), forced: np.ndarray | None = None) -> dict:
    """``rule=None`` never trades. ``forced`` [A, R] replaces the band trigger (matched-random baseline)."""
    a_count, k = win.mask.shape
    h = win.horizon
    shares = np.where(win.mask, win.capital * win.weights / win.close[:, 0], 0.0)
    cash = np.zeros(a_count)
    peak = np.full(a_count, win.capital)
    mdd = np.zeros(a_count)
    paid = np.zeros(a_count)
    review = rule.review_days if rule else 0
    n_reviews = (h - 1) // review if rule else 0
    triggered = np.zeros((a_count, n_reviews), dtype=bool)
    pending = np.zeros(a_count, dtype=bool)
    target = np.zeros((a_count, k))
    rows = np.arange(a_count)
    for t in range(1, h + 1):
        if pending.any():
            px, lot_value = win.open[:, t], costs.lot * win.open_raw[:, t]
            nav_open = cash + (shares * px).sum(axis=1)
            held = shares * px
            delta = np.where(pending[:, None], target * nav_open[:, None] - held, 0.0)
            sell_ok = (delta < 0) & win.can_sell[:, t]
            lots = np.round(-delta / lot_value)
            sell = np.where(sell_ok, np.minimum(np.where(target <= 1e-12, held, lots * lot_value), held), 0.0)
            fee = np.where(sell > 0, np.maximum(costs.min_commission, costs.commission * sell)
                           + (costs.stamp_sell + costs.slippage) * sell, 0.0)
            shares -= sell / px
            cash += sell.sum(axis=1) - fee.sum(axis=1)
            paid += fee.sum(axis=1)
            want = np.where((delta > 0) & win.can_buy[:, t], delta, 0.0)
            n_buy = (want > 0).sum(axis=1)
            need = want.sum(axis=1) * (1 + costs.commission + costs.slippage) + costs.min_commission * n_buy
            scale = np.where(need > 0, np.minimum(1.0, np.maximum(cash, 0) / np.where(need > 0, need, 1)), 0.0)
            buy = np.floor(want * scale[:, None] / lot_value) * lot_value
            fee = np.where(buy > 0, np.maximum(costs.min_commission, costs.commission * buy)
                           + costs.slippage * buy, 0.0)
            shares += buy / px
            cash -= buy.sum(axis=1) + fee.sum(axis=1)
            paid += fee.sum(axis=1)
            pending[:] = False
        nav = cash + (shares * win.close[:, t]).sum(axis=1)
        peak = np.maximum(peak, nav)
        mdd = np.maximum(mdd, 1 - nav / peak)
        if rule and t % review == 0 and t // review <= n_reviews:
            r = t // review - 1
            weights = shares * win.close[:, t] / nav[:, None]
            target = target_weights(rule, win.mask, win.vol[:, t])
            if forced is not None:
                fire = forced[:, r]
            else:
                gap = np.abs(weights - target).max(axis=1)
                gap = np.maximum(gap, np.abs(cash / nav - (1 - target.sum(axis=1))))
                fire = gap > rule.band if rule.band > 0 else np.ones(a_count, dtype=bool)
            triggered[rows, r] = fire
            pending = fire.copy()
    return {"wealth": nav / win.capital, "max_drawdown": mdd, "cost": paid / win.capital,
            "triggered": triggered, "rebalances": triggered.sum(axis=1)}


def matched_random(triggered: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Same number of rebalances per account as the rule, on uniformly random review days."""
    counts = triggered.sum(axis=1)
    ranks = np.argsort(np.argsort(rng.random(triggered.shape), axis=1), axis=1)
    return ranks < counts[:, None]


def certainty_equivalent(wealth: np.ndarray, gamma: float) -> float:
    w = np.maximum(wealth, 1e-6)
    if gamma == 1:
        return float(np.exp(np.mean(np.log(w))))
    return float(np.mean(w ** (1 - gamma)) ** (1 / (1 - gamma)))
