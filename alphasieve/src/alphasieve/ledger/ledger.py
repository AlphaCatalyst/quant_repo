import json
import sqlite3
from collections import Counter

from alphasieve.contracts import TrialLedgerEntry
from alphasieve.gates.policy import failed_checks
from alphasieve.util import canonical_json, sha256_hex, utcnow_iso

GENESIS_HASH = "0" * 64

HASHED_COLUMNS = (
    "trial_id",
    "record_kind",
    "campaign_id",
    "factor_id",
    "version",
    "candidate_hash",
    "evidence_tier",
    "data_window",
    "gate_policy_version",
    "search_space_version",
    "metrics_json",
    "gate_results_json",
    "outcome",
    "created_by",
    "artifact_id",
    "created_at",
)


def _row_hash(prev_hash: str, row: dict) -> str:
    payload = canonical_json({col: row[col] for col in HASHED_COLUMNS})
    return sha256_hex(prev_hash + payload)


def append_trial(conn: sqlite3.Connection, entry: TrialLedgerEntry) -> dict:
    row = {
        "trial_id": entry.trial_id,
        "record_kind": entry.record_kind,
        "campaign_id": entry.campaign_id,
        "factor_id": entry.factor_id,
        "version": entry.version,
        "candidate_hash": entry.candidate_hash,
        "evidence_tier": entry.evidence_tier,
        "data_window": entry.data_window,
        "gate_policy_version": entry.gate_policy_version,
        "search_space_version": entry.search_space_version,
        "metrics_json": canonical_json(entry.metrics),
        "gate_results_json": canonical_json(entry.gate_results),
        "outcome": entry.outcome,
        "created_by": entry.created_by,
        "artifact_id": entry.artifact_id,
        "created_at": utcnow_iso(),
    }
    conn.execute("BEGIN IMMEDIATE")
    try:
        last = conn.execute("SELECT hash FROM trials ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = last["hash"] if last else GENESIS_HASH
        row["prev_hash"] = prev_hash
        row["hash"] = _row_hash(prev_hash, row)
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        cur = conn.execute(f"INSERT INTO trials ({columns}) VALUES ({placeholders})", tuple(row.values()))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    row["seq"] = cur.lastrowid
    return row


def verify_ledger(conn: sqlite3.Connection) -> dict:
    errors = []
    prev_hash = GENESIS_HASH
    rows = 0
    kinds: dict[str, list[str]] = {}
    for row in conn.execute("SELECT * FROM trials ORDER BY seq"):
        rows += 1
        data = dict(row)
        if data["prev_hash"] != prev_hash:
            errors.append({"seq": data["seq"], "error": "prev_hash mismatch"})
        if _row_hash(data["prev_hash"], data) != data["hash"]:
            errors.append({"seq": data["seq"], "error": "hash mismatch"})
        prev_hash = data["hash"]
        kinds.setdefault(data["trial_id"], []).append(data["record_kind"])
    open_trials = []
    for trial_id, seq in kinds.items():
        if seq.count("started") != 1:
            errors.append({"trial_id": trial_id, "error": f"expected one started record, found {seq.count('started')}"})
        results = [k for k in seq if k in ("completed", "failed")]
        if len(results) > 1:
            errors.append({"trial_id": trial_id, "error": "more than one result record"})
        elif not results:
            open_trials.append(trial_id)
    return {"ok": not errors, "rows": rows, "trials": len(kinds), "open_trials": open_trials, "errors": errors}


def ledger_stats(conn: sqlite3.Connection, campaign_id: str | None = None, tier: str = "dev") -> dict:
    query = "SELECT * FROM trials WHERE record_kind IN ('completed', 'failed') AND evidence_tier = ?"
    params: list = [tier]
    if campaign_id is not None:
        query += " AND campaign_id IS ?"
        params.append(campaign_id)
    outcomes: Counter = Counter()
    failure_checks: Counter = Counter()
    hashes = set()
    total = 0
    for row in conn.execute(query, params):
        total += 1
        outcomes[row["outcome"]] += 1
        hashes.add(row["candidate_hash"])
        gate_results = json.loads(row["gate_results_json"])
        for level in ("l0", "l1", "l2"):
            result = gate_results.get(level)
            if result and not result.get("passed", False):
                for check in failed_checks(result):
                    failure_checks[f"{level}.{check['name']}"] += 1
                break
    return {
        "campaign_id": campaign_id,
        "evidence_tier": tier,
        "completed_trials": total,
        "distinct_candidates": len(hashes),
        "outcomes": dict(outcomes),
        "failure_reasons": dict(failure_checks.most_common()),
    }
