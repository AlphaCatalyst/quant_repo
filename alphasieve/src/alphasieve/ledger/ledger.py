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


STRATEGY_COLUMNS = ("layer", "scope")


def _row_hash(prev_hash: str, row: dict) -> str:
    cols = HASHED_COLUMNS + (STRATEGY_COLUMNS if row.get("layer", "factor") != "factor" else ())
    payload = canonical_json({col: row[col] for col in cols})
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
        "turn_id": entry.turn_id,
        "layer": entry.layer,
        "scope": entry.scope,
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
    seqs: dict[str, list[int]] = {}
    voided: dict[str, set[int]] = {}
    for row in conn.execute("SELECT * FROM trials ORDER BY seq"):
        rows += 1
        data = dict(row)
        if data["prev_hash"] != prev_hash:
            errors.append({"seq": data["seq"], "error": "prev_hash mismatch"})
        if _row_hash(data["prev_hash"], data) != data["hash"]:
            errors.append({"seq": data["seq"], "error": "hash mismatch"})
        prev_hash = data["hash"]
        if data["record_kind"] == "void":
            voided.setdefault(data["trial_id"], set()).add(json.loads(data["metrics_json"]).get("voids_seq"))
        else:
            kinds.setdefault(data["trial_id"], []).append(data["record_kind"])
            seqs.setdefault(data["trial_id"], []).append(data["seq"])
    open_trials = []
    for trial_id, seq in kinds.items():
        if seq.count("started") != 1:
            errors.append({"trial_id": trial_id, "error": f"expected one started record, found {seq.count('started')}"})
        results = [k for k, n in zip(seq, seqs[trial_id], strict=True)
                   if k in ("completed", "failed") and n not in voided.get(trial_id, set())]
        if len(results) > 1:
            errors.append({"trial_id": trial_id, "error": "more than one result record"})
        elif not results:
            open_trials.append(trial_id)
    return {"ok": not errors, "rows": rows, "trials": len(kinds), "open_trials": open_trials, "errors": errors}


def ledger_stats(conn: sqlite3.Connection, campaign_id: str | None = None, tier: str = "dev",
                 layer: str = "factor") -> dict:
    query = "SELECT * FROM trials WHERE record_kind IN ('completed', 'failed') AND evidence_tier = ? AND layer = ?"
    params: list = [tier, layer]
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
        "layer": layer,
        "evidence_tier": tier,
        "completed_trials": total,
        "distinct_candidates": len(hashes),
        "outcomes": dict(outcomes),
        "failure_reasons": dict(failure_checks.most_common()),
    }


def strategy_trial_count(conn: sqlite3.Connection, scope: str, tier: str = "dev") -> int:
    """Strategy-layer trials started for one mandate: the count behind its search discount (15 §4 P-5)."""
    return conn.execute("SELECT COUNT(*) FROM trials WHERE layer = 'strategy' AND scope = ? AND evidence_tier = ?"
                        " AND record_kind = 'started'", (scope, tier)).fetchone()[0]


def void_duplicate_result(conn: sqlite3.Connection, trial_id: str, seq: int, reason: str, created_by: str) -> dict:
    """Append a ``void`` record for a duplicated result row; the row stays in the chain but no longer counts.

    Only a duplicate can be voided: the trial must keep at least one other result record that is not voided.
    """
    rows = [dict(r) for r in conn.execute("SELECT * FROM trials WHERE trial_id = ? ORDER BY seq", (trial_id,))]
    voided = {json.loads(r["metrics_json"]).get("voids_seq") for r in rows if r["record_kind"] == "void"}
    results = [r for r in rows if r["record_kind"] in ("completed", "failed") and r["seq"] not in voided]
    target = next((r for r in results if r["seq"] == seq), None)
    if target is None:
        raise ValueError(f"seq {seq} is not an active result record of trial {trial_id}")
    if len(results) < 2:
        raise ValueError("only a duplicated result can be voided; this is the trial's only result")
    entry = TrialLedgerEntry(trial_id=trial_id, record_kind="void", campaign_id=target["campaign_id"],
                             candidate_hash=target["candidate_hash"], evidence_tier=target["evidence_tier"],
                             metrics={"voids_seq": seq, "reason": reason}, outcome="void_duplicate",
                             created_by=created_by, layer=target.get("layer") or "factor", scope=target.get("scope"))
    return append_trial(conn, entry)
