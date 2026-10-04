"""Current book-only style summary. This is deliberately not an rm1 risk snapshot."""

import math

import pandas as pd

from alphasieve.strategy.risk import STYLE


def _finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _raw(code, quote, closes, benchmark):
    series = closes.get(code, pd.Series(dtype=float)).dropna()
    market = closes.get(benchmark, pd.Series(dtype=float)).dropna()
    cap = _finite(quote.get("circulating_market_cap")) or _finite(quote.get("total_market_cap"))
    pb = _finite(quote.get("pb_ratio"))
    result = {name: None for name in STYLE}
    if cap and cap > 0:
        result["size_z"] = math.log(cap)
    if pb and pb > 0:
        result["value_z"] = math.log(1 / pb)
    if len(series) >= 2:
        returns = series.pct_change(fill_method=None).dropna().tail(60)
        if len(returns) >= 40 and returns.std(ddof=0) > 0:
            result["volatility_z"] = math.log(float(returns.std(ddof=0)) * math.sqrt(252))
        if len(series) >= 61 and series.iloc[-61] > 0 and series.iloc[-21] > 0:
            result["momentum_z"] = math.log(float(series.iloc[-21] / series.iloc[-61]))
        pair = pd.concat([returns, market.pct_change(fill_method=None).dropna()], axis=1).dropna().tail(60)
        if len(pair) >= 30 and pair.iloc[:, 1].var(ddof=0) > 0:
            result["beta_z"] = float(pair.iloc[:, 0].cov(pair.iloc[:, 1]) / pair.iloc[:, 1].var())
    # Book quotes have no traded amount; do not infer rm1 liquidity from market cap.
    return result


def exposure(settings, stock_codes, weights, quotes, closes, benchmark):
    """Raw book metrics against a benchmark proxy; missing dimensions stay null."""
    stock_weight = sum(weights.get(code, 0) for code in stock_codes)
    fields = {}
    raw = {code: _raw(code, quotes.get(code, {}), closes, benchmark) for code in stock_codes}
    index_raw = _raw(benchmark, quotes.get(benchmark, {}), closes, benchmark)
    # A broad index has beta one by construction; its own observed volatility is valid.
    index_raw["beta_z"] = 1.0 if len(closes.get(benchmark, [])) >= 31 else None
    for name in STYLE:
        valid = [(weights[code], raw[code][name]) for code in stock_codes if raw[code][name] is not None]
        covered = sum(weight for weight, _ in valid)
        portfolio = sum(weight * value for weight, value in valid) / covered if covered else None
        reference = index_raw[name] if name in {"beta_z", "volatility_z"} else None
        fields[name] = {"portfolio": portfolio, "benchmark": reference,
                        "active": portfolio - reference if portfolio is not None and reference is not None else None,
                        "covered_portfolio_weight": covered,
                        "missing_stock_weight": max(0.0, stock_weight - covered)}
    return {"model": "book_simplified_not_rm1", "benchmark": benchmark,
            "stock_weight": stock_weight, "factors": fields,
            "method": "Current book quotes and up to 110 calendar days of book prices; "
                      "raw log cap, paired 60-day beta, "
                      "40-day minimum annualised volatility, 60-to-20-day price momentum, log inverse current PB. "
                      "Benchmark beta=1 and observed index volatility; other benchmark styles unavailable. "
                      "No rm1 cross-sectional z scores, 252-day momentum, traded amount, or historical PB."}
