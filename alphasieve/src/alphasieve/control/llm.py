"""Codex load balancer availability and interrupted agent recovery."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from alphasieve.audit import record_event
from alphasieve.control.targets import load_targets
from alphasieve.errors import AlphaSieveError
from alphasieve.state import connect
from alphasieve.util import utcnow_iso

DEFAULT_STATE = {"available": None, "paused_since": None, "consecutive_failures": 0, "last_probe": None}


class LlmUnavailable(AlphaSieveError):
    def __init__(self, message: str = "codex-lb is unavailable", details: dict | None = None):
        super().__init__("LLM_UNAVAILABLE", message, details)


def _path(settings) -> Path:
    return settings.hot_root / "control" / "llm.json"


def state(settings) -> dict:
    path = _path(settings)
    if not path.exists():
        return dict(DEFAULT_STATE)
    return {**DEFAULT_STATE, **json.loads(path.read_text(encoding="utf-8"))}


def _write(settings, value: dict) -> None:
    path = _path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".llm-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def _locked(settings):
    path = _path(settings).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def probe(settings, timeout: float = 3) -> dict:
    endpoint = next((e for e in load_targets(settings).llm_endpoints if e.name == "codex-lb"), None)
    if endpoint is None:
        raise ValueError("codex-lb endpoint is not configured")
    start = time.monotonic()
    error = None
    try:
        with urllib.request.urlopen(endpoint.url, timeout=timeout) as response:
            if response.status >= 400:
                error = f"HTTP {response.status}"
    except (OSError, urllib.error.URLError) as exc:
        error = str(exc)
    return {"reachable": error is None, "latency_ms": round((time.monotonic() - start) * 1000, 2),
            "checked_at": utcnow_iso(), "error": error}


def _alert(settings, conn, *, recovered: bool, outage_start: str) -> None:
    from alphasieve.control.health import record_system_alert

    record_system_alert(
        settings, conn, rule="llm-unavailable", subject="codex-lb",
        title="codex-lb 已恢复" if recovered else "codex-lb 不可达",
        detail="模型服务已恢复，正在续跑中断的 agent 工作。" if recovered else "连续两次探测失败，agent 工作已暂停。",
        severity="info" if recovered else "warning",
        dedupe_key=f"{outage_start}:recovered" if recovered else outage_start,
    )


def _observe(settings, result: dict, conn=None) -> tuple[dict, bool]:
    """Update probe streak; return state and whether this probe ended an outage."""
    with _locked(settings):
        current = state(settings)
        was_paused = bool(current["paused_since"])
        if result["reachable"]:
            updated = {"available": True, "paused_since": None, "consecutive_failures": 0, "last_probe": result}
        else:
            failures = current["consecutive_failures"] + 1
            updated = {"available": False, "paused_since": current["paused_since"] or
                       (result["checked_at"] if failures >= 2 else None),
                       "consecutive_failures": failures, "last_probe": result}
        _write(settings, updated)
        changed = bool(updated["paused_since"]) != was_paused
        if changed:
            own_conn = conn is None
            db = conn or connect(settings.state_db)
            try:
                event = "llm.resumed" if result["reachable"] else "llm.paused"
                record_event(db, settings, event, object_type="llm", object_id="codex-lb",
                             payload={"outage_start": current["paused_since"] or updated["paused_since"],
                                      "probe": result})
                _alert(settings, db, recovered=result["reachable"],
                       outage_start=current["paused_since"] or updated["paused_since"])
                if own_conn:
                    db.commit()
            finally:
                if own_conn:
                    db.close()
        return updated, was_paused and result["reachable"]


def require_available(settings) -> None:
    if state(settings)["paused_since"]:
        raise LlmUnavailable("codex-lb is paused")
    result = probe(settings)
    _observe(settings, result)
    if not result["reachable"]:
        raise LlmUnavailable(details=result)


def _systemctl_start(campaign_id: str) -> None:
    subprocess.run(["systemctl", "start", f"alphasieve-orchestrator@{campaign_id}"], check=True,
                   timeout=15, capture_output=True, text=True)


def _resume_thesis(settings, conn, *, codex_reachable: bool) -> list[str]:
    from alphasieve.thesis.agent_run import resume_interrupted

    rows = conn.execute("SELECT object_id, payload_json FROM events WHERE event_type = 'thesis.run' ORDER BY seq DESC")
    seen = set()
    resumed_ids = []
    for row in rows:
        run_id = row["object_id"]
        if run_id in seen:
            continue
        seen.add(run_id)
        payload = json.loads(row["payload_json"])
        if payload.get("status") != "interrupted":
            continue
        if payload.get("harness") == "codex" and not codex_reachable:
            continue
        conn.commit()
        resumed = resume_interrupted(settings, run_id)
        if resumed is not None:
            resumed_ids.append(run_id)
    return resumed_ids


def check_and_resume(settings, conn, *, runner=None, max_concurrency: int | None = None) -> dict:
    """Probe once, then resume eligible campaigns and thesis runs after recovery."""
    result = probe(settings)
    current, recovered = _observe(settings, result, conn)
    output = {"probe": result, "state": current, "campaigns": [], "thesis_runs": []}

    # Retry pending work on later ticks too, for example after a full concurrency slot.
    from alphasieve.campaigns import service

    start = runner or _systemctl_start
    limit = (max_concurrency if max_concurrency is not None
             else int(os.environ.get("ALPHASIEVE_LLM_RESUME_CONCURRENCY", "2")))
    running = conn.execute("SELECT COUNT(*) FROM campaigns WHERE status = 'running'").fetchone()[0]
    rows = conn.execute("SELECT campaign_id, stats_json FROM campaigns WHERE status = 'paused' ORDER BY created_at")
    for row in rows:
        if running >= limit:
            break
        if json.loads(row["stats_json"]).get("pause_reason") != "llm_unavailable":
            continue
        campaign_id = row["campaign_id"]
        if not result["reachable"]:
            previous = conn.execute("SELECT harness FROM turns WHERE campaign_id = ? AND status = 'interrupted'"
                                    " ORDER BY turn_index DESC LIMIT 1", (campaign_id,)).fetchone()
            if previous is None or previous["harness"] == "codex":
                continue
        service.set_status(conn, settings, campaign_id, "running", "llm_available")
        service.update_stats(conn, campaign_id, pause_reason=None)
        conn.commit()  # the started service must see the running state
        try:
            start(campaign_id)
        except Exception:
            service.set_status(conn, settings, campaign_id, "paused", "llm_resume_start_failed")
            service.update_stats(conn, campaign_id, pause_reason="llm_unavailable")
            conn.commit()
            continue
        record_event(conn, settings, "campaign.llm_resumed", object_type="campaign", object_id=campaign_id,
                     payload={"recovered": recovered})
        conn.commit()
        output["campaigns"].append(campaign_id)
        running += 1

    output["thesis_runs"] = _resume_thesis(settings, conn, codex_reachable=result["reachable"])
    return output
