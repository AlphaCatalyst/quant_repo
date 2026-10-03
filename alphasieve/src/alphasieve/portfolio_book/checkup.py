"""Deterministic v0 holdings check-up and simple shocks."""

import json
import os
from itertools import combinations
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd

from alphasieve.config import Settings
from alphasieve.errors import validation_error
from alphasieve.portfolio_book import market


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
    equities = [position for position in positions if position["code"] != "CASH"]
    codes = [position["code"] for position in equities]
    values = {position["code"]: float(position["market_value"]) for position in positions}
    total = sum(values.values())
    if total <= 0:
        raise validation_error("holdings total market value must be positive")
    weights = {code: value / total for code, value in values.items()}
    industry, industry_meta = market.current_industry(settings, codes)
    industry_weights = {}
    for code in codes:
        label = industry.get(code, "unknown")
        industry_weights[label] = industry_weights.get(label, 0.0) + weights[code]
    closes = market.recent_closes(settings, codes, benchmark)
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
        "industry_weights": industry_weights, "industry_snapshot": industry_meta,
        "market_cap_buckets": None, "market_cap_note": "unavailable from current cached industry and close data",
        "beta_60d": beta, "beta_overlap_days": overlap_days, "portfolio_beta_60d": portfolio_beta,
        "benchmark": benchmark, "pairwise_correlation": {
            "mean": sum(item["correlation"] for item in correlations) / len(correlations) if correlations else None,
            "max_pair": max(correlations, key=lambda item: item["correlation"]) if correlations else None,
            "pair_count": len(correlations)}, "stress_returns": stress,
        "stress_note": "linear one-factor shocks; no liquidity, nonlinear or covariance effects",
    }
    markdown = (f"# 持仓体检：{snapshot['account']}（{snapshot['as_of']}）\n\n"
                f"总市值：{total:,.2f} 元；前 {top_n} 只股票集中度：{report['top_n_concentration']:.1%}；"
                f"HHI：{report['hhi']:.4f}。\n\n"
                f"组合 60 日 beta：{portfolio_beta:.3f}。\n" if portfolio_beta is not None else
                f"# 持仓体检：{snapshot['account']}（{snapshot['as_of']}）\n\n"
                f"总市值：{total:,.2f} 元；60 日 beta 数据不足。\n")
    markdown += (f"基准下跌 10% 情景：{stress['benchmark_down_10pct']:.1%}。\n" if portfolio_beta is not None
                 else "基准情景：数据不足。\n")
    markdown += f"行业分类：{industry_meta['label']}；市值分布：暂无可用数据。"
    return report, markdown
