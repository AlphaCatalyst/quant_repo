import sqlite3

from alphasieve.audit import record_event
from alphasieve.config import Settings
from alphasieve.errors import AlphaSieveError

TRANSITIONS: dict[str, set[str]] = {
    "draft": {"validating"},
    "validating": {"validation_failed", "validated"},
    "validated": {"evaluating"},
    "evaluating": {"evaluation_failed", "evaluated"},
    "evaluated": {"robust_evaluating"},
    "robust_evaluating": {"robust_failed", "robust_passed"},
    "robust_passed": {"ledger_failed", "ledger_gated"},
    "ledger_gated": {"shortlist_locked"},
    "shortlist_locked": {"holdout_evaluating"},
    "holdout_evaluating": {"holdout_failed", "holdout_contaminated", "holdout_passed"},
    "holdout_passed": {"reviewable"},
    "reviewable": {"rejected", "needs_repair", "approved_for_shadow"},
    "approved_for_shadow": {"shadow_promoted"},
    "shadow_promoted": {"materialized"},
    "materialized": {"shadow_trained"},
    "shadow_trained": {"fresh_observing"},
    "fresh_observing": {"fresh_failed", "fresh_supported"},
    "fresh_supported": {"approved_for_paper", "retired"},
    "approved_for_paper": {"paper_active"},
    "paper_active": {"retired", "rolled_back"},
}
TERMINAL = {"validation_failed", "evaluation_failed", "robust_failed", "ledger_failed", "holdout_failed",
            "holdout_contaminated", "rejected", "needs_repair", "fresh_failed", "retired", "rolled_back"}


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


def transition(conn: sqlite3.Connection, settings: Settings, factor_id: str, version: int, target: str,
               trial_id: str | None = None, reason: str | None = None) -> None:
    row = conn.execute("SELECT state FROM factor_specs WHERE factor_id = ? AND version = ?",
                       (factor_id, version)).fetchone()
    if row is None:
        raise AlphaSieveError("NOT_FOUND", f"factor {factor_id}@{version} not found")
    current = row["state"]
    if not can_transition(current, target):
        raise AlphaSieveError("CONFLICT", f"illegal transition {current} -> {target}",
                              {"factor_id": factor_id, "version": version})
    conn.execute("UPDATE factor_specs SET state = ? WHERE factor_id = ? AND version = ?", (target, factor_id, version))
    record_event(conn, settings, "factor.state_changed", object_type="factor", object_id=f"{factor_id}@{version}",
                 payload={"from": current, "to": target, "trial_id": trial_id, "reason": reason})


def advance(conn, settings, factor_id: str, version: int, path: list[str], trial_id: str | None = None) -> None:
    for target in path:
        transition(conn, settings, factor_id, version, target, trial_id)
