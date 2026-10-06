"""Dev-only Ray training with a stable trial and bundle across attempts."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .base import Kind, assert_dev, classify_ray, ray_poll, ray_stop, ray_submit, read_envelope, remote_root


@dataclass(frozen=True)
class Train(Kind):
    name: str = "train"
    placement: str = "ray"
    max_tier: str = "dev"
    need_r2: bool = True
    units: str = "retrain_date"
    idempotency: str = "trial_id+bundle_hash+retrain_date"

    def prepare(self, settings, conn, params: dict, trial_id: str | None = None):
        from alphasieve.training import run as tr
        from alphasieve.training.task import load_task

        task = load_task(settings, params["task"])
        assert_dev(settings)
        trial_id = trial_id or tr.new_trial_id()
        bundle = tr.make_bundle(task, tr.resolve_features(conn, task), trial_id, settings)
        bundle_dir = remote_root(settings) / "runs" / "bundles"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        path = bundle_dir / f"{trial_id}.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
        tr.start_trial(conn, settings, task, trial_id)
        return {**params, "bundle": str(path), "processes": params.get("processes") or task.platform.processes,
                "threads": params.get("threads") or 4}, trial_id

    def submit(self, ctx, job: dict, attempt: int) -> dict:
        assert_dev(ctx.settings)
        params = job["params"]
        cpus = params.get("cpus") or params["processes"] * params["threads"]
        return ray_submit(ctx, job, attempt, "train", ["train", "run", "--bundle", params["bundle"],
                          "--processes", str(params["processes"]), "--threads", str(params["threads"]),
                          "--units-dir", str(remote_root(ctx.settings) / "runs" / job["trial_id"] / "units")],
                          cpus=cpus)

    poll = staticmethod(ray_poll)
    classify = staticmethod(classify_ray)
    stop = staticmethod(ray_stop)

    def collect(self, ctx, job: dict) -> dict:
        handle = job.get("handle") or job.get("handle_json")
        if isinstance(handle, str):
            handle = json.loads(handle)
        result = read_envelope(handle["result_path"])["data"]
        if result["manifest"]["trial_id"] != job["trial_id"]:
            raise ValueError("remote training result belongs to another trial")
        return result


KIND = Train()
