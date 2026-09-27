"""Unattended campaign loop: one agent turn at a time until a stop condition, then L3 and a holdout request."""

import fcntl
import json
import math
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from alphasieve.agents import integrity, workspace
from alphasieve.agents.executors import TurnContext, TurnResult, make_executor
from alphasieve.audit import record_event
from alphasieve.campaigns import lifecycle, memory, service, stats
from alphasieve.config import Settings
from alphasieve.errors import AlphaSieveError
from alphasieve.state import connect
from alphasieve.util import canonical_json, utcnow_iso

PROMPT = (
    "You are running turn {turn} of AlphaSieve campaign {campaign}. Read program.md first, then brief.md, "
    "memory.md and directives.md in the current directory. Work according to program.md, stay within this turn's "
    "allowance of {allowance} evaluations, and finish with the SUMMARY / INSIGHTS message described there."
)
MAX_INSIGHTS_PER_TURN = 5


def turn_allowance(budget: dict) -> int:
    remaining_trials = budget["trials"]["budget"] - budget["trials"]["used"]
    remaining_turns = max(1, budget["turns"]["budget"] - budget["turns"]["used"])
    return max(3, min(10, math.ceil(2 * remaining_trials / remaining_turns)))


def parse_insights(summary: str) -> list[str]:
    match = re.search(r"INSIGHTS:\s*(.*)", summary or "", re.S)
    if not match:
        return []
    lines = [ln.strip()[2:].strip() for ln in match.group(1).splitlines() if ln.strip().startswith(("- ", "* "))]
    return [ln for ln in lines if ln][:MAX_INSIGHTS_PER_TURN]


def _max_seq(conn) -> int:
    return conn.execute("SELECT COALESCE(MAX(seq), 0) FROM trials").fetchone()[0]


def _max_event(conn) -> int:
    return conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]


def _new_robust(conn, campaign_id: str, since_seq: int) -> int:
    before = {r[0] for r in conn.execute(
        "SELECT candidate_hash FROM trials WHERE campaign_id = ? AND seq <= ? AND outcome = 'robust_passed'",
        (campaign_id, since_seq))}
    after = {r[0] for r in conn.execute(
        "SELECT candidate_hash FROM trials WHERE campaign_id = ? AND seq > ? AND outcome = 'robust_passed'"
        " AND evidence_tier = 'dev'", (campaign_id, since_seq))}
    return len(after - before)


def write_report(settings: Settings, conn, campaign_id: str) -> str:
    campaign = service.get_campaign(conn, campaign_id)
    b = stats.budget_status(conn, campaign_id)
    f = stats.funnel(conn, campaign_id)
    today = datetime.now(UTC).date().isoformat()
    turns = [dict(r) for r in conn.execute(
        "SELECT turn_index, harness, model, status, trials_before, trials_after, robust_passed_new, summary, error,"
        " started_at FROM turns WHERE campaign_id = ? ORDER BY turn_index", (campaign_id,))]
    lines = [f"# {campaign_id} status ({utcnow_iso()})", "", f"- status: {campaign['status']}",
             f"- trials: {b['trials']['used']}/{b['trials']['budget']}, turns: {b['turns']['used']}/"
             f"{b['turns']['budget']}, hours: {b['hours']['used']}/{b['hours']['budget']}",
             f"- usage: {b['usage']['input']} input tokens, {b['usage']['output']} output tokens,"
             f" ${b['usage']['cost_usd']:.2f} reported cost",
             "- funnel: " + ", ".join(f"{k} {v}" for k, v in f["counts"].items()), "", "## Turns today", ""]
    for t in turns:
        if (t["started_at"] or "").startswith(today):
            lines.append(f"- turn {t['turn_index']} {t['harness']}/{t['model']}: {t['status']}, trials"
                         f" {t['trials_before']}->{t['trials_after']}, new L2 {t['robust_passed_new']}"
                         + (f", error: {t['error'][:160]}" if t["error"] else ""))
    lines += ["", "## Memory", "", memory.render(conn, campaign_id)]
    text = "\n".join(lines) + "\n"
    out_dir = settings.reports_dir / campaign_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"daily-{today}.md").write_text(text, encoding="utf-8")
    (out_dir / "status.md").write_text(text, encoding="utf-8")
    return str(out_dir / f"daily-{today}.md")


def run_turn(settings: Settings, conn, campaign_id: str, executor_for: Callable) -> dict:
    campaign = service.get_campaign(conn, campaign_id)
    spec = campaign["spec"]
    budget = stats.budget_status(conn, campaign_id)
    index = budget["turns"]["used"] + 1
    slot = spec.agents[(index - 1) % len(spec.agents)]
    allowance = turn_allowance(budget)
    turn_id = f"{campaign_id}-t{index:03d}"
    ws = workspace.prepare(settings, conn, campaign_id, index, allowance)
    transcript = settings.transcripts_dir / campaign_id / f"{turn_id}.jsonl"
    since_seq, since_event = _max_seq(conn), _max_event(conn)
    guard = integrity.snapshot(conn, settings)
    trials_before = budget["trials"]["used"]
    conn.execute("INSERT INTO turns (turn_id, campaign_id, turn_index, harness, model, status, started_at,"
                 " transcript_path, prompt_version, trials_before) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)",
                 (turn_id, campaign_id, index, slot.harness, slot.model, utcnow_iso(), str(transcript), "v1",
                  trials_before))
    service.consume_directives(conn, campaign_id, turn_id)
    ctx = TurnContext(campaign_id=campaign_id, turn_id=turn_id, turn_index=index, workspace=ws,
                      prompt=PROMPT.format(turn=index, campaign=campaign_id, allowance=allowance), model=slot.model,
                      effort=slot.effort, timeout_s=spec.budgets.turn_minutes * 60, transcript_path=transcript,
                      trial_ceiling=trials_before + allowance)
    try:
        result: TurnResult = executor_for(slot.harness).run(ctx)
    except Exception as exc:  # noqa: BLE001
        result = TurnResult(status="failed", error=f"{type(exc).__name__}: {exc}")
    findings = integrity.compare(guard, integrity.snapshot(conn, settings))
    findings += integrity.scan_commands(result.commands, result.reads, ws)
    findings += integrity.agent_events_with_other_roles(conn, since_event, turn_id)
    trials_after = len(service.completed_trials(conn, campaign_id))
    new_robust = _new_robust(conn, campaign_id, since_seq)
    status = "failed" if result.status in ("failed", "timeout") and trials_after == trials_before else "completed"
    error = result.error
    if findings:
        status, error = "integrity_violation", "; ".join(findings)[:2000]
    conn.execute("UPDATE turns SET status = ?, ended_at = ?, trials_after = ?, robust_passed_new = ?, usage_json = ?,"
                 " summary = ?, error = ? WHERE turn_id = ?",
                 (status, utcnow_iso(), trials_after, new_robust, canonical_json(result.usage),
                  (result.summary or "")[:8000], error, turn_id))
    if status != "integrity_violation":
        for text in parse_insights(result.summary):
            memory.add_insight(conn, campaign_id, text, turn_id)
    workspace.commit(ws, f"turn {index} ({slot.harness}/{slot.model}): {status}")
    record_event(conn, settings, "turn.finished", object_type="campaign", object_id=campaign_id,
                 payload={"turn_id": turn_id, "status": status, "harness_status": result.status,
                          "trials": trials_after - trials_before, "new_robust": new_robust})
    if findings:
        service.set_status(conn, settings, campaign_id, "paused", "integrity violation")
        record_event(conn, settings, "integrity.violation", status="error", object_type="campaign",
                     object_id=campaign_id, payload={"turn_id": turn_id, "findings": findings[:20]})
    return {"turn_id": turn_id, "status": status, "harness_status": result.status,
            "trials": trials_after - trials_before, "new_robust": new_robust, "error": error}


def run_campaign(settings: Settings, campaign_id: str, max_turns: int | None = None,
                 executor_for: Callable | None = None) -> dict:
    settings = replace(settings, role="system") if settings.role == "agent" else settings
    lock_dir = settings.hot_root / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock = open(lock_dir / f"{campaign_id}.lock", "w")  # noqa: SIM115
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise AlphaSieveError("CONFLICT", f"an orchestrator is already running campaign {campaign_id}") from None
    executors: dict[str, object] = {}

    def default_executor(harness: str):
        if harness not in executors:
            executors[harness] = make_executor(settings, harness)
        return executors[harness]

    executor_for = executor_for or default_executor
    conn = connect(settings.state_db)
    turns, outcome = [], None
    try:
        while True:
            campaign = service.get_campaign(conn, campaign_id)
            if campaign["status"] != "running":
                outcome = {"stopped": f"campaign is {campaign['status']}"}
                break
            reason = stats.stop_reason(conn, campaign_id)
            if reason:
                outcome = {"concluded": reason, **lifecycle.conclude(conn, settings, campaign_id, reason)}
                break
            b = stats.budget_status(conn, campaign_id)
            if b["failed_turns_streak"]["current"] >= b["failed_turns_streak"]["limit"]:
                service.set_status(conn, settings, campaign_id, "paused", "consecutive failed turns")
                outcome = {"paused": "consecutive failed turns"}
                break
            if max_turns is not None and len(turns) >= max_turns:
                outcome = {"stopped": "max_turns for this run reached"}
                break
            turns.append(run_turn(settings, conn, campaign_id, executor_for))
            write_report(settings, conn, campaign_id)
        write_report(settings, conn, campaign_id)
        return {"campaign_id": campaign_id, "turns": turns, "outcome": json.loads(json.dumps(outcome, default=str))}
    finally:
        conn.close()
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
