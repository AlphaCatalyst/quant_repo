"""Submission guards and handoff for dev-only batch reports."""

import importlib.util
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from alphasieve.cli import commands_risk
from alphasieve.errors import AlphaSieveError

_sw_path = Path(__file__).resolve().parents[1] / "tools/sw_industry_sensitivity.py"
_sw_spec = importlib.util.spec_from_file_location("sw_industry_sensitivity", _sw_path)
assert _sw_spec and _sw_spec.loader
sw = importlib.util.module_from_spec(_sw_spec)
_sw_spec.loader.exec_module(sw)


def _context(tmp_path, *, end="2022-12-31", tier="dev"):
    artifacts = tmp_path / "artifacts"
    parent = artifacts / "parent"
    parent.mkdir(parents=True)
    (parent / "manifest.json").write_text(json.dumps({
        "trial_id": "S-test", "kind": "training_run", "evidence_tier": tier,
        "window": ["2012-01-01", end], "panel_signature": "dev-signature",
    }))
    (parent / "metrics.json").write_text("{}")
    panel = tmp_path / "data" / "panel" / "dev"
    panel.mkdir(parents=True)
    (panel / "meta.json").write_text(json.dumps({
        "tier": "dev", "window": {"start": "2012-01-01", "end": "2022-12-31"},
        "signature": "dev-signature",
    }))
    raw = tmp_path / "data" / "raw" / "swsresearch"
    raw.mkdir(parents=True)
    pd.DataFrame({
        "code": ["a", "b", "c"],
        "effective_date": ["2022-01-01", "2023-01-01", "2022-01-01"],
        "updated_at": ["2022-02-01", "2023-02-01", "2024-01-01"],
    }).to_parquet(raw / "sw_industry_hist.parquet")
    settings = SimpleNamespace(artifacts_dir=artifacts, raw_dir=tmp_path / "data" / "raw",
                               hot_root=tmp_path, panel_dir=lambda name: panel)
    return SimpleNamespace(settings=settings)


def _args(**kwargs):
    return SimpleNamespace(trial="S-test", tier="dev", model="rm1", cluster=None,
                           output=None, **kwargs)


@pytest.mark.parametrize("end,tier", [("2023-01-01", "dev"), ("2026-09-28", "fresh"),
                                       ("2022-12-31", "holdout")])
def test_risk_submit_refuses_restricted_parent(tmp_path, monkeypatch, end, tier):
    ctx = _context(tmp_path, end=end, tier=tier)
    monkeypatch.setattr(commands_risk.subprocess, "run", lambda *_a, **_kw: pytest.fail("submitted"))
    with pytest.raises(AlphaSieveError, match="dev|2022-12-31"):
        commands_risk.cmd_risk_submit(_args(), ctx)


def test_risk_submit_passes_args_and_only_dev_history(tmp_path, monkeypatch):
    ctx = _context(tmp_path)
    monkeypatch.setattr(commands_risk, "REMOTE_ROOT", tmp_path / "remote")
    monkeypatch.setenv("ALPHASIEVE_NO_WAIT", "1")
    seen = {}

    def submit(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["pre"] = kwargs["env"]["ALPHASIEVE_PRE"]
        return SimpleNamespace(returncode=0, stdout="job alphasieve-risk-S-test-20261005-010101; outputs\n",
                               stderr="")

    monkeypatch.setattr(commands_risk.subprocess, "run", submit)
    result = commands_risk.cmd_risk_submit(_args(), ctx)
    assert seen["cmd"][-8:] == ["risk", "report", "--trial", "S-test", "--model", "rm1", "--tier", "dev"]
    assert result.data["job_id"] == "alphasieve-risk-S-test-20261005-010101"
    archive = Path(seen["pre"].split()[2])
    with tarfile.open(archive) as tar:
        history = pd.read_parquet(io.BytesIO(tar.extractfile(
            "hot/data/raw/swsresearch/sw_industry_hist.parquet").read()))
        assert history.code.tolist() == ["a"]
        assert "store/artifacts/parent/manifest.json" in tar.getnames()
        assert "store/artifacts/parent/metrics.json" in tar.getnames()


@pytest.mark.parametrize("start,end", [("2012-01-01", "2023-01-01"),
                                          ("2026-09-28", "2026-09-29")])
def test_sw_ray_refuses_restricted_period_before_input_reads(tmp_path, start, end):
    args = SimpleNamespace(start=pd.Timestamp(start), end=pd.Timestamp(end))
    with pytest.raises(ValueError, match="only 2012-01-01..2022-12-31"):
        sw.submit_ray(args)


def test_sw_ray_passes_worker_args_and_dev_only_tar(tmp_path, monkeypatch):
    hot = tmp_path / "hot"
    store = tmp_path / "store"
    (hot / "data/panel/dev").mkdir(parents=True)
    (hot / "data/panel/dev/meta.json").write_text(json.dumps({
        "tier": "dev", "window": {"end": "2022-12-31"},
    }))
    history = hot / "data/raw/swsresearch/sw_industry_hist.parquet"
    history.parent.mkdir(parents=True)
    pd.DataFrame({"code": ["a", "b"], "effective_date": ["2022-01-01", "2023-01-01"],
                  "updated_at": ["2022-02-01", "2023-02-01"]}).to_parquet(history)
    official = hot / "data/raw/dolthub/index_weights/000905.SH.parquet"
    official.parent.mkdir(parents=True)
    pd.DataFrame({"trade_date": ["2022-12-30", "2023-01-31"], "weight": [1.0, 2.0]}).to_parquet(official)
    monkeypatch.setattr(sw, "REFERENCE", {"v4": ("S-test", "task/run")})
    weights = store / "models/task/run/weights.parquet"
    weights.parent.mkdir(parents=True)
    pd.DataFrame({"a": [1.0, 2.0]}, index=pd.to_datetime(["2022-01-01", "2023-01-01"])).to_parquet(weights)
    monkeypatch.setattr(sw, "_remote_root", lambda: tmp_path / "remote")
    remote_hot = tmp_path / "remote/hot/data/panel/dev"
    remote_hot.mkdir(parents=True)
    (remote_hot / "meta.json").write_text((hot / "data/panel/dev/meta.json").read_text())
    monkeypatch.setenv("ALPHASIEVE_NO_WAIT", "1")
    seen = {}

    def submit(cmd, **kwargs):
        seen["cmd"] = cmd
        archive = Path(kwargs["env"]["ALPHASIEVE_BATCH_INPUT_TAR"])
        with tarfile.open(archive) as tar:
            hist = pd.read_parquet(io.BytesIO(tar.extractfile(
                "data/raw/swsresearch/sw_industry_hist.parquet").read()))
            off = pd.read_parquet(io.BytesIO(tar.extractfile(
                "data/raw/dolthub/index_weights/000905.SH.parquet").read()))
            target = pd.read_parquet(io.BytesIO(tar.extractfile("models/task/run/weights.parquet").read()))
        assert hist.code.tolist() == ["a"]
        assert off.trade_date.tolist() == ["2022-12-30"]
        assert len(target) == 1
        return SimpleNamespace(stdout="job alphasieve-sw-sensitivity-20261005-010101; outputs\n")

    monkeypatch.setattr(sw.subprocess, "run", submit)
    args = SimpleNamespace(start=sw.START, end=sw.END, portfolio_only=False,
                           hot_root=hot, store_root=store, output=tmp_path / "output")
    result = sw.submit_ray(args)
    assert result["job_id"] == "alphasieve-sw-sensitivity-20261005-010101"
    assert "--dev-snapshot" in seen["cmd"]
    assert "--portfolio-only" in seen["cmd"]
