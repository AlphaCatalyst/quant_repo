"""Dev risk reports staged onto the R2 mounted Ray clusters."""

from __future__ import annotations

import json
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from alphasieve.errors import validation_error

from .base import Kind, assert_dev, classify_ray, ray_poll, ray_stop, ray_submit, read_envelope, remote_root


@dataclass(frozen=True)
class RiskReport(Kind):
    name: str = "risk_report"
    placement: str = "ray"
    max_tier: str = "dev"
    need_r2: bool = True
    units: str = "report"
    idempotency: str = "trial_id+model"

    def prepare(self, settings, conn, params: dict, trial_id: str | None = None):
        import pandas as pd

        from alphasieve.training.risk_report import DEV_END, _check_dev

        trial = params["trial"]
        if not re.fullmatch(r"S-[A-Za-z0-9_-]+", trial):
            raise validation_error("invalid risk parent trial ID")
        if params.get("tier", "dev") != "dev":
            raise validation_error("Ray risk reports require dev tier")
        assert_dev(settings)
        matches = []
        for path in settings.artifacts_dir.glob("*/manifest.json"):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest.get("trial_id") == trial:
                _check_dev(manifest, tier="dev")
                matches.append((path, manifest))
        if len(matches) != 1:
            raise validation_error("expected exactly one dev parent artifact", trial=trial, matches=len(matches))
        parent, manifest = matches[0]
        panel_meta = json.loads((settings.panel_dir("dev") / "meta.json").read_text(encoding="utf-8"))
        if panel_meta["signature"] != manifest["panel_signature"]:
            raise validation_error("dev panel signature differs from risk parent")
        source = settings.raw_dir / "swsresearch" / "sw_industry_hist.parquet"
        history = pd.read_parquet(source)
        history = history.loc[(pd.to_datetime(history["effective_date"]) <= DEV_END)
                              & (pd.to_datetime(history["updated_at"]) <= DEV_END)]
        stage = remote_root(settings) / "runs" / "staging"
        stage.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as temp:
            safe = Path(temp) / "sw_industry_hist.parquet"
            history.to_parquet(safe, index=False)
            archive = Path(temp) / "inputs.tar"
            with tarfile.open(archive, "w") as tar:
                tar.add(safe, arcname="hot/data/raw/swsresearch/sw_industry_hist.parquet")
                for filename in ("manifest.json", "metrics.json"):
                    tar.add(parent.parent / filename, arcname=f"store/artifacts/{parent.parent.name}/{filename}")
            staged = stage / f"risk-{trial}-{os.getpid()}.tar"
            shutil.copyfile(archive, staged)
        output = params.get("output") or str(settings.hot_root / "reports" / "risk" / f"{trial}.json")
        return {**params, "output": str(output), "staged_input": str(staged), "tier": "dev"}, trial_id

    def submit(self, ctx, job: dict, attempt: int) -> dict:
        assert_dev(ctx.settings)
        params = job["params"]
        archive = params["staged_input"]
        if not Path(archive).is_file():
            raise FileNotFoundError(f"risk input archive missing: {archive}")
        root = remote_root(ctx.settings)
        return ray_submit(ctx, job, attempt, "risk", ["risk", "report", "--trial", params["trial"],
                          "--model", params.get("model", "rm1"), "--tier", "dev"],
                          env_extra={"ALPHASIEVE_PRE": f"tar -xf {archive} -C {root}"})

    poll = staticmethod(ray_poll)
    classify = staticmethod(classify_ray)
    stop = staticmethod(ray_stop)

    def collect(self, ctx, job: dict) -> dict:
        handle = job.get("handle") or job.get("handle_json")
        if isinstance(handle, str):
            handle = json.loads(handle)
        path = Path(handle["result_path"])
        payload = read_envelope(path)
        if payload.get("command") != "risk report":
            raise ValueError("unexpected risk result command")
        output = Path(job["params"]["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_suffix(output.suffix + ".tmp")
        temp.write_bytes(path.read_bytes())
        temp.replace(output)
        return {"output": str(output), "report": payload["data"]}


KIND = RiskReport()
