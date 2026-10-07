"""PD-7 virtual accounts: a dev-accepted decision rule run forward on a copy of a real account."""

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

from alphasieve.config import Settings
from alphasieve.decisions.simulate import Costs, Rule, target_weights
from alphasieve.errors import validation_error
from alphasieve.portfolio_book import market
from alphasieve.util import canonical_json, utcnow_iso

SOURCE = "virtual"
PREFIX = "虚拟·"


def _registry(settings: Settings) -> Path:
    return settings.hot_root / "book" / "virtual" / "accounts.json"


def load_registry(settings: Settings) -> dict:
    path = _registry(settings)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _save_registry(settings: Settings, registry: dict) -> None:
    path = _registry(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def accepted_rule(settings: Settings, rule_id: str) -> dict:
    path = settings.hot_root / "reports" / "decisions" / "p3-latest.json"
    if not path.is_file():
        raise validation_error("no recorded P3 run; virtual accounts need a dev-accepted rule")
    report = json.loads(path.read_text(encoding="utf-8"))
    for pool in report["pools"]:
        for row in pool["rules"]:
            if row["rule_id"] == rule_id and row["acceptance"]["pass"]:
                return {**row["rule"], "pool": pool["pool"], "run_id": report["run_id"]}
    raise validation_error("rule did not pass P3 dev acceptance in any pool", rule_id=rule_id,
                           run_id=report["run_id"])


def _insert(conn: sqlite3.Connection, account: str, as_of: str, positions: list[dict], actor: str) -> str:
    body = canonical_json(positions)
    snapshot_id = uuid.uuid4().hex
    conn.execute("INSERT INTO holdings_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (snapshot_id, account, as_of, SOURCE, body, hashlib.sha256(body.encode()).hexdigest(), actor,
                  utcnow_iso()))
    return snapshot_id


def create(conn: sqlite3.Connection, settings: Settings, base_account: str, rule_id: str, actor: str) -> dict:
    rule = accepted_rule(settings, rule_id)
    base = conn.execute("SELECT * FROM holdings_snapshots WHERE account=? AND source != ? "
                        "ORDER BY as_of DESC, created_at DESC LIMIT 1", (base_account, SOURCE)).fetchone()
    if base is None:
        raise validation_error("base account has no snapshot", account=base_account)
    account = f"{PREFIX}{base_account}·{rule_id}"
    registry = load_registry(settings)
    if account in registry:
        raise validation_error("virtual account exists", account=account)
    positions = json.loads(base["positions_json"])
    snapshot_id = _insert(conn, account, base["as_of"], positions, actor)
    registry[account] = {"base_account": base_account, "base_snapshot": base["snapshot_id"], "start": base["as_of"],
                         "rule": rule, "created_by": actor, "created_at": utcnow_iso()}
    _save_registry(settings, registry)
    return {"account": account, "start": base["as_of"], "snapshot_id": snapshot_id, "rule": rule}


def advance(conn: sqlite3.Connection, settings: Settings, account: str, end: str, actor: str,
            costs: Costs = Costs()) -> dict:
    """Review every ``review_days`` trading days from the start; trades fill at the next close in whole lots."""
    entry = load_registry(settings).get(account)
    if entry is None:
        raise validation_error("unknown virtual account", account=account)
    spec = {k: entry["rule"][k] for k in ("target", "cap", "band", "review_days")}
    rule = Rule(**spec)
    last = conn.execute("SELECT * FROM holdings_snapshots WHERE account=? ORDER BY as_of DESC, created_at DESC "
                        "LIMIT 1", (account,)).fetchone()
    if last["as_of"] >= end:
        return {"account": account, "snapshots": [], "as_of": last["as_of"]}
    positions = json.loads(last["positions_json"])
    holdings = {p["code"]: float(p["quantity"]) for p in positions if p["code"] != "CASH"}
    names = {p["code"]: p.get("name", "") for p in positions}
    cash = sum(float(p["market_value"]) for p in positions if p["code"] == "CASH")
    codes = sorted(holdings)
    history_start = (pd.Timestamp(entry["start"]) - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
    prices, _ = market.history_closes(settings, codes, "sh.000300", history_start, end)
    table = pd.DataFrame({c: prices.get(c, pd.Series(dtype=float)) for c in codes}).sort_index().ffill()
    calendar = [d for d in prices["sh.000300"].index if entry["start"] <= d <= end]
    days = [d for d in calendar if d > last["as_of"]]
    written, pending = [], None
    mask = np.ones((1, len(codes)), dtype=bool)
    for day in days:
        px = table.loc[day].to_numpy(dtype=float)
        qty = np.array([holdings[c] for c in codes])
        if pending is not None:
            nav = cash + float((qty * px).sum())
            delta = pending * nav - qty * px
            lot = costs.lot * px
            sell = np.where(delta < 0, np.minimum(np.round(-delta / lot) * lot, qty * px), 0.0)
            cash += sell.sum() - sum(max(costs.min_commission, costs.commission * v)
                                     + (costs.stamp_sell + costs.slippage) * v for v in sell if v > 0)
            qty = qty - sell / px
            want = np.where(delta > 0, delta, 0.0)
            need = want.sum() * (1 + costs.commission + costs.slippage) + costs.min_commission * (want > 0).sum()
            buy = np.floor(want * (min(1.0, max(cash, 0) / need) if need else 0) / lot) * lot
            cash -= buy.sum() + sum(max(costs.min_commission, costs.commission * v) + costs.slippage * v
                                    for v in buy if v > 0)
            qty = qty + buy / px
            holdings = dict(zip(codes, qty.tolist(), strict=True))
            pending = None
            snapshot = [{"code": c, "name": names.get(c, ""), "quantity": holdings[c], "cost_price": None,
                         "price": float(px[i]), "market_value": holdings[c] * float(px[i])}
                        for i, c in enumerate(codes) if holdings[c]]
            snapshot.append({"code": "CASH", "name": "现金", "quantity": round(cash, 2), "cost_price": None,
                             "price": 1.0, "market_value": round(cash, 2)})
            written.append(_insert(conn, account, day, snapshot, actor))
        index = calendar.index(day)
        if index and index % rule.review_days == 0:
            nav = cash + float((qty * px).sum())
            returns = np.log(table.loc[:day].tail(61)).diff().std().to_numpy(dtype=float)
            target = target_weights(rule, mask, returns[None, :])[0]
            gap = max(np.abs(qty * px / nav - target).max(), abs(cash / nav - (1 - target.sum())))
            if gap > rule.band:
                pending = target
    if days and (not written or days[-1] != conn.execute(
            "SELECT as_of FROM holdings_snapshots WHERE snapshot_id=?", (written[-1],)).fetchone()[0]):
        px = table.loc[days[-1]]
        snapshot = [{"code": c, "name": names.get(c, ""), "quantity": holdings[c], "cost_price": None,
                     "price": float(px[c]), "market_value": holdings[c] * float(px[c])} for c in codes if holdings[c]]
        snapshot.append({"code": "CASH", "name": "现金", "quantity": round(cash, 2), "cost_price": None,
                         "price": 1.0, "market_value": round(cash, 2)})
        written.append(_insert(conn, account, days[-1], snapshot, actor))
    return {"account": account, "snapshots": written, "as_of": days[-1] if days else last["as_of"]}
