"""Derive dated holdings snapshots from a broker trade log, anchored on the latest broker export."""

import hashlib
import json
import sqlite3
import uuid
from collections import defaultdict
from pathlib import Path

import pandas as pd

from alphasieve.config import Settings
from alphasieve.errors import validation_error
from alphasieve.portfolio_book import market
from alphasieve.portfolio_book.importer import _number, _read_export, normalize_code
from alphasieve.util import canonical_json, utcnow_iso

SOURCE = "derived_trades"
BUY, SELL, SET, DIVIDEND = "买入", "卖出", "修改持仓", "派息"


def parse_trades(path: Path) -> list[dict]:
    """投资账本 trade export: account,date,code,name,type,price,quantity,amount,fee,note."""
    frame = _read_export(path)
    missing = {"date", "code", "type", "quantity", "amount"} - set(frame.columns)
    if missing:
        raise validation_error("trade log is missing columns", columns=sorted(missing))
    trades = []
    for _, row in frame.iterrows():
        kind = str(row["type"]).strip()
        if kind not in (BUY, SELL, SET, DIVIDEND):
            raise validation_error("unsupported trade type", type=kind, date=str(row["date"]))
        trades.append({
            "date": str(row["date"]).strip(), "code": normalize_code(row["code"]),
            "name": str(row.get("name") or "").strip(), "type": kind,
            "price": _number(row.get("price"), "price"),
            "quantity": _number(row["quantity"], "quantity") or 0.0,
            "amount": _number(row["amount"], "amount") or 0.0,
            "fee": _number(row.get("fee"), "fee") or 0.0,
        })
    return sorted(trades, key=lambda t: t["date"])


def _replay(trades: list[dict]) -> tuple[list[tuple[str, dict, float]], dict]:
    """End-of-day quantities and net trade cash per date, starting from zero cash."""
    quantities: dict[str, float] = defaultdict(float)
    cash, days = 0.0, []
    names = {}
    by_day: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_day[t["date"]].append(t)
        names[t["code"]] = t["name"]
    for day in sorted(by_day):
        for t in by_day[day]:
            if t["type"] == SET:
                quantities[t["code"]] = t["quantity"]
            elif t["type"] == BUY:
                quantities[t["code"]] += t["quantity"]
                cash -= t["amount"] + t["fee"]
            elif t["type"] == SELL:
                quantities[t["code"]] -= t["quantity"]
                cash += t["amount"] - t["fee"]
            else:
                cash += t["amount"]
            if quantities[t["code"]] < 0:
                raise validation_error("trade log sells more than held", code=t["code"], date=day)
        days.append((day, {c: q for c, q in quantities.items() if q}, cash))
    return days, names


def plan(conn: sqlite3.Connection, settings: Settings, account: str, trades: list[dict]) -> dict:
    anchor = conn.execute("SELECT * FROM holdings_snapshots WHERE account=? AND source='broker_export' "
                          "ORDER BY as_of DESC, created_at DESC LIMIT 1", (account,)).fetchone()
    if anchor is None:
        raise validation_error("reconstruction needs a broker export to anchor on", account=account)
    anchor_positions = json.loads(anchor["positions_json"])
    days, names = _replay([t for t in trades if t["date"] <= anchor["as_of"]])
    if not days:
        raise validation_error("no trades on or before the anchor snapshot", as_of=anchor["as_of"])
    held = {p["code"]: float(p["quantity"]) for p in anchor_positions if p["code"] != "CASH"}
    final = days[-1][1]
    mismatched = sorted(c for c in set(held) | set(final) if abs(held.get(c, 0.0) - final.get(c, 0.0)) > 1e-6)
    if mismatched:
        raise validation_error("trade log does not reproduce the anchor snapshot", codes=mismatched)
    anchor_cash = next((float(p["market_value"]) for p in anchor_positions if p["code"] == "CASH"), None)
    offset = anchor_cash - days[-1][2] if anchor_cash is not None else -min(0.0, min(c for _, _, c in days))
    existing = {r["as_of"] for r in conn.execute("SELECT as_of FROM holdings_snapshots WHERE account=?", (account,))}
    codes = sorted({c for _, q, _ in days for c in q})
    prices, _ = market.history_closes(settings, codes, "sh.000300", days[0][0], anchor["as_of"])
    snapshots, warnings = [], []
    for day, quantities, cash in days:
        if day in existing:
            continue
        positions = []
        for code in sorted(quantities):
            series = prices.get(code, pd.Series(dtype=float))
            close = series[series.index <= day]
            if close.empty:
                raise validation_error("no close for derived snapshot", code=code, date=day)
            price = float(close.iloc[-1])
            positions.append({"code": code, "name": names.get(code, ""), "quantity": quantities[code],
                              "cost_price": None, "price": price, "market_value": quantities[code] * price})
        derived_cash = round(cash + offset, 2) + 0.0
        if derived_cash < 0:
            warnings.append(f"{day}: derived cash {derived_cash:.2f} is negative; an unrecorded deposit or "
                            "cash balance not in the export is likely")
        positions.append({"code": "CASH", "name": "现金", "quantity": derived_cash, "cost_price": None,
                          "price": 1.0, "market_value": derived_cash})
        snapshots.append({"as_of": day, "positions": positions})
    return {"account": account, "anchor": anchor["snapshot_id"], "anchor_as_of": anchor["as_of"],
            "snapshots": snapshots, "warnings": warnings,
            "method": "Replays the trade log from zero cash, checks the end state against the latest broker "
                      "export, and shifts cash by the export's cash balance. Prices are daily closes."}


def write(conn: sqlite3.Connection, result: dict, actor: str) -> list[str]:
    ids = []
    for snap in result["snapshots"]:
        snapshot_id = uuid.uuid4().hex
        body = canonical_json(snap["positions"])
        conn.execute("INSERT INTO holdings_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (snapshot_id, result["account"], snap["as_of"], SOURCE, body,
                      hashlib.sha256(body.encode("utf-8")).hexdigest(), actor, utcnow_iso()))
        ids.append(snapshot_id)
    conn.commit()
    return ids
