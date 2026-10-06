"""Persistent jobs and bounded infrastructure retries."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from alphasieve.audit import record_event
from alphasieve.control.targets import load_targets
from alphasieve.errors import validation_error
from alphasieve.util import utcnow_iso

OPEN = ("queued", "submitted", "running", "retry_wait", "paused")
BACKOFF_MINUTES = (2, 8, 30)
UNREACHABLE_GRACE = timedelta(minutes=10)


def _kind(name):
    from alphasieve.control.kinds import get_kind

    return get_kind(name)


def _row(row):
    if row is None:
        return None
    value = dict(row)
    value["params"] = json.loads(value.pop("params_json"))
    value["handle"] = json.loads(value.pop("handle_json") or "null")
    return value


def _get(conn, job_id):
    row = _row(conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())
    if row is None:
        raise validation_error(f"unknown job {job_id}")
    return row


def list_jobs(conn, *, open_only=False, kind=None, limit=200) -> list[dict]:
    clauses, values = [], []
    if open_only:
        clauses.append("status IN ('queued','submitted','running','retry_wait','paused')")
    if kind:
        clauses.append("kind=?")
        values.append(kind)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(f"SELECT * FROM jobs{where} ORDER BY submitted_at DESC LIMIT ?", (*values, limit))
    return [_row(row) for row in rows]


def job_summary(conn) -> dict:
    summary = {}
    for column, key in (("status", "by_status"), ("kind", "by_kind"), ("placement", "by_placement")):
        summary[key] = {row[0]: row[1] for row in conn.execute(
            f"SELECT {column},count(*) FROM jobs GROUP BY {column}")}
    summary["recent_failures"] = list_jobs(conn, limit=100)
    summary["recent_failures"] = [j for j in summary["recent_failures"] if j["status"] == "failed"][:10]
    return summary


def _change(settings, conn, job_id, status, **fields):
    old = _get(conn, job_id)
    values = {"status": status, **fields}
    if status in ("succeeded", "failed", "cancelled"):
        values.setdefault("finished_at", utcnow_iso())
    names = ", ".join(f"{name}=?" for name in values)
    conn.execute(f"UPDATE jobs SET {names} WHERE job_id=?", (*values.values(), job_id))
    if old["status"] != status:
        record_event(conn, settings, "job_status", command="jobs", object_type="job", object_id=job_id,
                     payload={"from": old["status"], "to": status, "attempt": fields.get("attempt", old["attempt"]),
                              "failure_class": fields.get("failure_class"), "error": fields.get("error")})


def _target(settings, job, kind):
    if kind.placement != "ray":
        return SimpleNamespace(name="local", address="local")
    clusters = load_targets(settings).clusters(need_r2=kind.need_r2)
    if not clusters:
        raise validation_error("no eligible Ray cluster")
    requested = job["params"].get("cluster")
    if requested and job["attempt"] == 0:
        return next((c for c in clusters if c.name == requested or c.address == requested),
                    SimpleNamespace(name=requested, address=requested))
    if job["target"] and job["failure_class"] == "infra":
        index = next((i for i, c in enumerate(clusters) if c.name == job["target"] or c.address == job["target"]), -1)
        return clusters[(index + 1) % len(clusters)]
    return clusters[0]


def _start(settings, conn, job_id):
    job = _get(conn, job_id)
    kind = _kind(job["kind"])
    target = _target(settings, job, kind)
    attempt = job["attempt"] + 1
    now = utcnow_iso()
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = _get(conn, job_id)
        if current["status"] not in ("queued", "retry_wait", "paused"):
            conn.execute("ROLLBACK")
            return current
        conn.execute("INSERT INTO job_attempts(job_id,attempt,target,started_at) VALUES (?,?,?,?)",
                     (job_id, attempt, target.name, now))
        _change(settings, conn, job_id, "submitted", attempt=attempt, target=target.name,
                heartbeat_at=now, failure_class=None, error=None, next_retry_at=None)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    job = _get(conn, job_id)
    ctx = SimpleNamespace(settings=settings, conn=conn, target=target)
    try:
        handle = kind.submit(ctx, job, attempt)
        if isinstance(handle, str):
            handle = {"external_id": handle}
        handle = dict(handle)
        handle.setdefault("address", target.address)
        conn.execute("UPDATE jobs SET handle_json=? WHERE job_id=?", (json.dumps(handle), job_id))
        conn.execute("UPDATE job_attempts SET external_id=? WHERE job_id=? AND attempt=?",
                     (handle.get("external_id"), job_id, attempt))
    except Exception as exc:
        _failure(settings, conn, job_id, "infra" if kind.classify(str(exc)) == "infra" else "task", str(exc))
    return _get(conn, job_id)


def submit(settings, conn, kind, params, *, actor, idempotency_key=None, trial_id=None) -> dict:
    definition = _kind(kind)
    params = dict(params)
    if idempotency_key:
        existing = conn.execute("SELECT job_id FROM jobs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if existing:
            return _get(conn, existing["job_id"])
    if hasattr(definition, "prepare"):
        params, trial_id = definition.prepare(settings, conn, params, trial_id)
    job_id = "J-" + uuid.uuid4().hex[:12]
    key = idempotency_key or job_id
    now = utcnow_iso()
    output = params.get("output") or params.get("output_ref")
    conn.execute("INSERT INTO jobs(job_id,kind,idempotency_key,placement,attempt,max_attempts,status,"
                 "input_ref,output_ref,trial_id,submitted_at,params_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (job_id, kind, key, definition.placement, 0, definition.max_attempts, "queued",
                  params.get("input_ref") or params.get("bundle") or params.get("staged_input"),
                  str(output) if output else None, trial_id, now, json.dumps(params)))
    record_event(conn, settings, "job_status", command="jobs submit", object_type="job", object_id=job_id,
                 payload={"from": None, "to": "queued", "kind": kind, "actor": actor})
    return _start(settings, conn, job_id)


def _alert(settings, conn, job):
    from alphasieve.control.health import record_system_alert

    record_system_alert(settings, conn, rule="job_failed", subject=job["job_id"],
                        title=f"Job {job['job_id']} failed", detail=job.get("error") or "",
                        severity="warning", dedupe_key=f"{job['job_id']}:{job['attempt']}")


def _record_trial(settings, conn, job, *, result=None, error=None):
    """Ledger appends own their transaction, so this runs before the job update; an existing result is kept."""
    from alphasieve.training import run as tr
    from alphasieve.training.task import parse_task

    if conn.execute("SELECT 1 FROM trials WHERE trial_id=? AND record_kind IN ('completed','failed')",
                    (job["trial_id"],)).fetchone():
        return
    if result is not None:
        tr.complete_trial(conn, settings, parse_task(result["bundle"]["task"]), job["trial_id"], result)
    else:
        task = parse_task(tr.load_bundle(job["params"]["bundle"])["task"])
        tr.fail_trial(conn, settings, task, job["trial_id"], error)


def _failure(settings, conn, job_id, failure_class, detail):
    now = utcnow_iso()
    job = _get(conn, job_id)
    if job["kind"] == "train" and failure_class == "task" and job["status"] in ("submitted", "running"):
        _record_trial(settings, conn, job, error=detail)
    conn.execute("BEGIN IMMEDIATE")
    try:
        job = _get(conn, job_id)
        if job["status"] not in ("submitted", "running"):
            conn.execute("ROLLBACK")
            return
        conn.execute("UPDATE job_attempts SET ended_at=?,outcome='failed',failure_class=?,error=?"
                     " WHERE job_id=? AND attempt=? AND ended_at IS NULL",
                     (now, failure_class, detail[:2000], job_id, job["attempt"]))
        if failure_class == "infra" and job["attempt"] < job["max_attempts"]:
            delay = BACKOFF_MINUTES[min(job["attempt"] - 1, len(BACKOFF_MINUTES) - 1)]
            retry = (datetime.now(UTC) + timedelta(minutes=delay)).isoformat()
            _change(settings, conn, job_id, "retry_wait", failure_class="infra", error=detail[:2000],
                    next_retry_at=retry)
        else:
            _change(settings, conn, job_id, "failed", failure_class=failure_class, error=detail[:2000])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    if _get(conn, job_id)["status"] == "failed":
        _alert(settings, conn, _get(conn, job_id))


def _success(settings, conn, job_id):
    job = _get(conn, job_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = _get(conn, job_id)
        if current["status"] not in ("submitted", "running"):
            conn.execute("ROLLBACK")
            return
        conn.execute("UPDATE job_attempts SET ended_at=?,outcome='succeeded' WHERE job_id=? AND attempt=?",
                     (utcnow_iso(), job_id, job["attempt"]))
        _change(settings, conn, job_id, "succeeded", heartbeat_at=utcnow_iso(), error=None, failure_class=None)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def reconcile(settings, conn) -> dict:
    result = {"checked": 0, "succeeded": 0, "failed": 0, "retried": 0, "errors": []}
    for job in list_jobs(conn, open_only=True, limit=1000):
        try:
            if job["status"] in ("queued", "retry_wait"):
                if job["status"] == "retry_wait" and job["next_retry_at"] > utcnow_iso():
                    continue
                _start(settings, conn, job["job_id"])
                result["retried"] += 1
                continue
            if job["status"] == "paused":
                continue
            if not job["handle"]:
                started = datetime.fromisoformat(job["heartbeat_at"] or job["submitted_at"])
                if datetime.now(UTC) - started >= UNREACHABLE_GRACE:
                    _failure(settings, conn, job["job_id"], "infra", "submission handle missing after grace period")
                continue
            definition = _kind(job["kind"])
            target = SimpleNamespace(name=job["target"], address=(job["handle"] or {}).get("address"))
            state, detail = definition.poll(SimpleNamespace(settings=settings, conn=conn, target=target),
                                            job, job["handle"])
            result["checked"] += 1
            state = state.lower()
            if state in ("submitted", "running", "pending"):
                _change(settings, conn, job["job_id"], "running" if state == "running" else "submitted",
                        heartbeat_at=utcnow_iso())
            elif state in ("succeeded", "success"):
                try:
                    output = definition.collect(SimpleNamespace(settings=settings, conn=conn, target=target), job)
                    if job["kind"] == "train":
                        _record_trial(settings, conn, job, result=output.get("data", output))
                except Exception as exc:
                    _failure(settings, conn, job["job_id"], "task", f"collect failed: {exc}")
                    result["failed"] += 1
                    continue
                _success(settings, conn, job["job_id"])
                result["succeeded"] += 1
            elif state in ("unreachable", "unknown"):
                last = datetime.fromisoformat(job["heartbeat_at"] or job["submitted_at"])
                if state == "unknown" or datetime.now(UTC) - last >= UNREACHABLE_GRACE:
                    _failure(settings, conn, job["job_id"], "infra", str(detail))
            elif state in ("failed", "cancelled"):
                _failure(settings, conn, job["job_id"], definition.classify(detail), str(detail))
                result["failed"] += 1
            else:
                raise ValueError(f"unexpected poll state: {state}")
        except Exception as exc:
            result["errors"].append({"job_id": job["job_id"], "error": repr(exc)})
    return result


def resume(settings, conn, job_id, *, actor) -> dict:
    job = _get(conn, job_id)
    if job["status"] not in ("failed", "paused", "retry_wait"):
        raise validation_error("only failed, paused or waiting jobs can resume")
    if job["kind"] == "train" and job["status"] == "failed" and job["failure_class"] == "task":
        raise validation_error("a task-failed training trial cannot be resumed")
    if job["attempt"] >= job["max_attempts"]:
        conn.execute("UPDATE jobs SET max_attempts=? WHERE job_id=?", (job["attempt"] + 1, job_id))
    _change(settings, conn, job_id, "queued", finished_at=None, next_retry_at=None)
    return _start(settings, conn, job_id)


def cancel(settings, conn, job_id, *, actor) -> dict:
    job = _get(conn, job_id)
    if job["status"] not in OPEN:
        return job
    if job["status"] in ("submitted", "running"):
        definition = _kind(job["kind"])
        if hasattr(definition, "stop"):
            target = SimpleNamespace(name=job["target"], address=(job["handle"] or {}).get("address"))
            definition.stop(SimpleNamespace(settings=settings, conn=conn, target=target), job, job["handle"])
    _change(settings, conn, job_id, "cancelled")
    conn.execute("UPDATE job_attempts SET ended_at=?,outcome='cancelled' WHERE job_id=? AND attempt=?"
                 " AND ended_at IS NULL", (utcnow_iso(), job_id, job["attempt"]))
    return _get(conn, job_id)
