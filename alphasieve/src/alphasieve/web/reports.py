"""Read-only views over saved reports for the web frontend: decision tasks, red flags, announcements, docs."""

import json
import re
import sqlite3
from collections import Counter
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd

from alphasieve.announcements import list_local
from alphasieve.config import Settings
from alphasieve.decisions.ledger import list_trials
from alphasieve.portfolio_book import importer
from alphasieve.redflag.service import RULES

PROBES = ("p1", "p2", "p4", "p6", "p7")
DECISION_TASKS = (
    ("P1", "持有中的大跌预警", "probe"),
    ("P2", "换股：卖 A 买 B 是否更好", "probe"),
    ("P3", "仓位与再平衡规则", "run"),
    ("P4", "公告事件后的处理", "probe"),
    ("P5", "交易行为偏差", "book"),
    ("P6", "周期股估值位置", "probe"),
    ("P7", "候选股筛选", "probe"),
)


def _read_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _rule_brief(rule: dict) -> dict:
    keep = ("rule_id", "rule", "ce_gamma2", "ce_gamma4", "mean_wealth", "median_wealth", "p_loss_30", "p_mdd_30",
            "mean_cost", "mean_rebalances", "decision_value", "random", "acceptance")
    return {k: rule[k] for k in keep if k in rule}


def decisions_view(settings: Settings, conn: sqlite3.Connection) -> dict:
    root = settings.hot_root / "reports" / "decisions"
    p3 = _read_json(root / "p3-latest.json")
    if p3:
        pools = []
        for pool in p3["pools"]:
            selected = next((r for r in pool["rules"] if r["rule_id"] == pool["selected"]), None)
            pools.append({
                "pool": pool["pool"], "accounts": pool["accounts"], "start_range": pool.get("start_range"),
                "holdings_mix": pool.get("holdings_mix"), "no_action": pool["no_action"],
                "mechanical": pool["mechanical"], "rules": [_rule_brief(r) for r in pool["rules"]],
                "selected": pool["selected"], "selected_cohorts": selected.get("cohorts") if selected else None,
                "selected_acceptance": pool["selected_acceptance"],
            })
        p3 = {k: p3[k] for k in ("run_id", "tier", "data_end", "config_hash", "created_at", "method") if k in p3} | {
            "config": {k: p3["config"].get(k) for k in ("accounts", "pools", "grid", "mechanical", "costs",
                                                         "random_reps", "gammas", "acceptance", "trial_budget")},
            "pools": pools}
    trials = list_trials(conn)
    by_task = Counter(t["task"] for t in trials)
    budget = (p3 or {}).get("config", {}).get("trial_budget")
    return {
        "tasks": [{"task": t, "title": title, "kind": kind} for t, title, kind in DECISION_TASKS],
        "p3": p3,
        "probes": {name: _read_json(root / f"probe-{name}.json") for name in PROBES},
        "trials": {"count": len(trials), "by_task": dict(by_task), "p3_budget": budget,
                   "latest": trials[-1]["created_at"] if trials else None},
    }


def holdings(conn: sqlite3.Connection) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for item in importer.list_snapshots(conn):
        if item.get("source") == "virtual":
            continue
        current = latest.get(item["account"])
        if current is None or (item["as_of"], item["created_at"]) > (current["as_of"], current["created_at"]):
            latest[item["account"]] = item
    held: dict[str, dict] = {}
    for item in latest.values():
        for position in importer.show_snapshot(conn, item["snapshot_id"])["positions"]:
            code = str(position["code"])
            if code.upper() == "CASH":
                continue
            entry = held.setdefault(code[-6:], {"name": position.get("name"), "accounts": []})
            entry["accounts"].append(item["account"])
    return held


@lru_cache(maxsize=4)
def _redflag_frame(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_parquet(path)


@lru_cache(maxsize=4)
def _names(path: str, mtime: float) -> dict[str, str]:
    frame = pd.read_parquet(path, columns=["code", "code_name"])
    return {str(c)[-6:]: n for c, n in zip(frame["code"], frame["code_name"], strict=True)}


def stock_names(settings: Settings) -> dict[str, str]:
    path = settings.raw_dir / "baostock_all" / "stock_basic.parquet"
    return _names(str(path), path.stat().st_mtime) if path.is_file() else {}


def redflags_view(settings: Settings, conn: sqlite3.Connection) -> dict:
    root = settings.hot_root / "redflag"
    folders = sorted((p for p in root.glob("*") if (p / "results.parquet").is_file()), reverse=True) \
        if root.is_dir() else []
    catalog = [{"id": r.id, "name": r.name, "formula": r.formula, "rationale": r.rationale,
                "amber": r.amber, "red": r.red, "direction": r.direction, "available": r.available} for r in RULES]
    if not folders:
        return {"asof": None, "rules": catalog, "rows": [], "levels": {}, "scans": []}
    path = folders[0] / "results.parquet"
    frame = _redflag_frame(str(path), path.stat().st_mtime)
    names = stock_names(settings)
    held = holdings(conn)
    rule_ids = [r.id for r in RULES]
    rows = []
    for record in frame.to_dict("records"):
        code6 = str(record["code"])[-6:]
        flagged = {rid: {"level": record.get(f"{rid}_level"), "value": record.get(f"{rid}_value")}
                   for rid in rule_ids if record.get(f"{rid}_level") in ("red", "amber")}
        if record["level"] not in ("red", "amber") and code6 not in held:
            continue
        rows.append({"code": record["code"], "name": names.get(code6), "industry": record.get("industry"),
                     "period": record.get("period"), "announcement_date": record.get("announcement_date"),
                     "level": record["level"], "held": code6 in held,
                     "flags": {k: {**v, "value": None if pd.isna(v["value"]) else float(v["value"])}
                               for k, v in flagged.items()}})
    levels = frame["level"].value_counts().to_dict()
    rule_counts = {rid: {lv: int((frame[f"{rid}_level"] == lv).sum()) for lv in ("red", "amber")}
                   for rid in rule_ids if f"{rid}_level" in frame}
    with (folders[0] / "results.json").open(encoding="utf-8") as fh:
        universe = re.search(r'"universe": "([^"]+)"', fh.read(512))
    return {"asof": folders[0].name, "universe": universe and universe.group(1), "count": len(frame),
            "levels": {k: int(v) for k, v in levels.items()}, "rule_counts": rule_counts, "rules": catalog,
            "rows": rows, "held_total": len(held), "scans": [p.name for p in folders]}


_ANNOUNCEMENT_FIELDS = ("announcement_id", "code", "name", "title", "published_at", "event_type", "importance",
                        "pdf_url")
_announcement_cache: dict[tuple, list[dict]] = {}


def _recent_announcements(settings: Settings, since: str) -> list[dict]:
    root = settings.raw_dir / "cninfo" / "announcements"
    files = [p for p in root.glob("*.parquet") if p.stem >= since] if root.is_dir() else []
    key = (str(root), since, len(files), max((p.stat().st_mtime for p in files), default=0))
    if key not in _announcement_cache:
        _announcement_cache.clear()
        _announcement_cache[key] = [{k: row.get(k) for k in _ANNOUNCEMENT_FIELDS}
                                    for row in list_local(settings, since=since)]
    return _announcement_cache[key]


def announcements_view(settings: Settings, conn: sqlite3.Connection, days: int = 30,
                       importance: str | None = None, limit: int = 400) -> dict:
    """importance: None = high and medium, "all", one level, or "held" (holdings only)."""
    since = (date.today() - timedelta(days=days)).isoformat()
    held = holdings(conn)
    recent = _recent_announcements(settings, since)
    held_rows = [r | {"held": True} for r in recent if r["code"] in held]

    def keep(row: dict) -> bool:
        if importance == "held":
            return row["code"] in held
        if importance is None:
            return row["importance"] != "low"
        return importance == "all" or row["importance"] == importance

    rows = [r | {"held": r["code"] in held} for r in recent if keep(r)]
    root = settings.raw_dir / "cninfo" / "announcements"
    coverage = Counter(p.stem[:4] for p in root.glob("*.parquet")) if root.is_dir() else Counter()
    return {"since": since, "days": days, "total": len(recent), "shown": len(rows),
            "by_importance": dict(Counter(r["importance"] for r in recent)),
            "by_type": dict(Counter(r["event_type"] for r in recent if r["importance"] != "low").most_common()),
            "held": held_rows[:100], "rows": rows[:limit], "truncated": len(rows) > limit,
            "coverage": dict(sorted(coverage.items()))}


def docs_index(docs: Path) -> list[dict]:
    readme = (docs / "README.md").read_text(encoding="utf-8") if (docs / "README.md").is_file() else ""
    described = dict(re.findall(r"^\| \[([\w.-]+\.md)\]\([^)]*\) \| (.+?) \|$", readme, re.M))
    items = []
    for path in sorted(docs.glob("*/*.md")):
        text = path.read_text(encoding="utf-8")
        title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), path.stem)
        status = next((line.removeprefix("状态：").strip() for line in text.splitlines()[:8]
                       if line.startswith("状态：")), None)
        items.append({"name": path.name, "folder": path.parent.name, "title": title, "status": status,
                      "summary": described.get(path.name), "chars": len(text)})
    return items


def sw_sensitivity_view(settings: Settings) -> dict | None:
    root = settings.hot_root / "reports" / "sw_sensitivity"
    if not (root / "manifest.json").is_file():
        return None
    manifest = _read_json(root / "manifest.json") or {}

    def table(name: str) -> list[dict]:
        path = root / name
        return json.loads(pd.read_csv(path).to_json(orient="records")) if path.is_file() else []

    drivers = table("industry_drivers.csv")
    return {"window": manifest.get("window"), "trials": manifest.get("trials"),
            "portfolio_summary": table("portfolio_summary.csv"), "factor_neutral_ic": table("factor_neutral_ic.csv"),
            "industry_drivers": sorted(drivers, key=lambda r: -r.get("exceed_days", 0))[:40]}
