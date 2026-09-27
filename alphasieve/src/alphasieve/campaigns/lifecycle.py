"""Campaign conclusion (L3 + shortlist lock), holdout reads (L4) and review decisions.

Holdout approval and review decisions are human-only. The holdout panel is read inside this module with an
internal system role; results never flow into campaign memory or agent-visible commands.
"""

import json
import sqlite3
import uuid
from dataclasses import replace

from alphasieve.artifacts import write_artifact
from alphasieve.audit import record_event
from alphasieve.campaigns import memory
from alphasieve.campaigns.service import completed_trials, get_campaign, set_status
from alphasieve.config import Settings, load_config
from alphasieve.contracts import TrialLedgerEntry
from alphasieve.data.access import load_panel
from alphasieve.errors import AlphaSieveError, not_found, permission_denied, validation_error
from alphasieve.evaluation.core import EvalInputs, l1_metrics
from alphasieve.factors.dsl import compile_expression, evaluate
from alphasieve.factors.registry import get_factor
from alphasieve.gates.l3 import benjamini_hochberg, evaluate_candidate, search_discount
from alphasieve.gates.policy import _check, _result
from alphasieve.gates.state_machine import advance, transition
from alphasieve.ledger import append_trial
from alphasieve.search_space import load_search_space
from alphasieve.util import canonical_json, code_version, pretty_json, sha256_hex, utcnow_iso

REVIEW_DECISIONS = {"rejected", "needs_repair", "approved_for_shadow"}


def _require_human(settings: Settings, action: str) -> None:
    if settings.role != "human":
        raise permission_denied(f"{action} requires the human role")


def l3_screen(conn: sqlite3.Connection, settings: Settings, campaign_id: str) -> dict:
    campaign = get_campaign(conn, campaign_id)
    horizon = campaign["spec"].horizon
    policy = load_config(settings, "gate_policy")["l3"]
    trials = completed_trials(conn, campaign_id)
    discount = search_discount(trials)
    latest: dict[str, dict] = {}
    for t in trials:
        if t["outcome"] == "robust_passed":
            latest[t["candidate_hash"]] = t
    rows = {}
    for h, t in latest.items():
        m = t["metrics"]
        rows[h] = {"factor_id": t["factor_id"], "version": t["version"], "candidate_hash": h, "trial_id": t["trial_id"],
                   **evaluate_candidate(m.get("icir") or 0.0, m.get("valid_dates") or 0, horizon, discount,
                                        m.get("ic_skew") or 0.0, m.get("ic_kurtosis") or 3.0)}
    bh = benjamini_hochberg({h: r["p_value"] for h, r in rows.items()}, policy["bh_q"]) if rows else {}
    for h, r in rows.items():
        r["bh"] = bh[h]
        r["passed"] = r["p_value"] <= policy["dsr_max_p"] and bh[h]["passed"]
    ranked = sorted(rows.values(), key=lambda r: r["dsr"], reverse=True)
    members = [r for r in ranked if r["passed"]][: policy["max_shortlist"]]
    return {"discount": discount, "policy": policy, "candidates": ranked, "members": members}


def conclude(conn: sqlite3.Connection, settings: Settings, campaign_id: str, reason: str) -> dict:
    campaign = get_campaign(conn, campaign_id)
    if campaign["status"] in ("running", "paused"):
        set_status(conn, settings, campaign_id, "concluding", reason)
    elif campaign["status"] != "concluding":
        raise AlphaSieveError("CONFLICT", f"campaign {campaign_id} is {campaign['status']}")
    screen = l3_screen(conn, settings, campaign_id)
    member_keys = {r["candidate_hash"] for r in screen["members"]}
    skipped = []
    for r in screen["candidates"]:
        state = conn.execute("SELECT state FROM factor_specs WHERE factor_id = ? AND version = ?",
                             (r["factor_id"], r["version"])).fetchone()["state"]
        if state != "robust_passed":
            skipped.append({"factor": f"{r['factor_id']}@{r['version']}", "state": state})
            if r["candidate_hash"] in member_keys:
                member_keys.discard(r["candidate_hash"])
            continue
        path = ["ledger_gated", "shortlist_locked"] if r["candidate_hash"] in member_keys else ["ledger_failed"]
        advance(conn, settings, r["factor_id"], r["version"], path, r["trial_id"])
    members = [r for r in screen["members"] if r["candidate_hash"] in member_keys]
    memory.freeze(conn, campaign_id)
    shortlist_id = f"S-{uuid.uuid4().hex[:10]}"
    conn.execute("INSERT INTO shortlists (shortlist_id, campaign_id, members_json, l3_json, status, locked_at,"
                 " locked_by) VALUES (?, ?, ?, ?, 'locked', ?, ?)",
                 (shortlist_id, campaign_id, canonical_json(members), canonical_json(screen), utcnow_iso(),
                  settings.user))
    record_event(conn, settings, "shortlist.locked", object_type="campaign", object_id=campaign_id,
                 payload={"shortlist_id": shortlist_id, "members": len(members), "reason": reason})
    out = {"shortlist_id": shortlist_id, "members": members, "l3": screen, "skipped": skipped}
    if not members:
        set_status(conn, settings, campaign_id, "concluded", "no L3 survivors")
        out["holdout_request"] = None
        return out
    if _approved_reads(conn, campaign_id) >= get_campaign(conn, campaign_id)["spec"].budgets.holdout_reads:
        set_status(conn, settings, campaign_id, "concluded", "shortlist locked; no holdout read budget left")
        out["holdout_request"] = None
        out["note"] = "no holdout read budget; the shortlist is locked but no request was created"
        return out
    out["holdout_request"] = create_holdout_request(conn, settings, campaign_id, shortlist_id)
    set_status(conn, settings, campaign_id, "awaiting_holdout_approval", reason)
    return out


def _approved_reads(conn: sqlite3.Connection, campaign_id: str) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM holdout_requests WHERE campaign_id = ? AND status = 'approved'",
                        (campaign_id,)).fetchone()["n"]


def create_holdout_request(conn: sqlite3.Connection, settings: Settings, campaign_id: str, shortlist_id: str) -> dict:
    budget = get_campaign(conn, campaign_id)["spec"].budgets.holdout_reads
    if _approved_reads(conn, campaign_id) >= budget:
        raise AlphaSieveError("BUDGET_EXHAUSTED", f"campaign {campaign_id} used its {budget} holdout reads")
    if conn.execute("SELECT 1 FROM holdout_requests WHERE shortlist_id = ?", (shortlist_id,)).fetchone():
        raise AlphaSieveError("CONFLICT", f"shortlist {shortlist_id} already has a holdout request")
    request_id = f"H-{uuid.uuid4().hex[:10]}"
    conn.execute("INSERT INTO holdout_requests (request_id, shortlist_id, campaign_id, reads_requested, status,"
                 " created_at) VALUES (?, ?, ?, 1, 'pending', ?)",
                 (request_id, shortlist_id, campaign_id, utcnow_iso()))
    record_event(conn, settings, "holdout.requested", object_type="holdout_request", object_id=request_id,
                 payload={"campaign_id": campaign_id, "shortlist_id": shortlist_id})
    return {"request_id": request_id, "shortlist_id": shortlist_id, "status": "pending"}


def _holdout_request(conn: sqlite3.Connection, request_id: str) -> dict:
    row = conn.execute("SELECT * FROM holdout_requests WHERE request_id = ?", (request_id,)).fetchone()
    if row is None:
        raise not_found(f"holdout request {request_id} not found")
    if row["status"] != "pending":
        raise AlphaSieveError("CONFLICT", f"holdout request {request_id} is already {row['status']}")
    return dict(row)


def reject_holdout(conn: sqlite3.Connection, settings: Settings, request_id: str, reason: str) -> dict:
    _require_human(settings, "holdout reject")
    req = _holdout_request(conn, request_id)
    if not reason.strip():
        raise validation_error("a reason is required")
    conn.execute("UPDATE holdout_requests SET status = 'rejected', decided_by = ?, decided_at = ?, reason = ?"
                 " WHERE request_id = ?", (settings.user, utcnow_iso(), reason, request_id))
    _decision(conn, settings, "holdout_request", request_id, "rejected", reason, None)
    set_status(conn, settings, req["campaign_id"], "concluded", "holdout request rejected")
    return {"request_id": request_id, "status": "rejected"}


def _holdout_eval(settings: Settings, conn: sqlite3.Connection, panel, member: dict, horizon: int, policy: dict,
                  dev_icir: float, campaign_id: str) -> dict:
    factor = get_factor(conn, f"{member['factor_id']}@{member['version']}")
    space = load_search_space(settings)
    trial_id = uuid.uuid4().hex[:16]
    base = dict(trial_id=trial_id, campaign_id=campaign_id, factor_id=factor["factor_id"], version=factor["version"],
                candidate_hash=factor["candidate_hash"], evidence_tier="holdout",
                gate_policy_version=policy["version"], search_space_version=space.version_tag, created_by="system")
    contaminated = conn.execute("SELECT 1 FROM trials WHERE candidate_hash = ? AND evidence_tier = 'holdout'"
                                " AND record_kind = 'completed'", (factor["candidate_hash"],)).fetchone() is not None
    append_trial(conn, TrialLedgerEntry(record_kind="started", **base))
    transition(conn, settings, factor["factor_id"], factor["version"], "holdout_evaluating", trial_id)
    compiled = compile_expression(factor["canonical_expression"], space)
    values = evaluate(compiled, panel) * factor["spec"]["direction"]
    m = l1_metrics(values, EvalInputs(panel, horizon), {})
    p = policy["l4"]
    checks = [_check("holdout_ic_mean", m["ic_mean"], p["min_ic_mean"], ">"),
              _check("holdout_icir_ratio", m["icir"] / dev_icir if dev_icir else float("nan"), p["min_icir_ratio"],
                     ">=")]
    gate = _result(checks, contaminated=contaminated)
    outcome = "holdout_contaminated" if contaminated else ("holdout_passed" if gate["passed"] else "holdout_failed")
    summary = {k: m.get(k) for k in ("ic_mean", "icir", "ic_positive_ratio", "coverage", "valid_dates")}
    summary["long_short"] = m["quantiles"]["long_short"]
    start, end = panel.window
    window = f"{start.date()}..{end.date()}"
    manifest = {"kind": "holdout_eval", "candidate_hash": factor["candidate_hash"], "panel_signature": panel.signature,
                "window": window, "gate_policy_version": policy["version"], "code_version": code_version(),
                "trial_id": trial_id}
    artifact_id = write_artifact(settings, manifest, metrics={"summary": summary, "gate": gate, "dev_icir": dev_icir})
    append_trial(conn, TrialLedgerEntry(record_kind="completed", metrics=summary, gate_results={"l4": gate},
                                        outcome=outcome, artifact_id=artifact_id, data_window=window, **base))
    transition(conn, settings, factor["factor_id"], factor["version"], outcome, trial_id)
    return {"factor": f"{factor['factor_id']}@{factor['version']}", "name": factor["name"],
            "expression": factor["canonical_expression"], "outcome": outcome, "dev_icir": dev_icir,
            "holdout": summary, "gate": gate, "artifact_id": artifact_id, "trial_id": trial_id}


def approve_holdout(conn: sqlite3.Connection, settings: Settings, request_id: str, reason: str) -> dict:
    _require_human(settings, "holdout approve")
    req = _holdout_request(conn, request_id)
    campaign = get_campaign(conn, req["campaign_id"])
    if _approved_reads(conn, req["campaign_id"]) >= campaign["spec"].budgets.holdout_reads:
        raise AlphaSieveError("BUDGET_EXHAUSTED", "holdout read budget exhausted")
    shortlist = conn.execute("SELECT * FROM shortlists WHERE shortlist_id = ?", (req["shortlist_id"],)).fetchone()
    members = json.loads(shortlist["members_json"])
    conn.execute("UPDATE holdout_requests SET status = 'approved', decided_by = ?, decided_at = ?, reason = ?"
                 " WHERE request_id = ?", (settings.user, utcnow_iso(), reason, request_id))
    _decision(conn, settings, "holdout_request", request_id, "approved", reason, None)
    system = replace(settings, role="system")
    policy = load_config(settings, "gate_policy")
    panel = load_panel(system, "holdout")
    dev = {t["trial_id"]: t["metrics"] for t in completed_trials(conn, req["campaign_id"])}
    results = [_holdout_eval(system, conn, panel, m, campaign["spec"].horizon, policy,
                             dev[m["trial_id"]].get("icir") or 0.0, req["campaign_id"]) for m in members]
    packets = [create_review_packet(conn, settings, r, req) for r in results if r["outcome"] == "holdout_passed"]
    conn.execute("UPDATE holdout_requests SET result_json = ? WHERE request_id = ?",
                 (canonical_json({"results": results, "packets": packets}), request_id))
    set_status(conn, settings, req["campaign_id"], "holdout_evaluated", f"holdout {request_id} evaluated")
    return {"request_id": request_id, "status": "approved", "results": results, "review_packets": packets}


def create_review_packet(conn: sqlite3.Connection, settings: Settings, result: dict, req: dict) -> dict:
    factor_id, version = result["factor"].split("@")
    factor = get_factor(conn, result["factor"])
    dev_trials = [dict(r) for r in conn.execute(
        "SELECT trial_id, metrics_json, gate_results_json, artifact_id FROM trials WHERE factor_id = ? AND version = ?"
        " AND evidence_tier = 'dev' AND record_kind = 'completed' ORDER BY seq", (factor_id, int(version)))]
    shortlist = conn.execute("SELECT l3_json FROM shortlists WHERE shortlist_id = ?", (req["shortlist_id"],)).fetchone()
    l3 = next((c for c in json.loads(shortlist["l3_json"])["candidates"]
               if c["candidate_hash"] == factor["candidate_hash"]), None)
    packet = {
        "factor": result["factor"], "name": factor["name"], "expression": factor["canonical_expression"],
        "hypothesis": factor["spec"].get("hypothesis"), "cell": factor["spec"].get("cell"),
        "campaign_id": req["campaign_id"], "shortlist_id": req["shortlist_id"],
        "dev": [{"trial_id": t["trial_id"], "metrics": json.loads(t["metrics_json"]),
                 "gates": json.loads(t["gate_results_json"]), "artifact_id": t["artifact_id"]} for t in dev_trials],
        "l3": l3, "holdout": {k: result[k] for k in ("holdout", "gate", "artifact_id", "trial_id", "dev_icir")},
    }
    evidence_hash = sha256_hex(canonical_json(packet))
    report = "# Review packet\n\n```json\n" + pretty_json(packet) + "\n```\n"
    artifact_id = write_artifact(settings, {"kind": "review_packet", "evidence_hash": evidence_hash}, metrics=packet,
                                 report=report)
    packet_id = f"P-{uuid.uuid4().hex[:10]}"
    conn.execute("INSERT INTO review_packets (packet_id, factor_id, version, campaign_id, shortlist_id, artifact_id,"
                 " status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
                 (packet_id, factor_id, int(version), req["campaign_id"], req["shortlist_id"], artifact_id,
                  utcnow_iso()))
    transition(conn, settings, factor_id, int(version), "reviewable", result["trial_id"])
    return {"packet_id": packet_id, "factor": result["factor"], "artifact_id": artifact_id,
            "evidence_hash": evidence_hash}


def _decision(conn, settings, object_type, object_id, decision, reason, evidence_hash) -> str:
    decision_id = f"DEC-{uuid.uuid4().hex[:10]}"
    conn.execute("INSERT INTO decisions (decision_id, object_type, object_id, decision, reason, decided_by, decided_at,"
                 " evidence_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (decision_id, object_type, object_id, decision, reason, settings.user, utcnow_iso(), evidence_hash))
    record_event(conn, settings, "decision.recorded", object_type=object_type, object_id=object_id,
                 payload={"decision_id": decision_id, "decision": decision})
    return decision_id


def decide_review(conn: sqlite3.Connection, settings: Settings, packet_id: str, decision: str, reason: str) -> dict:
    _require_human(settings, "review decide")
    if decision not in REVIEW_DECISIONS:
        raise validation_error(f"decision must be one of {sorted(REVIEW_DECISIONS)}")
    if not reason.strip():
        raise validation_error("a reason is required")
    row = conn.execute("SELECT * FROM review_packets WHERE packet_id = ?", (packet_id,)).fetchone()
    if row is None:
        raise not_found(f"review packet {packet_id} not found")
    if row["status"] != "open":
        raise AlphaSieveError("CONFLICT", f"review packet {packet_id} is already {row['status']}")
    manifest = json.loads((settings.artifacts_dir / row["artifact_id"] / "manifest.json").read_text(encoding="utf-8"))
    decision_id = _decision(conn, settings, "review_packet", packet_id, decision, reason, manifest["evidence_hash"])
    conn.execute("UPDATE review_packets SET status = ? WHERE packet_id = ?", (decision, packet_id))
    transition(conn, settings, row["factor_id"], row["version"], decision, reason=reason)
    return {"packet_id": packet_id, "decision": decision, "decision_id": decision_id}
