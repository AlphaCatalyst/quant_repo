"""Persistent, minute-tick scheduling through the local job kind."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import yaml

from alphasieve.control import jobs
from alphasieve.errors import validation_error

ZONE = ZoneInfo("Asia/Shanghai")
OPEN = ("queued", "submitted", "running", "retry_wait", "paused")
# A newly registered entry starts at its next slot instead of backfilling the last one.
FIRST_RUN_GRACE = timedelta(minutes=15)
_DAILY = re.compile(r"daily ([01]\d|2[0-3]):([0-5]\d) (mon-sat|mon-fri|all)\Z")
_EVERY = re.compile(r"every ([1-9]\d*)m\Z")
_HOURLY = re.compile(r"hourly :([0-5]\d)\Z")
_DAYS = {"mon-sat": set(range(6)), "mon-fri": set(range(5)), "all": set(range(7))}


def _entries(settings) -> list[dict]:
    path = settings.config_dir / "control" / "schedule.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("version") != 1 or not isinstance(data.get("entries"), list):
        raise validation_error("control schedule requires version 1 and entries list", path=str(path))
    names = set()
    entries = []
    for item in data["entries"]:
        if not isinstance(item, dict):
            raise validation_error("schedule entry must be a mapping")
        name, spec, command = item.get("name"), item.get("spec"), item.get("command")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name) or name in names:
            raise validation_error(f"invalid or duplicate schedule name: {name!r}")
        if not isinstance(spec, str) or not (_DAILY.fullmatch(spec) or _EVERY.fullmatch(spec)
                                             or _HOURLY.fullmatch(spec)):
            raise validation_error(f"invalid schedule spec for {name}: {spec!r}")
        if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg for arg in command):
            raise validation_error(f"schedule command for {name} must be nonempty argv")
        timeout = item.get("timeout")
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 86400:
            raise validation_error(f"schedule timeout for {name} must be 1..86400 seconds")
        if not isinstance(item.get("enabled"), bool):
            raise validation_error(f"schedule enabled for {name} must be boolean")
        if item.get("concurrency", "skip") != "skip":
            raise validation_error(f"schedule concurrency for {name} must be skip")
        env = item.get("env", {})
        if not isinstance(env, dict) or not all(isinstance(k, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", k)
                                                and isinstance(v, str) for k, v in env.items()):
            raise validation_error(f"schedule env for {name} must map UPPER_CASE names to strings")
        entries.append(item)
        names.add(name)
    for item in entries:
        if item.get("after") and item["after"] not in names:
            raise validation_error(f"unknown schedule dependency: {item['after']}")
    return entries


def _due_at(spec: str, now: datetime) -> datetime:
    """Return latest scheduled slot at or before now, in Shanghai time."""
    local = now.astimezone(ZONE).replace(second=0, microsecond=0)
    if match := _EVERY.fullmatch(spec):
        minutes = int(match[1])
        epoch_minute = int(local.timestamp() // 60)
        return datetime.fromtimestamp((epoch_minute // minutes) * minutes * 60, ZONE)
    if match := _HOURLY.fullmatch(spec):
        slot = local.replace(minute=int(match[1]))
        return slot if slot <= local else slot - timedelta(hours=1)
    match = _DAILY.fullmatch(spec)
    assert match is not None
    hour, minute, days = int(match[1]), int(match[2]), _DAYS[match[3]]
    for days_back in range(8):
        day = (local - timedelta(days=days_back)).date()
        slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZONE)
        if slot.weekday() in days and slot <= local:
            return slot
    raise AssertionError("daily schedule has no due day")


def _next_at(spec: str, now: datetime) -> datetime:
    current = _due_at(spec, now)
    if match := _EVERY.fullmatch(spec):
        return current + timedelta(minutes=int(match[1]))
    if _HOURLY.fullmatch(spec):
        return current + timedelta(hours=1)
    match = _DAILY.fullmatch(spec)
    assert match is not None
    days = _DAYS[match[3]]
    for days_ahead in range(1, 9):
        slot = current + timedelta(days=days_ahead)
        if slot.weekday() in days:
            return slot
    raise AssertionError("daily schedule has no next day")


def _latest(conn, name: str):
    return conn.execute("SELECT job_id, idempotency_key, status, submitted_at FROM jobs "
                        "WHERE idempotency_key LIKE ? ORDER BY idempotency_key DESC LIMIT 1",
                        (f"schedule:{name}:%",)).fetchone()


def list_schedule(settings, conn) -> list[dict]:
    now = datetime.now(UTC)
    result = []
    for entry in _entries(settings):
        last = _latest(conn, entry["name"])
        result.append({"name": entry["name"], "spec": entry["spec"], "command": entry["command"],
                       "placement": "local", "enabled": entry["enabled"],
                       "last_run": last["submitted_at"] if last else None,
                       "last_status": last["status"] if last else None,
                       "next_due": _next_at(entry["spec"], now).isoformat()})
    return result


def run_due(settings, conn, submit_fn=None, *, now: datetime | None = None) -> dict:
    """Submit at most one missed slot per entry; one entry's error does not block another."""
    if submit_fn is None:
        submit_fn = jobs.submit
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise validation_error("schedule tick requires timezone-aware now")
    result = {"submitted": [], "skipped": [], "errors": []}
    for entry in _entries(settings):
        name = entry["name"]
        if not entry["enabled"]:
            result["skipped"].append({"name": name, "reason": "disabled"})
            continue
        slot = _due_at(entry["spec"], now)
        key = f"schedule:{name}:{slot.isoformat()}"
        last = _latest(conn, name)
        if last is None and now - slot > FIRST_RUN_GRACE:
            result["skipped"].append({"name": name, "reason": "no_history_before_first_slot"})
            continue
        if last and last["idempotency_key"] == key:
            result["skipped"].append({"name": name, "reason": "already_submitted"})
            continue
        if last and last["status"] in OPEN:
            result["skipped"].append({"name": name, "reason": "previous_open"})
            continue
        dependency = entry.get("after")
        if dependency:
            prior = _latest(conn, dependency)
            prior_slot = (datetime.fromisoformat(prior["idempotency_key"][len(f"schedule:{dependency}:"):])
                          if prior else None)
            if not prior or prior["status"] != "succeeded" or prior_slot.date() != slot.date():
                result["skipped"].append({"name": name, "reason": "dependency_incomplete"})
                continue
        try:
            job = submit_fn(settings, conn, "local_command",
                            {"name": name, "argv": entry["command"], "timeout": entry["timeout"],
                             "env": entry.get("env", {}), "schedule_slot": slot.isoformat()},
                            actor="system", idempotency_key=key)
            result["submitted"].append({"name": name, "job_id": job["job_id"], "slot": slot.isoformat()})
        except Exception as exc:  # one broken entry must not block other scheduled work
            result["errors"].append({"name": name, "error": repr(exc)})
    return result
