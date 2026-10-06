import json
import sqlite3
from collections import Counter
from datetime import datetime

from alphasieve.campaigns.service import completed_trials, elapsed_hours, get_campaign
from alphasieve.gates.l3 import search_intensity_curve
from alphasieve.gates.policy import failed_checks

LEVELS = ("submitted", "l0", "l1", "l2", "l3", "shortlisted", "holdout_passed")


def _level(trial: dict) -> str:
    gates = trial["gate_results"]
    if gates.get("l2", {}).get("passed"):
        return "l2"
    if gates.get("l1", {}).get("passed"):
        return "l1"
    if gates.get("l0", {}).get("passed"):
        return "l0"
    return "submitted"


def funnel(conn: sqlite3.Connection, campaign_id: str, include_holdout: bool = True) -> dict:
    trials = completed_trials(conn, campaign_id)
    best: dict[str, int] = {}
    failures: dict[str, Counter] = {lvl: Counter() for lvl in ("l0", "l1", "l2")}
    for t in trials:
        level = LEVELS.index(_level(t))
        best[t["candidate_hash"]] = max(best.get(t["candidate_hash"], 0), level)
        for gate_name in ("l0", "l1", "l2"):
            gate = t["gate_results"].get(gate_name)
            if gate and not gate.get("passed"):
                for check in failed_checks(gate):
                    failures[gate_name][check["name"]] += 1
                break
    counts = {lvl: sum(1 for v in best.values() if v >= i) for i, lvl in enumerate(LEVELS[:4])}
    shortlist = conn.execute("SELECT * FROM shortlists WHERE campaign_id = ? ORDER BY locked_at DESC LIMIT 1",
                             (campaign_id,)).fetchone()
    counts["l3"] = len(json.loads(shortlist["members_json"])) if shortlist else 0
    counts["shortlisted"] = counts["l3"]
    if include_holdout:
        passed = conn.execute("SELECT COUNT(*) AS n FROM trials WHERE campaign_id = ? AND evidence_tier = 'holdout'"
                              " AND outcome = 'holdout_passed'", (campaign_id,)).fetchone()["n"]
        counts["holdout_passed"] = passed
    return {"counts": counts, "trials": len(trials), "distinct_candidates": len(best),
            "failure_reasons": {k: dict(v.most_common()) for k, v in failures.items()}}


def budget_status(conn: sqlite3.Connection, campaign_id: str) -> dict:
    campaign = get_campaign(conn, campaign_id)
    spec = campaign["spec"]
    all_turns = [dict(r) for r in conn.execute("SELECT * FROM turns WHERE campaign_id = ? ORDER BY turn_index",
                                               (campaign_id,))]
    turns = [t for t in all_turns if t["status"] != "interrupted"]
    no_improvement = 0
    for t in reversed(turns):
        if t["status"] != "completed" or (t["robust_passed_new"] or 0) > 0:
            break
        no_improvement += 1
    resumed_after = campaign["stats"].get("resumed_after_turn", 0)
    failed_streak = 0
    for t in reversed(turns):
        if t["status"] != "failed" or t["turn_index"] <= resumed_after:
            break
        failed_streak += 1
    tokens = {"input": 0, "output": 0, "cost_usd": 0.0}
    for t in all_turns:  # interrupted turns do not use turn budget but their tokens were still spent
        usage = json.loads(t["usage_json"] or "{}")
        tokens["input"] += usage.get("input_tokens", 0) or 0
        tokens["output"] += usage.get("output_tokens", 0) or 0
        tokens["cost_usd"] += usage.get("cost_usd", 0.0) or 0.0
    trials = len(completed_trials(conn, campaign_id))
    paused_seconds = campaign["stats"].get("paused_seconds", 0.0)
    if paused_at := campaign["stats"].get("paused_at"):
        paused_seconds += max(0.0, (datetime.now().astimezone() - datetime.fromisoformat(paused_at)).total_seconds())
    return {
        "trials": {"used": trials, "budget": spec.budgets.trials},
        "turns": {"used": len(turns), "budget": spec.budgets.turns},
        "hours": {"used": round(max(0.0, elapsed_hours(campaign) - paused_seconds / 3600), 2),
                  "budget": spec.budgets.max_hours},
        "no_improvement_turns": {"current": no_improvement, "limit": spec.stop.no_improvement_turns},
        "failed_turns_streak": {"current": failed_streak, "limit": spec.stop.max_consecutive_failed_turns},
        "usage": tokens,
    }


def stop_reason(conn: sqlite3.Connection, campaign_id: str) -> str | None:
    b = budget_status(conn, campaign_id)
    if b["trials"]["used"] >= b["trials"]["budget"]:
        return "trial_budget_exhausted"
    if b["turns"]["used"] >= b["turns"]["budget"]:
        return "turn_budget_exhausted"
    if b["hours"]["used"] >= b["hours"]["budget"]:
        return "time_budget_exhausted"
    if b["no_improvement_turns"]["current"] >= b["no_improvement_turns"]["limit"]:
        return "no_improvement"
    return None


def search_intensity(conn: sqlite3.Connection, campaign_id: str) -> list[dict]:
    return search_intensity_curve(completed_trials(conn, campaign_id))


def recent_outcomes(conn: sqlite3.Connection, campaign_id: str, limit: int = 12) -> list[dict]:
    rows = completed_trials(conn, campaign_id)[-limit:]
    out = []
    for t in rows:
        spec = conn.execute("SELECT name, canonical_expression FROM factor_specs WHERE factor_id = ? AND version = ?",
                            (t["factor_id"], t["version"])).fetchone()
        failed = [f"{lvl}.{c['name']}" for lvl, g in t["gate_results"].items() for c in failed_checks(g)]
        out.append({"factor": f"{t['factor_id']}@{t['version']}", "name": spec["name"] if spec else None,
                    "expression": spec["canonical_expression"] if spec else None, "outcome": t["outcome"],
                    "ic_mean": t["metrics"].get("ic_mean"), "icir": t["metrics"].get("icir"),
                    "library_max_abs_corr": t["metrics"].get("library_max_abs_corr"), "failed": failed})
    return out
