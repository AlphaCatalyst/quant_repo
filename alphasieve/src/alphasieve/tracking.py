"""Optional experiment tracking on RunLab (a WandB-compatible server) for platform batch jobs.

Enabled only when ALPHASIEVE_TRACKING=runlab and WANDB_API_KEY / WANDB_BASE_URL are set, which the Ray submit
script does when a key file exists on the remote root. Holdout and review commands are never tracked.
"""

import math
import os

UNTRACKED_PREFIXES = ("holdout", "review", "campaign", "directive", "request", "orchestrator", "serve", "version",
                      "evalsvc", "state")
MAX_KEYS = 300


def enabled() -> bool:
    return (os.environ.get("ALPHASIEVE_TRACKING") == "runlab" and bool(os.environ.get("WANDB_API_KEY"))
            and bool(os.environ.get("WANDB_BASE_URL")))


def flatten(data, prefix: str = "", depth: int = 0, out: dict | None = None) -> dict:
    out = {} if out is None else out
    if len(out) >= MAX_KEYS or depth > 4:
        return out
    if isinstance(data, dict):
        for k, v in data.items():
            flatten(v, f"{prefix}{k}" if not prefix else f"{prefix}/{k}", depth + 1, out)
    elif isinstance(data, bool):
        out[prefix] = int(data)
    elif isinstance(data, (int, float)) and math.isfinite(data):
        out[prefix] = data
    return out


def record(command: str, args: dict, envelope: dict, settings) -> str | None:
    if not enabled() or command.split(" ")[0] in UNTRACKED_PREFIXES or settings.role == "agent":
        return None
    try:
        import wandb
    except ImportError:
        return None
    from alphasieve.util import code_version

    run = wandb.init(
        entity=os.environ.get("ALPHASIEVE_RUNLAB_ENTITY") or None,
        project=os.environ.get("ALPHASIEVE_RUNLAB_PROJECT", "alphasieve"),
        name=os.environ.get("ALPHASIEVE_JOB_ID") or None,
        job_type=command.replace(" ", "-"),
        config={"command": command, "args": args, "code_version": code_version(), "user": settings.user},
        tags=[command.split(" ")[0]],
        reinit=True,
    )
    data = envelope.get("data") or {}
    series = data.get("series") if isinstance(data.get("series"), dict) else {}
    numeric = {k: v for k, v in series.items() if isinstance(v, list) and v and isinstance(v[0], (int, float))}
    for step in range(max((len(v) for v in numeric.values()), default=0)):
        run.log({k: v[step] for k, v in numeric.items() if step < len(v)}, step=step)
    summary = flatten({k: v for k, v in data.items() if k != "series"})
    summary["status_ok"] = int(envelope.get("status") == "ok")
    run.summary.update(summary)
    run_id = run.id
    run.finish()
    return run_id
