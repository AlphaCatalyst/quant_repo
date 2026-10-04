"""Deterministic v0 holdings check-up and simple shocks."""

import json
import math
import os
from itertools import combinations
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd

from alphasieve.config import Settings
from alphasieve.errors import validation_error
from alphasieve.portfolio_book import market


def _num(value) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _cap_bucket(cap: float | None) -> str:
    if cap is None or cap <= 0:
        return "未知"
    if cap < 5e9:
        return "<50亿"
    if cap < 2e10:
        return "50–200亿"
    if cap < 1e11:
        return "200–1000亿"
    return ">=1000亿"


def _bond_record(code: str, row: dict, underlying: dict) -> dict:
    price = _num(row.get("price"))
    equity_value = _num(row.get("bond_equity_value"))
    equity_premium = _num(row.get("bond_equity_premium"))
    if equity_premium is None and price is not None and equity_value and equity_value > 0:
        equity_premium = 100 * (price / equity_value - 1)
    remaining_wan = _num(row.get("bond_undue_size"))
    remaining_yi = remaining_wan / 10000 if remaining_wan is not None else None
    term = _num(row.get("bond_undue_term"))
    stock_price = _num(underlying.get("price"))
    redeem = _num(row.get("bond_redeem_price_trigger"))
    buyback = _num(row.get("bond_buyback_price_trigger"))
    rating = str(row.get("bond_rating") or "").strip() or None
    flags = []
    if equity_premium is not None and equity_premium > 50:
        flags.append("high_conversion_premium")
    if remaining_yi is not None and remaining_yi < 3:
        flags.append("small_remaining_size")
    if term is not None and term < 1:
        flags.append("short_maturity")
    if rating and rating.upper() not in {"AAA", "AA+", "AA", "AA-"}:
        flags.append("low_rating")
    return {
        "code": code, "price": price, "conversion_premium_pct": equity_premium,
        "pure_bond_premium_pct": _num(row.get("bond_pure_premium")),
        "double_low": _num(row.get("bond_double_low")), "ytm_pct": _num(row.get("bond_ytm")),
        "rating": rating, "remaining_size_yi": remaining_yi, "remaining_term_years": term,
        "due_date": row.get("bond_due_date"), "underlying_code": underlying.get("code"),
        "underlying_price": stock_price, "redeem_trigger": redeem, "buyback_trigger": buyback,
        "redeem_distance_pct": (100 * (stock_price / redeem - 1)
                                if stock_price is not None and redeem and redeem > 0 else None),
        "buyback_distance_pct": (100 * (stock_price / buyback - 1)
                                 if stock_price is not None and buyback and buyback > 0 else None),
        "flags": flags,
    }


def _display(value: float | None, unit: str = "", digits: int = 2) -> str:
    return f"{value:.{digits}f}{unit}" if value is not None else "缺失"


CLASS_LABELS = {"stock": "股票", "etf": "ETF", "convertible_bond": "可转债", "cash": "现金", "other": "其他"}
BOND_FLAG_LABELS = {"high_conversion_premium": "转股溢价率>50%", "small_remaining_size": "剩余规模<3亿元",
                    "short_maturity": "剩余期限<1年", "low_rating": "评级低于AA-"}


def _report_path(settings: Settings, snapshot_id: str) -> Path:
    if not snapshot_id or snapshot_id in {".", ".."} or Path(snapshot_id).name != snapshot_id:
        raise validation_error("invalid snapshot id")
    return settings.hot_root / "book" / "reports" / f"{snapshot_id}.json"


def save_report(report: dict, settings: Settings) -> Path:
    """Persist a completed check-up for read-only web views."""
    path = _report_path(settings, report["snapshot_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(report, tmp, ensure_ascii=False, allow_nan=False)
        temporary_path = Path(tmp.name)
    os.replace(temporary_path, path)
    return path


def load_report(snapshot_id: str, settings: Settings) -> dict | None:
    """Read a saved check-up without recalculating market data."""
    path = _report_path(settings, snapshot_id)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def build_report(snapshot: dict, settings: Settings, benchmark: str = "sh.000300", top_n: int = 5) -> tuple[dict, str]:
    positions = snapshot["positions"]
    codes = [position["code"] for position in positions if position["code"] != "CASH"]
    values = {position["code"]: float(position["market_value"]) for position in positions}
    total = sum(values.values())
    if total <= 0:
        raise validation_error("holdings total market value must be positive")
    weights = {code: value / total for code, value in values.items()}
    quotes = market.current_quotes(settings, codes)
    classes = {position["code"]: market.asset_class(position["code"], quotes.get(position["code"]))
               for position in positions}
    stock_codes = [code for code in codes if classes[code] == "stock"]
    industry, industry_meta = market.current_industry(settings, stock_codes)
    industry_weights = {}
    asset_class_weights = {}
    market_cap_buckets = {}
    holdings = []
    for position in positions:
        code = position["code"]
        kind = classes[code]
        asset_class_weights[kind] = asset_class_weights.get(kind, 0.0) + weights[code]
        row = quotes.get(code, {})
        cap = _num(row.get("circulating_market_cap")) or _num(row.get("total_market_cap"))
        bucket = _cap_bucket(cap) if kind == "stock" else None
        if bucket:
            market_cap_buckets[bucket] = market_cap_buckets.get(bucket, 0.0) + weights[code]
        low, high, price = (_num(row.get(field)) for field in ("low_52week", "high_52week", "price"))
        position_52week = (price - low) / (high - low) if None not in (low, high, price) and high > low else None
        holdings.append({"code": code, "name": position["name"], "asset_class": kind,
                         "weight": weights[code], "industry": industry.get(code) if kind == "stock" else None,
                         "market_cap_bucket": bucket,
                         "quote": {"price": price, "time": row.get("time"),
                                   "circulating_market_cap": _num(row.get("circulating_market_cap")),
                                   "total_market_cap": _num(row.get("total_market_cap")),
                                   "pe_ratio": _num(row.get("pe_ratio")), "pb_ratio": _num(row.get("pb_ratio")),
                                   "high_52week": high, "low_52week": low,
                                   "position_52week": position_52week} if row else None})
    for code in codes:
        if classes[code] == "stock":
            label = industry.get(code, "未知")
            industry_weights[label] = industry_weights.get(label, 0.0) + weights[code]
    underlying_codes = []
    for code in codes:
        if classes[code] == "convertible_bond":
            stock_code = str(quotes.get(code, {}).get("bond_stock_code") or "")
            if len(stock_code) == 6 and stock_code.isdigit():
                underlying_codes.append((code, ("sh." if stock_code.startswith(("6", "9")) else "sz.") + stock_code))
    underlying_quotes = (market.current_quotes(settings, [item[1] for item in underlying_codes])
                         if underlying_codes else {})
    convertible_bonds = []
    for code in codes:
        if classes[code] == "convertible_bond":
            linked = next((stock for bond, stock in underlying_codes if bond == code), None)
            underlying = {"code": linked, **underlying_quotes.get(linked, {})} if linked else {}
            convertible_bonds.append(_bond_record(code, quotes.get(code, {}), underlying))
    closes = market.recent_closes(settings, codes, benchmark, asset_classes=classes)
    returns = {code: series.pct_change(fill_method=None).dropna().tail(60) for code, series in closes.items()}
    benchmark_returns = returns[benchmark]
    beta = {}
    overlap_days = {}
    for code in codes:
        pair = pd.concat([returns[code], benchmark_returns], axis=1, join="inner").dropna().tail(60)
        overlap_days[code] = len(pair)
        variance = pair.iloc[:, 1].var() if len(pair) >= 20 else None
        beta[code] = float(pair.iloc[:, 0].cov(pair.iloc[:, 1]) / variance) if variance and variance > 0 else None
    portfolio_beta = sum(weights[code] * beta[code] for code in codes if beta[code] is not None)
    if not any(beta[code] is not None for code in codes):
        portfolio_beta = None
    correlations = []
    for left, right in combinations(codes, 2):
        pair = pd.concat([returns[left], returns[right]], axis=1, join="inner").dropna().tail(60)
        if len(pair) >= 20 and pair.iloc[:, 0].std() > 0 and pair.iloc[:, 1].std() > 0:
            correlations.append({"codes": [left, right], "correlation": float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))})
    largest = max(codes, key=lambda code: weights[code]) if codes else None
    stress = {"benchmark_down_10pct": -0.1 * portfolio_beta if portfolio_beta is not None else None,
              "industry_down_20pct": {name: -0.2 * weight for name, weight in industry_weights.items()},
              "largest_position_down_30pct": -0.3 * weights[largest] if largest else None}
    report = {
        "snapshot_id": snapshot["snapshot_id"], "account": snapshot["account"], "as_of": snapshot["as_of"],
        "valuation_basis": "broker export market values", "total_value": total, "weights": weights,
        "top_n": top_n, "top_n_concentration": sum(sorted((weights[code] for code in codes), reverse=True)[:top_n]),
        "hhi": sum(weight * weight for weight in weights.values()),
        "holdings": holdings, "asset_class_weights": asset_class_weights,
        "industry_weights": industry_weights, "industry_snapshot": industry_meta,
        "market_cap_buckets": market_cap_buckets,
        "market_cap_note": "流通市值优先，总市值回退；仅股票；当前快照，非 PIT；单位人民币元",
        "convertible_bonds": convertible_bonds,
        "beta_60d": beta, "beta_overlap_days": overlap_days, "portfolio_beta_60d": portfolio_beta,
        "benchmark": benchmark, "pairwise_correlation": {
            "mean": sum(item["correlation"] for item in correlations) / len(correlations) if correlations else None,
            "max_pair": max(correlations, key=lambda item: item["correlation"]) if correlations else None,
            "pair_count": len(correlations)}, "stress_returns": stress,
        "stress_note": "线性单因子冲击估算；未计入流动性、非线性或协方差影响",
    }
    markdown = (f"# 持仓体检：{snapshot['account']}（{snapshot['as_of']}）\n\n"
                f"总市值：{total:,.2f} 元；前 {top_n} 个证券集中度：{report['top_n_concentration']:.1%}；"
                f"HHI：{report['hhi']:.4f}。\n\n"
                f"组合 60 日 beta：{portfolio_beta:.3f}。\n" if portfolio_beta is not None else
                f"# 持仓体检：{snapshot['account']}（{snapshot['as_of']}）\n\n"
                f"总市值：{total:,.2f} 元；60 日 beta 数据不足。\n")
    markdown += (f"基准下跌 10% 情景：{stress['benchmark_down_10pct']:.1%}。\n" if portfolio_beta is not None
                 else "基准情景：数据不足。\n")
    markdown += (f"行业分类：{industry_meta['label']}；"
                 "市值分档：流通市值优先，<50亿、50–200亿、200–1000亿、≥1000亿。\n\n")
    markdown += ("资产类别：" + "；".join(f"{CLASS_LABELS[kind]} {weight:.1%}"
                                           for kind, weight in asset_class_weights.items())
                 + "。\n")
    if convertible_bonds:
        markdown += "\n## 可转债（当前行情快照）\n\n"
        for bond in convertible_bonds:
            flag_text = "、".join(BOND_FLAG_LABELS[flag] for flag in bond["flags"]) if bond["flags"] else "无"
            markdown += (f"- {bond['code']}：转股溢价率 {_display(bond['conversion_premium_pct'], '%')}；"
                         f"纯债溢价率 {_display(bond['pure_bond_premium_pct'], '%')}；"
                         f"双低 {_display(bond['double_low'])}；到期收益率 {_display(bond['ytm_pct'], '%')}；"
                         f"评级 {bond['rating'] or '缺失'}；剩余规模 {_display(bond['remaining_size_yi'], ' 亿元')}；"
                         f"剩余期限 {_display(bond['remaining_term_years'], ' 年')}；"
                         f"正股距强赎触发价 {_display(bond['redeem_distance_pct'], '%')}；"
                         f"正股距回售触发价 {_display(bond['buyback_distance_pct'], '%')}；"
                         f"标记 {flag_text}。\n")
    return report, markdown
