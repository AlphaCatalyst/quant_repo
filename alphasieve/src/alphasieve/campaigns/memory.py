"""Campaign memory. Statistical parts are derived from dev trials on every read, so they are reproducible;
agent insights are stored as memory_items. Nothing here ever reads holdout or fresh evidence."""

import json
import sqlite3
import uuid
from collections import defaultdict

from alphasieve.campaigns.service import completed_trials, get_campaign
from alphasieve.util import canonical_json, utcnow_iso

DEAD_CELL_MIN_TRIALS = 5
MAX_INSIGHTS = 30


def _cells(conn: sqlite3.Connection) -> dict[tuple[str, int], dict]:
    out = {}
    for r in conn.execute("SELECT factor_id, version, name, spec_json, canonical_expression FROM factor_specs"):
        spec = json.loads(r["spec_json"])
        out[(r["factor_id"], r["version"])] = {"name": r["name"], "expression": r["canonical_expression"],
                                              "cell": spec.get("cell", {}), "hypothesis": spec.get("hypothesis")}
    return out


def derive(conn: sqlite3.Connection, campaign_id: str) -> dict:
    trials = completed_trials(conn, campaign_id)
    specs = _cells(conn)
    successes, correlated, cells = {}, {}, defaultdict(lambda: {"trials": 0, "l1_passed": 0, "robust_passed": 0})
    for t in trials:
        info = specs.get((t["factor_id"], t["version"]), {})
        cell = info.get("cell", {})
        key = f"{cell.get('domain')}/{cell.get('form')}/{cell.get('scale')}"
        cells[key]["trials"] += 1
        gates = t["gate_results"]
        if gates.get("l1", {}).get("passed"):
            cells[key]["l1_passed"] += 1
        m = t["metrics"]
        if t["outcome"] == "robust_passed":
            cells[key]["robust_passed"] += 1
            successes[t["candidate_hash"]] = {"name": info.get("name"), "expression": info.get("expression"),
                                              "icir": m.get("icir"), "ic_mean": m.get("ic_mean"),
                                              "library_max_abs_corr": m.get("library_max_abs_corr")}
        corr_check = next((c for c in gates.get("l1", {}).get("checks", []) if c["name"] == "library_corr"), None)
        if corr_check and not corr_check["passed"]:
            correlated[t["candidate_hash"]] = {"expression": info.get("expression"),
                                               "corr": m.get("library_max_abs_corr"),
                                               "with": m.get("library_max_corr_with")}
    dead = sorted(k for k, v in cells.items() if v["trials"] >= DEAD_CELL_MIN_TRIALS and v["l1_passed"] == 0)
    return {"successes": list(successes.values()), "too_correlated": list(correlated.values())[-20:],
            "cells": dict(cells), "dead_cells": dead, "trials": len(trials)}


def add_insight(conn: sqlite3.Connection, campaign_id: str, content: str, source_turn: str | None = None) -> bool:
    if get_campaign(conn, campaign_id)["memory_frozen_at"] or not content.strip():
        return False
    conn.execute("INSERT INTO memory_items (item_id, campaign_id, scope, kind, content_json, source_trials, created_at)"
                 " VALUES (?, ?, 'campaign', 'insight', ?, ?, ?)",
                 (uuid.uuid4().hex[:12], campaign_id, canonical_json({"text": content.strip()[:2000]}),
                  canonical_json([source_turn] if source_turn else []), utcnow_iso()))
    return True


def insights(conn: sqlite3.Connection, campaign_id: str) -> list[dict]:
    rows = conn.execute("SELECT content_json, source_trials, created_at FROM memory_items WHERE campaign_id = ?"
                        " AND kind = 'insight' ORDER BY created_at DESC LIMIT ?", (campaign_id, MAX_INSIGHTS))
    return [{"text": json.loads(r["content_json"])["text"], "turn": (json.loads(r["source_trials"]) or [None])[0],
             "created_at": r["created_at"]} for r in rows]


def freeze(conn: sqlite3.Connection, campaign_id: str) -> None:
    conn.execute("UPDATE campaigns SET memory_frozen_at = COALESCE(memory_frozen_at, ?) WHERE campaign_id = ?",
                 (utcnow_iso(), campaign_id))


def _fmt(v, digits=3):
    return f"{v:.{digits}f}" if isinstance(v, (int, float)) else "n/a"


def render(conn: sqlite3.Connection, campaign_id: str) -> str:
    d = derive(conn, campaign_id)
    lines = ["# Campaign memory", "", f"Dev trials so far: {d['trials']}", "", "## Robust-passed candidates (L2)"]
    lines += [f"- `{s['expression']}` ICIR {_fmt(s['icir'])}, IC {_fmt(s['ic_mean'], 4)},"
              f" max library corr {_fmt(s['library_max_abs_corr'], 2)}" for s in d["successes"]] or ["- none yet"]
    lines += ["", "## Rejected as too correlated with the library (do not resubmit variants)"]
    lines += [f"- `{c['expression']}` corr {_fmt(c['corr'], 2)} with {c['with']}" for c in d["too_correlated"]] \
        or ["- none"]
    lines += ["", "## Cell coverage (trials / L1 passed / L2 passed)"]
    lines += [f"- {k}: {v['trials']} / {v['l1_passed']} / {v['robust_passed']}" for k, v in sorted(d["cells"].items())]
    if d["dead_cells"]:
        lines += ["", f"Dead cells (>= {DEAD_CELL_MIN_TRIALS} trials, no L1 pass): " + ", ".join(d["dead_cells"])]
    lines += ["", "## Insights from earlier turns"]
    lines += [f"- ({i['turn']}) {i['text']}" for i in insights(conn, campaign_id)] or ["- none yet"]
    return "\n".join(lines) + "\n"
