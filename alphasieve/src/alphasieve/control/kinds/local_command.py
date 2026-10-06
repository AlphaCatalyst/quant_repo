"""Bounded local alphasieve commands as transient systemd units."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from alphasieve.errors import validation_error

from .base import Kind


def systemd_run(argv: list[str], *, timeout: int = 10) -> subprocess.CompletedProcess:
    """Single injection point for tests."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def executable() -> list[str]:
    chosen = os.environ.get("ALPHASIEVE_BIN", "/data/alphasieve/deploy/current/.venv/bin/alphasieve")
    if Path(chosen).is_file() and os.access(chosen, os.X_OK):
        return [chosen]
    return [sys.executable, "-m", "alphasieve.cli.main"]


@dataclass(frozen=True)
class LocalCommand(Kind):
    name: str = "local_command"
    placement: str = "local"
    max_tier: str = "fresh"
    need_r2: bool = False
    units: str = "command"
    idempotency: str = "schedule_slot_or_job_id"

    def prepare(self, settings, conn, params: dict, trial_id: str | None = None):
        argv = params.get("argv") or params.get("command")
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            raise validation_error("local_command requires a nonempty alphasieve argv list")
        timeout = int(params.get("timeout", 3600))
        if timeout < 1 or timeout > 86400:
            raise validation_error("local_command timeout must be 1..86400 seconds")
        env = params.get("env") or {}
        if not isinstance(env, dict) or not all(re.fullmatch(r"[A-Z][A-Z0-9_]*", str(k)) and isinstance(v, str)
                                                and "\n" not in v for k, v in env.items()):
            raise validation_error("local_command env must map UPPER_CASE names to single-line strings")
        return {**params, "argv": argv, "timeout": timeout, "env": env}, trial_id

    def submit(self, ctx, job: dict, attempt: int) -> dict:
        params = job["params"]
        unit = f"alphasieve-job-{job['job_id']}"
        if not re.fullmatch(r"alphasieve-job-[A-Za-z0-9_-]+", unit):
            raise validation_error("invalid local job ID")
        log = Path("/data/alphasieve/logs/jobs") / f"{job['job_id']}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        # A shell is needed only for appending stderr/stdout to the requested log.
        import shlex

        command = " ".join(shlex.quote(x) for x in executable() + params["argv"])
        log_path = shlex.quote(str(log))
        shell = (f"{command} >> {log_path} 2>&1; "
                 f"code=$?; printf 'ALPHASIEVE_JOB_EXIT_STATUS=%s\\n' \"$code\" >> {log_path}; "
                 "exit \"$code\"")
        argv = ["systemd-run", f"--unit={unit}", "--collect", "-p", "Nice=10", "-p", "CPUWeight=20",
                "-p", f"RuntimeMaxSec={params['timeout']}", "--setenv=ALPHASIEVE_ROLE=system",
                *(f"--setenv={key}={value}" for key, value in sorted(params.get("env", {}).items())),
                "--", "/bin/bash", "-lc", shell]
        result = systemd_run(argv)
        if result.returncode:
            raise RuntimeError(f"systemd-run failed: {result.stderr[-500:]}")
        return {"external_id": unit, "unit": unit, "log": str(log)}

    def poll(self, ctx, job: dict, handle: dict) -> tuple[str, dict]:
        result = systemd_run(["systemctl", "show", handle["unit"], "-p", "ActiveState", "-p", "Result",
                              "-p", "ExecMainStatus"], timeout=4)
        if result.returncode:
            return "unreachable", {"error": result.stderr[-500:], "infra": True}
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        active = values.get("ActiveState", "")
        outcome = values.get("Result", "")
        code = values.get("ExecMainStatus", "")
        detail = {"active_state": active, "result": outcome, "exit_status": code}
        if active in ("active", "activating", "reloading"):
            return "running", detail
        if active == "inactive" and outcome == "success" and code in ("0", ""):
            return "succeeded", detail
        # --collect may unload the unit before the next minute's poll.
        log = Path(handle["log"])
        if active == "inactive" and log.is_file():
            markers = re.findall(r"^ALPHASIEVE_JOB_EXIT_STATUS=(\d+)$", log.read_text(errors="replace"), re.M)
            if markers:
                detail["exit_status"] = markers[-1]
                return ("succeeded" if markers[-1] == "0" else "failed"), detail
        if active == "inactive" and outcome == "" and code == "":
            return "submitted", detail
        return "failed", detail

    def classify(self, detail) -> str:
        if isinstance(detail, dict):
            if detail.get("infra"):
                return "infra"
            if detail.get("result") in ("timeout", "oom-kill", "signal"):
                return "infra"
        return "task"

    def collect(self, ctx, job: dict) -> dict:
        return {"log": str(Path("/data/alphasieve/logs/jobs") / f"{job['job_id']}.log")}

    def stop(self, ctx, job: dict, handle: dict) -> None:
        result = systemd_run(["systemctl", "stop", handle["unit"]], timeout=10)
        if result.returncode:
            raise RuntimeError(result.stderr[-500:])


KIND = LocalCommand()
