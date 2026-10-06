"""Dev-only SW sensitivity batch analysis on Ray."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from alphasieve.errors import validation_error

from .base import Kind, assert_dev, classify_ray, ray_poll, ray_stop, ray_submit, remote_root


def _tool():
    path = Path(__file__).resolve().parents[4] / "tools" / "sw_industry_sensitivity.py"
    spec = importlib.util.spec_from_file_location("alphasieve_sw_sensitivity_tool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class SwSensitivity(Kind):
    name: str = "sw_sensitivity"
    placement: str = "ray"
    max_tier: str = "dev"
    need_r2: bool = True
    units: str = "portfolio_report"
    idempotency: str = "input_archive_hash"

    def prepare(self, settings, conn, params: dict, trial_id: str | None = None):
        import pandas as pd

        tool = _tool()
        start = pd.Timestamp(params.get("start", tool.START))
        end = pd.Timestamp(params.get("end", tool.END))
        if start != tool.START or end != tool.END:
            raise validation_error("SW Ray report accepts only the full 2012-01-01..2022-12-31 dev window")
        assert_dev(settings, end=str(end.date()))
        remote = remote_root(settings)
        hot = remote / "hot"
        meta = json.loads((hot / "data/panel/dev/meta.json").read_text(encoding="utf-8"))
        if meta.get("tier") != "dev" or meta["window"]["end"] > str(tool.END.date()):
            raise validation_error("SW Ray report requires dev-only remote panel")
        with tempfile.TemporaryDirectory(prefix="alphasieve-sw-ray-") as temp:
            staging = Path(temp)
            history = pd.read_parquet(settings.raw_dir / "swsresearch/sw_industry_hist.parquet")
            history = history[(pd.to_datetime(history["effective_date"]) <= tool.END)
                              & (pd.to_datetime(history["updated_at"]) <= tool.END)].copy()
            if history.empty:
                raise validation_error("no dev-only SW history rows")
            dest = staging / "data/raw/swsresearch/sw_industry_hist.parquet"
            dest.parent.mkdir(parents=True)
            history.to_parquet(dest, index=False)
            official = pd.read_parquet(settings.raw_dir / "dolthub/index_weights/000905.SH.parquet")
            official = official[pd.to_datetime(official["trade_date"]) <= tool.END].copy()
            if official.empty:
                raise validation_error("no dev-only official CSI 500 weights")
            dest = staging / "data/raw/dolthub/index_weights/000905.SH.parquet"
            dest.parent.mkdir(parents=True)
            official.to_parquet(dest, index=False)
            for _key, (_trial, rel) in tool.REFERENCE.items():
                source_dir = Path(rel) if Path(rel).is_absolute() else settings.store_root / "models" / rel
                source = source_dir / "weights.parquet"
                if not source.exists():
                    raise validation_error(f"missing saved A weights: {source}")
                target = Path(rel).parent.name / Path(rel).name if Path(rel).is_absolute() else Path(rel)
                tool._dev_weights(source, staging / "models" / target / "weights.parquet")
            archive = staging / "inputs.tar"
            with tarfile.open(archive, "w") as tar:
                tar.add(staging / "models", arcname="models")
                tar.add(staging / "data", arcname="data")
            staged = remote / "batch_inputs" / f"sw-sensitivity-{uuid.uuid4().hex}.tar"
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(archive, staged)
        output = params.get("output") or str(settings.hot_root / "reports" / "sw_sensitivity")
        return {**params, "start": str(start.date()), "end": str(end.date()), "output": str(output),
                "staged_input": str(staged)}, trial_id

    def submit(self, ctx, job: dict, attempt: int) -> dict:
        assert_dev(ctx.settings, end=job["params"]["end"])
        staged = Path(job["params"]["staged_input"])
        if not staged.is_file():
            raise FileNotFoundError(f"SW input archive missing: {staged}")
        hot = remote_root(ctx.settings) / "hot"
        unpacked = staged.with_suffix("")
        argv = ["tools/sw_industry_sensitivity.py", "--ray-worker", "--portfolio-only", "--dev-snapshot",
                "--hot-root", str(hot), "--store-root", str(unpacked),
                "--history-path", str(unpacked / "data/raw/swsresearch/sw_industry_hist.parquet"),
                "--official-path", str(unpacked / "data/raw/dolthub/index_weights/000905.SH.parquet")]
        return ray_submit(ctx, job, attempt, "sw-sensitivity", argv, batch=True,
                          env_extra={"ALPHASIEVE_BATCH_INPUT_TAR": str(staged)})

    poll = staticmethod(ray_poll)
    classify = staticmethod(classify_ray)
    stop = staticmethod(ray_stop)

    def collect(self, ctx, job: dict) -> dict:
        handle = job.get("handle") or job.get("handle_json")
        if isinstance(handle, str):
            handle = json.loads(handle)
        source = Path(handle["result_path"]).parent
        result = json.loads((source / "result.json").read_text(encoding="utf-8"))
        output = Path(job["params"]["output"])
        output.mkdir(parents=True, exist_ok=True)
        for name in [*result.get("outputs", []), "manifest.json"]:
            if Path(name).name != name:
                raise ValueError("invalid SW result file name")
            shutil.copy2(source / name, output / name)
        return {"output": str(output), "result": result}


KIND = SwSensitivity()
