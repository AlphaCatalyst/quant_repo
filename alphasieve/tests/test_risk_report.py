import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from alphasieve.errors import AlphaSieveError
from alphasieve.strategy.risk import PARAMETERS, SPEC_HASH
from alphasieve.training.risk_report import (
    _parent,
    read_risk_artifact,
    write_risk_artifact,
)


def _fixtures(tmp_path):
    settings = SimpleNamespace(artifacts_dir=tmp_path / "artifacts", store_root=tmp_path / "store")
    manifest = {
        "kind": "risk_report",
        "evidence_tier": "dev",
        "window": ["2012-01-01", "2022-12-31"],
        "parent_trial_id": "S-example",
        "model": "rm1",
        "risk_spec_hash": SPEC_HASH,
        "parameters": PARAMETERS,
    }
    exposures = pd.DataFrame(
        {
            "date": [pd.Timestamp("2022-01-03")],
            "book": ["target"],
            "factor": ["market"],
            "active": np.array([0.123456789123], dtype=np.float64),
        }
    )
    te = pd.DataFrame(
        {"date": [pd.Timestamp("2022-01-03")], "book": ["target"], "te": np.array([0.0123456789123], dtype=np.float64)}
    )
    validation = {"status": "insufficient_evidence", "actual_unavailable": True}
    return settings, manifest, {"risk_exposures.parquet": exposures, "risk_te.parquet": te}, validation


def test_write_is_atomic_idempotent_and_preserves_float64(tmp_path):
    settings, manifest, tables, validation = _fixtures(tmp_path)
    aid = write_risk_artifact(settings, manifest, tables, validation)
    assert aid == write_risk_artifact(settings, manifest, tables, validation)
    root = settings.artifacts_dir / aid
    got_manifest, got_validation = read_risk_artifact(settings, aid)
    assert got_manifest["files"]["risk_te.parquet"]
    assert got_validation == validation
    got = pd.read_parquet(root / "risk_exposures.parquet")
    assert got.active.dtype == np.float64
    assert got.active.iloc[0] == tables["risk_exposures.parquet"].active.iloc[0]
    assert not list(settings.artifacts_dir.glob(".risk-*"))
    (root / "risk_validation.json").write_text(json.dumps({"tampered": True}))
    with pytest.raises(AlphaSieveError, match="digest"):
        read_risk_artifact(settings, aid)


def test_rejects_duplicate_keys_and_non_dev(tmp_path):
    settings, manifest, tables, validation = _fixtures(tmp_path)
    tables["risk_te.parquet"] = pd.concat([tables["risk_te.parquet"]] * 2, ignore_index=True)
    with pytest.raises(AlphaSieveError, match="duplicate"):
        write_risk_artifact(settings, manifest, tables, validation)
    manifest["evidence_tier"] = "holdout"
    with pytest.raises(AlphaSieveError, match="dev"):
        write_risk_artifact(settings, manifest, tables, validation)


def test_parent_rejects_future_before_opening_model_file(tmp_path, monkeypatch):
    settings, _, _, _ = _fixtures(tmp_path)
    parent = settings.artifacts_dir / "0123456789abcdef01234567"
    parent.mkdir(parents=True)
    (parent / "manifest.json").write_text(
        json.dumps(
            {
                "trial_id": "S-example",
                "kind": "training_run",
                "evidence_tier": "dev",
                "window": ["2012-01-01", "2023-01-01"],
            }
        )
    )
    with pytest.raises(AlphaSieveError, match="2022-12-31"):
        _parent(settings, "S-example", "dev")
    with pytest.raises(AlphaSieveError, match="dev"):
        _parent(settings, "S-example", "holdout")
