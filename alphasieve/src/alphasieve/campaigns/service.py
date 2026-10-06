import json
import sqlite3
import uuid
from datetime import UTC, datetime

from alphasieve.audit import record_event
from alphasieve.config import Settings
from alphasieve.contracts import Campaign
from alphasieve.data.universe import universe_config
from alphasieve.errors import AlphaSieveError, not_found, permission_denied, validation_error
from alphasieve.search_space import load_search_space
from alphasieve.util import canonical_json, utcnow_iso

CAMPAIGN_TRANSITIONS = {
    "draft": {"running"},
    "running": {"paused", "concluding"},
    "paused": {"running", "concluding"},
    "concluding": {"awaiting_holdout_approval", "concluded"},
    "awaiting_holdout_approval": {"holdout_evaluated", "concluded"},
    "holdout_evaluated": {"concluded"},
}
HOLDOUT_STAGE_STATES = {
    "holdout_evaluating", "holdout_failed", "holdout_contaminated", "holdout_passed", "reviewable", "rejected",
    "needs_repair", "approved_for_shadow", "shadow_promoted", "materialized", "shadow_trained", "fresh_observing",
    "fresh_failed", "fresh_supported", "approved_for_paper", "paper_active", "retired", "rolled_back",
}
LEAK_TERMS = ("holdout", "留出", "fresh", "前瞻", "review packet", "评审", "reviewable")


def masked_state(state: str, role: str) -> str:
    return "batch_concluded" if role == "agent" and state in HOLDOUT_STAGE_STATES else state


def create_campaign(conn: sqlite3.Connection, settings: Settings, spec: Campaign) -> dict:
    space = load_search_space(settings)
    universe_config(settings, spec.universe)
    unknown_domains = [d for d in spec.domains if d not in space.domains]
    if unknown_domains:
        raise validation_error(f"unknown domains {unknown_domains}", allowed=sorted(space.domains))
    for cell in spec.cells:
        if cell.domain not in space.domains or cell.form not in space.forms or cell.scale not in space.scales:
            raise validation_error(f"cell {cell.model_dump()} is not in search space {space.version_tag}")
    if spec.horizon is None:
        spec = spec.model_copy(update={"horizon": space.horizon_for([c.domain for c in spec.cells])})
    if conn.execute("SELECT 1 FROM campaigns WHERE campaign_id = ?", (spec.campaign_id,)).fetchone():
        raise AlphaSieveError("CONFLICT", f"campaign {spec.campaign_id} already exists")
    conn.execute(
        "INSERT INTO campaigns (campaign_id, title, spec_json, status, created_by, created_at)"
        " VALUES (?, ?, ?, 'draft', ?, ?)",
        (spec.campaign_id, spec.title, canonical_json(spec.model_dump()), settings.user, utcnow_iso()),
    )
    record_event(conn, settings, "campaign.created", object_type="campaign", object_id=spec.campaign_id)
    return get_campaign(conn, spec.campaign_id)


def get_campaign(conn: sqlite3.Connection, campaign_id: str) -> dict:
    row = conn.execute("SELECT * FROM campaigns WHERE campaign_id = ?", (campaign_id,)).fetchone()
    if row is None:
        raise not_found(f"campaign {campaign_id} not found")
    out = dict(row)
    out["spec"] = Campaign(**json.loads(out.pop("spec_json")))
    out["stats"] = json.loads(out.pop("stats_json"))
    return out


def list_campaigns(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT campaign_id, title, status, created_at, started_at, concluded_at FROM campaigns ORDER BY created_at")]


def set_status(conn: sqlite3.Connection, settings: Settings, campaign_id: str, target: str,
               reason: str | None = None) -> dict:
    campaign = get_campaign(conn, campaign_id)
    current = campaign["status"]
    if target not in CAMPAIGN_TRANSITIONS.get(current, set()):
        raise AlphaSieveError("CONFLICT", f"campaign {campaign_id}: illegal transition {current} -> {target}")
    now = utcnow_iso()
    updates = {"status": target}
    if target == "running" and campaign["started_at"] is None:
        updates["started_at"] = now
    if target == "concluded":
        updates["concluded_at"] = now
    if target == "running":
        last = conn.execute("SELECT COALESCE(MAX(turn_index), 0) FROM turns WHERE campaign_id = ?",
                            (campaign_id,)).fetchone()[0]
        prior = campaign["stats"].get("paused_seconds", 0.0)
        paused_at = campaign["stats"].get("paused_at")
        if paused_at:
            prior += max(0.0, (datetime.fromisoformat(now) - datetime.fromisoformat(paused_at)).total_seconds())
        updates["stats_json"] = canonical_json({**campaign["stats"], "resumed_after_turn": last,
                                                 "pause_reason": None, "paused_at": None,
                                                 "paused_seconds": prior})
    if target == "paused" and reason:
        updates["stats_json"] = canonical_json({**campaign["stats"], "pause_reason": reason, "paused_at": now})
    sets = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(f"UPDATE campaigns SET {sets} WHERE campaign_id = ?", (*updates.values(), campaign_id))
    record_event(conn, settings, "campaign.status_changed", object_type="campaign", object_id=campaign_id,
                 payload={"from": current, "to": target, "reason": reason})
    return get_campaign(conn, campaign_id)


def update_stats(conn: sqlite3.Connection, campaign_id: str, **values) -> None:
    stats = get_campaign(conn, campaign_id)["stats"]
    stats.update(values)
    conn.execute("UPDATE campaigns SET stats_json = ? WHERE campaign_id = ?", (canonical_json(stats), campaign_id))


def completed_trials(conn: sqlite3.Connection, campaign_id: str, tier: str = "dev") -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM trials WHERE campaign_id = ? AND evidence_tier = ? AND record_kind IN ('completed', 'failed')"
        " ORDER BY seq", (campaign_id, tier)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["metrics"] = json.loads(d.pop("metrics_json"))
        d["gate_results"] = json.loads(d.pop("gate_results_json"))
        out.append(d)
    return out


def ensure_can_evaluate(conn: sqlite3.Connection, settings: Settings, campaign_id: str | None) -> Campaign | None:
    if campaign_id is None:
        if settings.role == "agent":
            raise permission_denied("agent evaluations must belong to a campaign (set ALPHASIEVE_CAMPAIGN)")
        return None
    campaign = get_campaign(conn, campaign_id)
    spec: Campaign = campaign["spec"]
    if settings.role == "agent" and campaign["status"] != "running":
        raise AlphaSieveError("CONFLICT", f"campaign {campaign_id} is {campaign['status']}, not running")
    used = started_trials(conn, campaign_id)
    if used >= spec.budgets.trials:
        raise AlphaSieveError("BUDGET_EXHAUSTED", f"campaign {campaign_id} used its trial budget {spec.budgets.trials}")
    if settings.role == "agent" and settings.turn and settings.turn_allowance is not None:
        if started_trials(conn, campaign_id, settings.turn) >= settings.turn_allowance:
            raise AlphaSieveError("BUDGET_EXHAUSTED",
                                  "this turn's trial allowance is used up; summarize and end the turn")
    return spec


def started_trials(conn: sqlite3.Connection, campaign_id: str, turn_id: str | None = None) -> int:
    """Trials begun (including in flight), so concurrent turns cannot overrun a budget."""
    query = "SELECT COUNT(*) FROM trials WHERE campaign_id = ? AND evidence_tier = 'dev' AND record_kind = 'started'"
    params: list = [campaign_id]
    if turn_id is not None:
        query += " AND turn_id = ?"
        params.append(turn_id)
    return conn.execute(query, params).fetchone()[0]


def domain_violations(spec: Campaign | None, terminals: set[str], settings: Settings) -> list[str]:
    if spec is None:
        return []
    space = load_search_space(settings)
    return sorted(t for t in terminals if space.domain_of(t) not in spec.domains)


def elapsed_hours(campaign: dict) -> float:
    if not campaign["started_at"]:
        return 0.0
    started = datetime.fromisoformat(campaign["started_at"])
    return (datetime.now(UTC) - started).total_seconds() / 3600


def add_directive(conn: sqlite3.Connection, settings: Settings, campaign_id: str, kind: str, content: str) -> dict:
    if kind not in ("prioritize", "forbid", "hint", "answer"):
        raise validation_error("kind must be prioritize, forbid, hint or answer")
    get_campaign(conn, campaign_id)
    lowered = content.lower()
    hits = [t for t in LEAK_TERMS if t in lowered]
    referenced = [r["factor_id"] for r in conn.execute("SELECT DISTINCT factor_id, state FROM factor_specs")
                  if r["factor_id"].lower() in lowered and r["state"] in HOLDOUT_STAGE_STATES]
    if hits or referenced:
        raise validation_error("directive may not reference holdout, fresh or review information",
                               terms=hits, factors=referenced)
    directive_id = f"D-{uuid.uuid4().hex[:10]}"
    conn.execute(
        "INSERT INTO directives (directive_id, campaign_id, kind, content, created_by, created_at, status)"
        " VALUES (?, ?, ?, ?, ?, ?, 'pending')",
        (directive_id, campaign_id, kind, content, settings.user, utcnow_iso()),
    )
    record_event(conn, settings, "directive.created", object_type="campaign", object_id=campaign_id,
                 payload={"directive_id": directive_id, "kind": kind})
    return {"directive_id": directive_id, "kind": kind, "content": content, "status": "pending"}


def directives(conn: sqlite3.Connection, campaign_id: str, status: str | None = None) -> list[dict]:
    query = "SELECT * FROM directives WHERE campaign_id = ?"
    params: list = [campaign_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    return [dict(r) for r in conn.execute(query + " ORDER BY created_at", params)]


def consume_directives(conn: sqlite3.Connection, campaign_id: str, turn_id: str) -> int:
    cur = conn.execute("UPDATE directives SET status = 'consumed', consumed_turn_id = ? WHERE campaign_id = ?"
                       " AND status = 'pending' AND kind != 'forbid'", (turn_id, campaign_id))
    conn.execute("UPDATE directives SET consumed_turn_id = COALESCE(consumed_turn_id, ?) WHERE campaign_id = ?"
                 " AND status = 'pending' AND kind = 'forbid'", (turn_id, campaign_id))
    return cur.rowcount


def create_request(conn: sqlite3.Connection, settings: Settings, campaign_id: str, kind: str, content: str) -> dict:
    if kind not in ("data", "question", "scope"):
        raise validation_error("kind must be data, question or scope")
    get_campaign(conn, campaign_id)
    request_id = f"R-{uuid.uuid4().hex[:10]}"
    conn.execute("INSERT INTO agent_requests (request_id, campaign_id, kind, content, status, created_at)"
                 " VALUES (?, ?, ?, ?, 'open', ?)", (request_id, campaign_id, kind, content, utcnow_iso()))
    record_event(conn, settings, "inbox.created", object_type="agent_request", object_id=request_id,
                 payload={"campaign_id": campaign_id, "kind": kind})
    return {"request_id": request_id, "status": "open"}


def respond_request(conn: sqlite3.Connection, settings: Settings, request_id: str, decision: str,
                    response: str) -> dict:
    from alphasieve.approvals import record_applied

    row = conn.execute("SELECT * FROM agent_requests WHERE request_id = ?", (request_id,)).fetchone()
    if row is None:
        raise not_found(f"request {request_id} not found")
    if row["status"] != "open":
        raise AlphaSieveError("CONFLICT", f"request {request_id} is already {row['status']}")
    if decision not in ("approved", "rejected", "answered") or not response.strip():
        raise validation_error("decision must be approved, rejected or answered, with a non-empty response")
    conn.execute("UPDATE agent_requests SET status = ?, response = ?, responded_by = ?, responded_at = ?"
                 " WHERE request_id = ?", (decision, response, settings.user, utcnow_iso(), request_id))
    add_directive(conn, settings, row["campaign_id"], "answer", f"[{request_id} {decision}] {response}")
    record_event(conn, settings, "inbox.resolved", object_type="agent_request", object_id=request_id,
                 payload={"decision": decision})
    record_applied(conn, "request", request_id, decision)
    return {"request_id": request_id, "status": decision}
