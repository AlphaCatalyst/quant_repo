"""Small shared execution helpers for Ray job kinds."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from alphasieve.control.targets import load_targets
from alphasieve.errors import validation_error

DEV_END = "2022-12-31"
INFRA_WORDS = re.compile(
    r"node (?:died|death|lost|failed|removed|reclaimed)|raylet|worker (?:died|lost|failed)|"
    r"actor (?:died|lost|failed)|supervisor (?:died|lost|failed)|oom.kill|out.of.memory|outofmemory|"
    r"preempt|cluster unreachable|failed to connect to ray|connectionerror|connection refused|"
    r"connection timed out|job unknown|"
    r"submission_id .* not found|http error 404|head node", re.I,
)


def assert_dev(settings, *, end: str | None = None) -> None:
    """Hard boundary before any Ray submission."""
    if end and str(end)[:10] > DEV_END:
        raise validation_error("Ray jobs refuse dates after 2022-12-31")
    panel = settings.panel_dir("dev") / "meta.json"
    if panel.exists():
        meta = json.loads(panel.read_text(encoding="utf-8"))
        if meta.get("tier") != "dev" or str(meta.get("window", {}).get("end", ""))[:10] > DEV_END:
            raise validation_error("Ray jobs require a dev panel ending by 2022-12-31")
    remote = Path(load_targets(settings).remote_root) / "hot" / "data" / "panel"
    if (remote / "holdout").exists() or (remote / "fresh").exists():
        raise validation_error("holdout/fresh panel found on Ray shared storage")


def remote_root(settings) -> Path:
    return Path(load_targets(settings).remote_root)


def target_address(ctx) -> str:
    target = ctx.target
    if hasattr(target, "address"):
        return target.address
    if isinstance(target, dict):
        return target["address"]
    return str(target)


def ray_submit(ctx, job: dict, attempt: int, name: str, argv: list[str], *,
               batch: bool = False, env_extra: dict[str, str] | None = None,
               cpus: int | None = None) -> dict:
    external_id = f"alphasieve-{name}-{job['job_id']}-a{attempt}"
    script = Path(__file__).resolve().parents[4] / "deploy" / "ray" / "submit.sh"
    env = {**os.environ, "RAY_ADDRESS": target_address(ctx),
           "ALPHASIEVE_REMOTE_ROOT": str(remote_root(ctx.settings)),
           "ALPHASIEVE_NO_WAIT": "1"}
    if cpus:
        env["ALPHASIEVE_ENTRYPOINT_CPUS"] = str(cpus)
    env.update(env_extra or {})
    cmd = [str(script), "--no-wait", "--submission-id", external_id]
    if batch:
        cmd.append("--batch")
    cmd += [name, *argv]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=90)
    if result.returncode:
        raise RuntimeError(f"Ray submit failed: {(result.stderr + result.stdout)[-1000:]}")
    if f"job {external_id};" not in result.stdout:
        raise RuntimeError(f"Ray submit omitted submission ID: {result.stdout[-500:]}")
    return {"external_id": external_id, "address": target_address(ctx),
            "result_path": str(remote_root(ctx.settings) / "runs" / external_id / "result.json")}


def ray_poll(ctx, job: dict, handle: dict) -> tuple[str, dict]:
    address = handle.get("address") or target_address(ctx)
    external_id = handle["external_id"]
    request = Request(f"{address.rstrip('/')}/api/jobs/{external_id}", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=3) as response:
            payload = json.load(response)
    except HTTPError as exc:
        return "failed", {"error": f"job unknown to cluster (HTTP {exc.code})", "infra": exc.code == 404}
    except (URLError, TimeoutError, OSError) as exc:
        return "unreachable", {"error": f"cluster unreachable: {exc}", "infra": True}
    raw = payload.get("status", "")
    status = raw.upper() if isinstance(raw, str) else str(raw).upper()
    state = {"PENDING": "submitted", "RUNNING": "running", "SUCCEEDED": "succeeded",
             "FAILED": "failed", "STOPPED": "cancelled"}.get(status, "failed")
    detail = {"ray_status": status, "message": payload.get("message") or payload.get("error_message") or "",
              "external_id": external_id}
    if state == "failed":
        detail["infra"] = classify_ray(detail) == "infra"
    return state, detail


def classify_ray(detail) -> str:
    if isinstance(detail, dict):
        if detail.get("infra") is True:
            return "infra"
        detail = " ".join(str(detail.get(key, "")) for key in ("error", "message", "ray_status"))
    return "infra" if INFRA_WORDS.search(str(detail)) else "task"


def ray_stop(ctx, job: dict, handle: dict) -> None:
    subprocess.run(["ray", "job", "stop", handle["external_id"]],
                   env={**os.environ, "RAY_ADDRESS": handle.get("address") or target_address(ctx)},
                   check=True, capture_output=True, text=True, timeout=10)


def read_envelope(path: str | Path) -> dict:
    raw = Path(path).read_text(encoding="utf-8")
    payload = json.loads(raw[raw.find("{"):])
    if payload.get("status") != "ok":
        raise ValueError(f"remote result failed: {payload.get('error')}")
    return payload


@dataclass(frozen=True)
class Kind:
    name: str
    placement: str
    max_tier: str
    need_r2: bool
    max_attempts: int = 3
    units: str = "one"
    idempotency: str = "job_id"
