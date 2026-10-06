"""Job and control timer commands."""

from __future__ import annotations

import argparse
import fcntl
from datetime import UTC, datetime, timedelta

from alphasieve.cli.registry import CommandResult, command
from alphasieve.control import jobs, scheduler
from alphasieve.errors import validation_error
from alphasieve.util import canonical_json, utcnow_iso

ALL = ("agent", "human", "system")
OPERATORS = ("human", "system")


def _submit_args(parser):
    parser.add_argument("kind", choices=["train", "risk_report", "sw_sensitivity", "local_command"])
    parser.add_argument("argv", nargs=argparse.REMAINDER)


@command("jobs submit", OPERATORS, configure=_submit_args, needs_store=True)
def cmd_jobs_submit(args, ctx):
    options = argparse.ArgumentParser(prog=f"alphasieve jobs submit {args.kind}", add_help=False)
    options.add_argument("--json", action="store_true")
    for flag in ("task", "trial", "output", "model", "tier", "cluster", "name", "idempotency-key",
                 "start", "end"):
        options.add_argument(f"--{flag}")
    for flag in ("cpus", "processes", "threads", "timeout"):
        options.add_argument(f"--{flag}", type=int)
    tokens = args.argv
    delimiter = tokens.index("--") if "--" in tokens else len(tokens)
    parsed, extra = options.parse_known_args(tokens[:delimiter])
    args.json = args.json or parsed.json
    if extra:
        raise validation_error(f"unrecognized job arguments: {extra}")
    params = {key: value for key, value in vars(parsed).items()
              if value is not None and key not in ("idempotency_key", "json")}
    if args.kind == "local_command":
        params["argv"] = tokens[delimiter + 1:] if delimiter < len(tokens) else []
    elif delimiter < len(tokens):
        raise validation_error("only local_command accepts argv after --")
    return CommandResult(data=jobs.submit(ctx.settings, ctx.conn, args.kind, params, actor=ctx.settings.role,
                                          idempotency_key=parsed.idempotency_key))


def _status_args(parser):
    parser.add_argument("--open", action="store_true", dest="open_only")
    parser.add_argument("--kind")


@command("jobs status", ALL, configure=_status_args)
def cmd_jobs_status(args, ctx):
    rows = jobs.list_jobs(ctx.conn, open_only=args.open_only, kind=args.kind)
    return CommandResult(data={"count": len(rows), "jobs": rows})


def _id_args(parser):
    parser.add_argument("id")


@command("jobs show", ALL, configure=_id_args)
def cmd_jobs_show(args, ctx):
    row = next((r for r in jobs.list_jobs(ctx.conn, limit=10000) if r["job_id"] == args.id), None)
    if row is None:
        raise validation_error(f"unknown job {args.id}")
    row["attempts"] = [dict(r) for r in ctx.conn.execute(
        "SELECT * FROM job_attempts WHERE job_id=? ORDER BY attempt", (args.id,))]
    return CommandResult(data=row)


@command("jobs reconcile", ("system",))
def cmd_jobs_reconcile(args, ctx):
    return CommandResult(data=jobs.reconcile(ctx.settings, ctx.conn))


@command("jobs resume", OPERATORS, configure=_id_args)
def cmd_jobs_resume(args, ctx):
    return CommandResult(data=jobs.resume(ctx.settings, ctx.conn, args.id, actor=ctx.settings.role))


@command("jobs cancel", OPERATORS, configure=_id_args)
def cmd_jobs_cancel(args, ctx):
    return CommandResult(data=jobs.cancel(ctx.settings, ctx.conn, args.id, actor=ctx.settings.role))


def _last_tick(conn, key):
    row = conn.execute("SELECT ts FROM events WHERE event_type=? ORDER BY seq DESC LIMIT 1", (key,)).fetchone()
    return datetime.fromisoformat(row["ts"]) if row else None


def _periodic(ctx, key, interval, function):
    from alphasieve.audit import record_event

    now = datetime.now(UTC)
    previous = _last_tick(ctx.conn, key)
    if previous and now - previous < timedelta(minutes=interval):
        return {"skipped": "interval"}
    result = function()
    record_event(ctx.conn, ctx.settings, key, command="control tick")
    return result


@command("control tick", ("system",))
def cmd_control_tick(args, ctx):
    lock_path = ctx.settings.hot_root / "control" / "tick.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return CommandResult(data={"skipped": "overlapping tick"})
        result = {"errors": []}

        def guarded(name, function):
            try:
                result[name] = function()
            except Exception as exc:  # noqa: BLE001
                result["errors"].append({"step": name, "error": repr(exc)})

        guarded("schedule", lambda: scheduler.run_due(ctx.settings, ctx.conn))
        guarded("jobs", lambda: jobs.reconcile(ctx.settings, ctx.conn))
        from alphasieve.control import health, llm, resources

        if hasattr(llm, "check_and_resume"):
            guarded("llm", lambda: llm.check_and_resume(ctx.settings, ctx.conn))
        if hasattr(resources, "write_snapshot"):
            guarded("resources", lambda: _periodic(ctx, "control_resources", 2,
                                                    lambda: resources.write_snapshot(ctx.settings)))

        def insert_alerts():
            alerts = health.system_alerts(ctx.settings, ctx.conn, datetime.now(UTC).date().isoformat())
            count = 0
            for alert in alerts:
                cursor = ctx.conn.execute(
                    "INSERT OR IGNORE INTO alerts(alert_id,kind,severity,subject,title,detail,evidence_json,"
                    "created_at,visibility) VALUES (?,?,?,?,?,?,?,?,?)",
                    (alert["alert_id"], alert["kind"], alert["severity"], alert["subject"], alert["title"],
                     alert["detail"], canonical_json(alert.get("evidence", [])), alert.get("created_at", utcnow_iso()),
                     alert.get("visibility", "public")))
                count += cursor.rowcount
            return {"evaluated": len(alerts), "inserted": count}

        guarded("alerts", lambda: _periodic(ctx, "control_alerts", 10, insert_alerts))
        return CommandResult(data=result)


def _unit_args(parser):
    parser.add_argument("unit")


@command("control unit-failed", ("system",), configure=_unit_args)
def cmd_control_unit_failed(args, ctx):
    from alphasieve.control.health import record_system_alert

    inserted = record_system_alert(ctx.settings, ctx.conn, rule="unit_failed", subject=args.unit,
                                   title=f"Systemd unit failed: {args.unit}", detail=args.unit,
                                   severity="critical", dedupe_key=utcnow_iso()[:16])
    return CommandResult(data={"unit": args.unit, "alert_inserted": inserted})


@command("control schedule", ALL)
def cmd_control_schedule(args, ctx):
    return CommandResult(data={"schedule": scheduler.list_schedule(ctx.settings, ctx.conn)})
