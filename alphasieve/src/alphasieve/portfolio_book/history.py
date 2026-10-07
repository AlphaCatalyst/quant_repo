"""Daily personal-book reconstruction from dated, append-only broker snapshots."""

import json
import math
import sqlite3
from bisect import bisect_left
from collections import defaultdict
from datetime import date

import pandas as pd

from alphasieve.config import Settings
from alphasieve.errors import validation_error
from alphasieve.portfolio_book import market
from alphasieve.portfolio_book.cashflow import list_cashflows


def _snapshots(conn: sqlite3.Connection, account: str | None = None) -> tuple[list[dict], str]:
    rows = conn.execute(
        "SELECT * FROM holdings_snapshots"
        + (" WHERE account=?" if account else "")
        + " ORDER BY as_of, created_at, snapshot_id",
        (account,) if account else (),
    ).fetchall()
    if not rows:
        raise validation_error("no holdings snapshots")
    accounts = {row["account"] for row in rows}
    if len(accounts) != 1:
        raise validation_error("select one account", accounts=sorted(accounts))
    # Latest import on each date supersedes earlier versions without changing the append-only records.
    by_day = {row["as_of"]: {**dict(row), "positions": json.loads(row["positions_json"])} for row in rows}
    return list(by_day.values()), next(iter(accounts))


def build_history(
    conn: sqlite3.Connection,
    settings: Settings,
    start: str | None = None,
    end: str | None = None,
    account: str | None = None,
    benchmark: str = "sh.000300",
) -> dict:
    snapshots, account = _snapshots(conn, account)
    end = end or snapshots[-1]["as_of"]
    start = start or snapshots[0]["as_of"]
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise validation_error("start must not exceed end")
    active = [s for s in snapshots if s["as_of"] <= end]
    if not active:
        raise validation_error("no snapshot on or before end")
    first = active[0]["as_of"]
    codes = sorted({p["code"] for s in active for p in s["positions"] if p["code"] != "CASH"})
    prices, benchmark_meta = market.history_closes(settings, codes, benchmark, first, end)
    index = sorted(set(prices[benchmark].index) | {s["as_of"] for s in active})
    index = [d for d in index if first <= d <= end]
    if not index:
        raise validation_error("no valuation dates")
    price_table = pd.DataFrame({c: prices.get(c, pd.Series(dtype=float)) for c in codes}).reindex(index).ffill()
    for s in active:
        day = s["as_of"]
        for p in s["positions"]:
            if p["code"] == "CASH":
                continue
            if pd.isna(price_table.loc[day, p["code"]]):
                observed = p.get("price") or (
                    float(p["market_value"]) / float(p["quantity"]) if p["quantity"] else None
                )
                if observed:
                    price_table.loc[day, p["code"]] = float(observed)
    price_table = price_table.ffill()
    benchmark_prices = prices[benchmark].reindex(index).ffill()
    flows = defaultdict(float)
    for flow in list_cashflows(conn, account):
        if first < flow["as_of"] <= end:
            next_day = bisect_left(index, flow["as_of"])
            if next_day < len(index):
                flows[index[next_day]] += float(flow["amount"])
    pending_flows = 0.0
    current = {}
    cash = 0.0
    previous_nav = None
    previous_prices = {}
    nav_index = 1.0
    peak = 1.0
    rows = []
    trades = []
    snap_by_day = {s["as_of"]: s for s in active}
    for day in index:
        pending_flows += flows[day]
        prices_today = {
            c: (float(price_table.loc[day, c]) if pd.notna(price_table.loc[day, c]) else None) for c in codes
        }
        snap = snap_by_day.get(day)
        day_trades = []
        previous_quantities = current.copy()
        if snap:
            new = {p["code"]: float(p["quantity"]) for p in snap["positions"] if p["code"] != "CASH"}
            cash_row = next((p for p in snap["positions"] if p["code"] == "CASH"), None)
            if current:
                for code in sorted(set(current) | set(new)):
                    delta = new.get(code, 0.0) - current.get(code, 0.0)
                    if delta:
                        px = prices_today.get(code)
                        day_trades.append(
                            {
                                "date": day,
                                "code": code,
                                "quantity_delta": delta,
                                "estimated_value": delta * px if px is not None else None,
                                "inferred": True,
                            }
                        )
                        if cash_row is None and px is not None:
                            cash -= delta * px
            current = new
            if cash_row is not None:
                cash = float(cash_row["market_value"])
            elif pending_flows:
                cash += pending_flows
            trades.extend(day_trades)
        elif pending_flows:
            cash += pending_flows
        missing = [c for c, q in current.items() if q and prices_today.get(c) is None]
        if missing:
            # A missing quote must not silently become a zero-valued holding.
            rows.append(
                {
                    "date": day,
                    "nav": None,
                    "unit_nav": None,
                    "return": None,
                    "benchmark_return": None,
                    "excess_return": None,
                    "drawdown": None,
                    "missing_prices": missing,
                    "snapshot_id": snap["snapshot_id"] if snap else None,
                }
            )
            continue
        nav = cash + sum(q * prices_today[c] for c, q in current.items())
        if nav <= 0:
            raise validation_error("book NAV must be positive", date=day)
        flow = pending_flows if previous_nav is not None else 0.0
        daily_return = (nav - flow) / previous_nav - 1 if previous_nav else 0.0
        nav_index *= 1 + daily_return
        peak = max(peak, nav_index)
        benchmark_return = None
        if rows and pd.notna(benchmark_prices.loc[day]):
            prior = next((r.get("benchmark_close") for r in reversed(rows) if r.get("benchmark_close")), None)
            if prior:
                benchmark_return = float(benchmark_prices.loc[day]) / prior - 1
        contribution = {
            c: previous_quantities.get(c, 0) * (prices_today[c] - previous_prices[c]) / previous_nav
            for c in previous_quantities
            if previous_nav and c in previous_prices and prices_today[c] is not None
        }
        rows.append(
            {
                "date": day,
                "nav": nav,
                "unit_nav": nav_index,
                "return": daily_return,
                "benchmark_close": float(benchmark_prices.loc[day]) if pd.notna(benchmark_prices.loc[day]) else None,
                "benchmark_return": benchmark_return,
                "excess_return": daily_return - benchmark_return if benchmark_return is not None else None,
                "drawdown": nav_index / peak - 1,
                "cash_flow": flow,
                "snapshot_id": snap["snapshot_id"] if snap else None,
                "positions": {
                    c: {"quantity": q, "price": prices_today[c], "market_value": q * prices_today[c]}
                    for c, q in current.items()
                    if q
                },
                "cash": cash,
                "contribution": contribution,
                "missing_prices": [],
            }
        )
        previous_nav = nav
        previous_prices = {c: p for c, p in prices_today.items() if p is not None}
        pending_flows = 0.0
    visible = [r for r in rows if start <= r["date"] <= end]
    valid = [r for r in visible if r["unit_nav"] is not None]
    if not valid:
        raise validation_error("no priced book dates")
    daily = [r["return"] for r in valid[1:] if r["return"] is not None]
    vol = float(pd.Series(daily).std(ddof=1) * math.sqrt(252)) if len(daily) > 1 else None
    traded = sum(
        abs(t["estimated_value"]) for t in trades if t["estimated_value"] is not None and start <= t["date"] <= end
    )
    avg_nav = sum(r["nav"] for r in valid) / len(valid)
    book_return = valid[-1]["unit_nav"] / valid[0]["unit_nav"] - 1
    benchmark_return = (
        valid[-1]["benchmark_close"] / valid[0]["benchmark_close"] - 1
        if valid[-1]["benchmark_close"] and valid[0]["benchmark_close"]
        else None
    )
    return {
        "account": account,
        "start": start,
        "end": end,
        "benchmark": benchmark_meta,
        "rows": visible,
        "trades": [t for t in trades if start <= t["date"] <= end],
        "summary": {
            "start_nav": valid[0]["nav"],
            "end_nav": valid[-1]["nav"],
            "total_return": book_return,
            "benchmark_return": benchmark_return,
            "excess_return": book_return - benchmark_return if benchmark_return is not None else None,
            "max_drawdown": min(r["drawdown"] for r in valid),
            "volatility_annualized": vol,
            "turnover": traded / (2 * avg_nav) if avg_nav else None,
        },
        "method": "Daily close valuation; quantities held until next snapshot; "
        "changes inferred at later snapshot close. "
        "Time-weighted returns subtract recorded external cash flows at end-of-day. Unrecorded external "
        "flows cannot be distinguished from trades. Turnover uses half gross inferred trade value / average NAV.",
    }


def save_analysis_report(report: dict, settings: Settings, kind: str):
    """Save a latest read-only web projection under the private book tree."""
    import os
    from pathlib import Path
    from tempfile import NamedTemporaryFile

    if kind not in {"history", "attribution", "attribution-position", "attribution-industry",
                    "attribution-thesis", "rebalance", "behavior"}:
        raise validation_error("invalid book report kind")
    path = settings.hot_root / "book" / "reports" / f"{kind}-latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(report, stream, ensure_ascii=False, allow_nan=False)
        temporary = Path(stream.name)
    os.replace(temporary, path)
    return path


def load_analysis_report(settings: Settings, kind: str) -> dict | None:
    if kind not in {"history", "attribution", "attribution-position", "attribution-industry",
                    "attribution-thesis", "rebalance", "behavior"}:
        raise validation_error("invalid book report kind")
    path = settings.hot_root / "book" / "reports" / f"{kind}-latest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
