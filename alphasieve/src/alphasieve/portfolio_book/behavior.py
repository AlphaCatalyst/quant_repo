"""Behaviour statistics of a personal trade log (P5): disposition, repurchase, chasing, switch value, costs."""

from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd

from alphasieve.config import Settings
from alphasieve.portfolio_book import market
from alphasieve.portfolio_book.reconstruct import BUY, DIVIDEND, SELL, SET

MIN_DECISIONS = 30
HORIZON = 20


def _after(series: pd.Series, day: str, steps: int) -> float | None:
    later = series[series.index > day]
    return float(later.iloc[steps - 1]) if len(later) >= steps else None


def _before(series: pd.Series, day: str, steps: int) -> float | None:
    earlier = series[series.index < day]
    return float(earlier.iloc[-steps]) if len(earlier) >= steps else None


def _bootstrap_ci(values: list[float], seed: int = 0, draws: int = 2000) -> list[float] | None:
    if len(values) < 2:
        return None
    rng = np.random.default_rng(seed)
    arr = np.asarray(values)
    means = arr[rng.integers(0, len(arr), (draws, len(arr)))].mean(axis=1)
    return [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))]


def build_behavior(settings: Settings, trades: list[dict], benchmark: str = "sh.000300") -> dict:
    trades = sorted(trades, key=lambda t: t["date"])
    codes = sorted({t["code"] for t in trades})
    start = (date.fromisoformat(trades[0]["date"]) - timedelta(days=45)).isoformat()
    end = min(date.today(), date.fromisoformat(trades[-1]["date"]) + timedelta(days=45)).isoformat()
    prices, benchmark_meta = market.history_closes(settings, codes, benchmark, start, end)
    bench = prices[benchmark]

    held: dict[str, float] = defaultdict(float)
    cost: dict[str, float] = {}
    fees = gross = 0.0
    realized_gain = realized_loss = paper_gain = paper_loss = 0
    sells, buys, decisions = [], [], []
    by_day: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_day[t["date"]].append(t)
    for day in sorted(by_day):
        day_trades = by_day[day]
        sold_today = {t["code"] for t in day_trades if t["type"] == SELL}
        if sold_today:
            # Odean (1998): on each day with a sale, classify every position held that morning.
            for code, quantity in held.items():
                if quantity <= 0 or code not in cost:
                    continue
                series = prices.get(code, pd.Series(dtype=float))
                sale = next((t for t in day_trades if t["type"] == SELL and t["code"] == code), None)
                px = sale["amount"] / sale["quantity"] if sale else (
                    float(series[series.index <= day].iloc[-1]) if (series.index <= day).any() else None)
                if px is None:
                    continue
                gain = px > cost[code]
                if sale:
                    realized_gain += gain
                    realized_loss += not gain
                else:
                    paper_gain += gain
                    paper_loss += not gain
        for t in day_trades:
            code = t["code"]
            fees += t["fee"]
            if t["type"] == SET:
                held[code] = t["quantity"]
                cost[code] = t["price"] or 0.0
                if not cost[code]:
                    series = prices.get(code, pd.Series(dtype=float))
                    prior = series[series.index <= day]
                    cost[code] = float(prior.iloc[-1]) if len(prior) else 0.0
            elif t["type"] == BUY:
                px = t["amount"] / t["quantity"]
                total = held[code] + t["quantity"]
                cost[code] = (cost.get(code, 0.0) * held[code] + px * t["quantity"]) / total
                held[code] = total
                gross += t["amount"]
                buys.append({"date": day, "code": code, "price": px})
            elif t["type"] == SELL:
                held[code] -= t["quantity"]
                gross += t["amount"]
                sells.append({"date": day, "code": code, "price": t["amount"] / t["quantity"],
                              "amount": t["amount"]})
            elif t["type"] != DIVIDEND:
                raise ValueError(t["type"])

    def forward(code: str, day: str, px: float) -> float | None:
        later = _after(prices.get(code, pd.Series(dtype=float)), day, HORIZON)
        return later / px - 1 if later is not None else None

    repurchased = 0
    for s in sells:
        horizon_end = _after(bench, s["date"], HORIZON)
        window = [b for b in buys if b["code"] == s["code"] and s["date"] < b["date"]]
        if horizon_end is not None:
            last_day = bench[bench.index > s["date"]].index[HORIZON - 1]
            window = [b for b in window if b["date"] <= last_day]
        repurchased += bool(window)
        # Switch value: what was bought the same day against keeping what was sold, h days later.
        bought = [b for b in buys if b["date"] == s["date"] and b["code"] != s["code"]]
        sold_fwd = forward(s["code"], s["date"], s["price"])
        if bought and sold_fwd is not None:
            bought_fwd = [forward(b["code"], b["date"], b["price"]) for b in bought]
            if all(v is not None for v in bought_fwd):
                decisions.append({"date": s["date"], "sold": s["code"], "bought": [b["code"] for b in bought],
                                  "value": float(np.mean(bought_fwd)) - sold_fwd})
    chase = []
    for b in buys:
        series = prices.get(b["code"], pd.Series(dtype=float))
        past, past_bench = _before(series, b["date"], HORIZON), _before(bench, b["date"], HORIZON)
        now_bench = bench[bench.index < b["date"]]
        if past and past_bench and len(now_bench):
            now = series[series.index < b["date"]]
            chase.append(float(now.iloc[-1]) / past - float(now_bench.iloc[-1]) / past_bench)

    pgr = realized_gain / (realized_gain + paper_gain) if realized_gain + paper_gain else None
    plr = realized_loss / (realized_loss + paper_loss) if realized_loss + paper_loss else None
    values = [d["value"] for d in decisions]
    enough = len(values) >= MIN_DECISIONS
    ci = _bootstrap_ci(values)
    verdict = None
    if enough and ci:
        verdict = "positive" if ci[0] > 0 else "negative" if ci[1] < 0 else "inconclusive"
    return {
        "trades": len(trades), "buys": len(buys), "sells": len(sells), "benchmark": benchmark_meta,
        "disposition": {"pgr": pgr, "plr": plr, "pgr_minus_plr": pgr - plr if pgr is not None and plr is not None
                        else None, "counts": {"realized_gain": realized_gain, "realized_loss": realized_loss,
                                              "paper_gain": paper_gain, "paper_loss": paper_loss}},
        "repurchase_within_h": {"count": repurchased, "share": repurchased / len(sells) if sells else None},
        "chasing": {"mean_prior_excess_h": float(np.mean(chase)) if chase else None, "n": len(chase)},
        "switch_value_h": {"decisions": decisions, "n": len(values),
                           "mean": float(np.mean(values)) if values else None, "bootstrap_ci95": ci,
                           "verdict": verdict},
        "costs": {"fees": fees, "gross_traded": gross, "fee_rate": fees / gross if gross else None},
        "horizon_days": HORIZON,
        "method": f"Disposition per Odean (1998) on sale days with average-cost basis; repurchase = same code bought "
                  f"within {HORIZON} trading days after a sale; chasing = bought name's {HORIZON}-day return minus the "
                  f"benchmark's before the buy; switch value = mean {HORIZON}-day return of names bought on a sale "
                  f"day minus the sold name's, from trade prices. A verdict needs at least {MIN_DECISIONS} decisions "
                  "and a bootstrap interval excluding zero; below that only numbers are reported.",
    }
