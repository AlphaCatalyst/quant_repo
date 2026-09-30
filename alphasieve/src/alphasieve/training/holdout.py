"""Strategy-layer holdout reads (15 §4 P-4, docs/19 §1.2).

A completed dev trial of a TrainingTask can be locked into a request; only a human can approve it, each mandate
has a budget of one approved read, and the approved read re-runs exactly the locked configuration (same config
hash, same frozen feature set) with the holdout window as the scoring period. Holdout panels never leave this host.
"""

import json
import sqlite3
import uuid
from dataclasses import replace

from alphasieve.config import Settings
from alphasieve.errors import AlphaSieveError, permission_denied
from alphasieve.training.run import complete_trial, execute
from alphasieve.training.task import parse_task
from alphasieve.util import canonical_json, sha256_hex, utcnow_iso

READS_PER_MANDATE = 1


def _require_human(settings: Settings, action: str) -> None:
    if settings.role != "human":
        raise permission_denied(f"{action} is human-only")


def _trial_bundle(conn: sqlite3.Connection, settings: Settings, trial_id: str) -> tuple[dict, dict]:
    row = conn.execute("SELECT * FROM trials WHERE trial_id = ? AND layer = 'strategy' AND record_kind = 'completed'"
                       " AND evidence_tier = 'dev'", (trial_id,)).fetchone()
    if row is None:
        raise AlphaSieveError("NOT_FOUND", f"no completed dev strategy trial {trial_id}")
    metrics = json.loads((settings.artifacts_dir / row["artifact_id"] / "metrics.json").read_text(encoding="utf-8"))
    bundle = metrics.get("bundle")
    if bundle is None:
        raise AlphaSieveError("NOT_FOUND", f"trial {trial_id} has no frozen bundle in its artifact")
    return dict(row), bundle


def request_read(conn: sqlite3.Connection, settings: Settings, trial_id: str) -> dict:
    _require_human(settings, "strategy holdout request")
    row, bundle = _trial_bundle(conn, settings, trial_id)
    request_id = f"SH-{uuid.uuid4().hex[:10]}"
    conn.execute("INSERT INTO strategy_holdout_requests (request_id, mandate, task_id, trial_id, config_hash, status,"
                 " created_by, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                 (request_id, row["scope"], bundle["task"]["task_id"], trial_id, bundle["config_hash"],
                  settings.user, utcnow_iso()))
    return {"request_id": request_id, "mandate": row["scope"], "trial_id": trial_id, "status": "pending"}


def _approved(conn: sqlite3.Connection, mandate: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM strategy_holdout_requests WHERE mandate = ? AND status = 'approved'",
                        (mandate,)).fetchone()[0]


def _request(conn: sqlite3.Connection, request_id: str) -> dict:
    row = conn.execute("SELECT * FROM strategy_holdout_requests WHERE request_id = ?", (request_id,)).fetchone()
    if row is None:
        raise AlphaSieveError("NOT_FOUND", f"strategy holdout request {request_id} not found")
    if row["status"] != "pending":
        raise AlphaSieveError("CONFLICT", f"request {request_id} is {row['status']}")
    return dict(row)


def _decision(conn: sqlite3.Connection, settings: Settings, request_id: str, decision: str, reason: str) -> None:
    conn.execute("INSERT INTO decisions (decision_id, object_type, object_id, decision, reason, decided_by,"
                 " decided_at, evidence_hash) VALUES (?, 'strategy_holdout_request', ?, ?, ?, ?, ?, ?)",
                 (f"D-{uuid.uuid4().hex[:10]}", request_id, decision, reason, settings.user, utcnow_iso(),
                  sha256_hex(request_id + decision + reason)))


def approve_read(conn: sqlite3.Connection, settings: Settings, request_id: str, reason: str, processes=None) -> dict:
    _require_human(settings, "strategy holdout approve")
    req = _request(conn, request_id)
    if _approved(conn, req["mandate"]) >= READS_PER_MANDATE:
        raise AlphaSieveError("BUDGET_EXHAUSTED", f"mandate {req['mandate']} already used its holdout read")
    _, bundle = _trial_bundle(conn, settings, req["trial_id"])
    if bundle["config_hash"] != req["config_hash"]:
        raise AlphaSieveError("CONFLICT", "the locked configuration changed since the request")
    conn.execute("UPDATE strategy_holdout_requests SET status = 'approved', decided_by = ?, decided_at = ?,"
                 " reason = ? WHERE request_id = ?", (settings.user, utcnow_iso(), reason, request_id))
    _decision(conn, settings, request_id, "approved", reason)
    system = replace(settings, role="system")
    task = parse_task(bundle["task"])
    from alphasieve.training.run import start_trial

    holdout_trial = f"{req['trial_id']}-H"
    start_trial(conn, system, task, holdout_trial, check_budget=False)
    result, _ = execute(system, {**bundle, "trial_id": holdout_trial}, processes, tier="holdout")
    artifact_id = complete_trial(conn, system, task, holdout_trial, result, tier="holdout")
    conn.execute("UPDATE strategy_holdout_requests SET result_json = ? WHERE request_id = ?",
                 (canonical_json({"trial_id": holdout_trial, "artifact_id": artifact_id,
                                  "acceptance": result.get("acceptance")}), request_id))
    return {"request_id": request_id, "status": "approved", "holdout_trial": holdout_trial,
            "artifact_id": artifact_id, "acceptance": result.get("acceptance")}


def reject_read(conn: sqlite3.Connection, settings: Settings, request_id: str, reason: str) -> dict:
    _require_human(settings, "strategy holdout reject")
    _request(conn, request_id)
    conn.execute("UPDATE strategy_holdout_requests SET status = 'rejected', decided_by = ?, decided_at = ?,"
                 " reason = ? WHERE request_id = ?", (settings.user, utcnow_iso(), reason, request_id))
    _decision(conn, settings, request_id, "rejected", reason)
    return {"request_id": request_id, "status": "rejected"}
