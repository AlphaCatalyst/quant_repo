"""Independent, immutable dev risk artifacts for saved strategy trials."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from alphasieve.artifacts import artifact_id_for
from alphasieve.data.access import load_panel
from alphasieve.errors import validation_error
from alphasieve.strategy.risk import (
    MODEL,
    PARAMETERS,
    SPEC_HASH,
    SW_WARNING,
    ExposureEngine,
    estimate_covariance,
    exante_te,
    fit_factor_returns,
    pit_quality,
)
from alphasieve.training.samples import benchmark_returns, universe_mask
from alphasieve.util import pretty_json

DEV_END = pd.Timestamp("2022-12-31")
FILES = ("risk_exposures.parquet", "risk_te.parquet", "risk_validation.json")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_dev(manifest: dict, *, tier: str) -> None:
    if tier != "dev" or manifest.get("evidence_tier") != "dev":
        raise validation_error("risk reports only accept dev parent and --tier dev")
    window = manifest.get("window") or []
    if len(window) != 2 or pd.Timestamp(window[1]) > DEV_END or pd.Timestamp(window[0]) > DEV_END:
        raise validation_error("risk report refuses a parent window after 2022-12-31")
    if manifest.get("kind") != "training_run" or not manifest.get("trial_id"):
        raise validation_error("parent must be a saved training run")


def _parent(settings, trial: str, tier: str) -> tuple[str, dict, Path, dict]:
    # Read only small dev manifests before opening any data table.
    if tier != "dev":
        raise validation_error("risk report only accepts --tier dev")
    matches = []
    for path in settings.artifacts_dir.glob("*/manifest.json"):
        manifest = _read_json(path)
        if manifest.get("trial_id") == trial:
            _check_dev(manifest, tier=tier)
            matches.append((path.parent.name, manifest))
    if len(matches) != 1:
        raise validation_error("expected exactly one parent artifact", trial=trial, matches=len(matches))
    aid, manifest = matches[0]
    runs = list((settings.store_root / "models" / manifest["task_id"]).glob(f"*{trial}*/manifest.json"))
    if len(runs) != 1:
        raise validation_error("expected exactly one saved model output", trial=trial, matches=len(runs))
    run_dir = runs[0].parent
    run_manifest = _read_json(runs[0])
    for field in ("trial_id", "config_hash", "panel_signature", "evidence_tier"):
        if run_manifest.get(field) != manifest.get(field):
            raise validation_error(f"saved output {field} does not match parent artifact")
    return aid, manifest, run_dir, run_manifest


def _sw_history(settings) -> tuple[pd.DataFrame, str]:
    source = settings.raw_dir / "swsresearch" / "sw_industry_hist.parquet"
    frame = pd.read_parquet(source)
    needed = {"code", "effective_date", "updated_at", "l1_name", "history_source_sha256"}
    if not needed.issubset(frame):
        raise validation_error("SW history lacks required source and timing columns")
    hist = frame.rename(
        columns={"l1_name": "industry", "updated_at": "known_at", "history_source_sha256": "source_version"}
    )
    return hist[["code", "effective_date", "known_at", "industry", "source_version"]], _sha(source)


def _unique_weights(weights: pd.DataFrame, panel) -> None:
    if not weights.index.is_unique or not weights.columns.is_unique:
        raise validation_error("saved weights have duplicate dates or codes")
    if pd.Timestamp(weights.index.max()) > DEV_END or pd.Timestamp(weights.index.min()) > DEV_END:
        raise validation_error("saved weights extend beyond dev")
    if set(weights.columns) != set(panel.codes) or not weights.index.isin(panel.dates).all():
        raise validation_error("saved weights do not align to parent panel")
    if not np.isfinite(weights.to_numpy(dtype=float)).all():
        raise validation_error("saved weights contain non-finite values")


def build_report(
    settings, trial: str, *, tier: str = "dev", model: str = MODEL
) -> tuple[dict, dict[str, pd.DataFrame], dict]:
    if model != MODEL:
        raise validation_error("only frozen rm1 is supported")
    aid, parent, run_dir, run_manifest = _parent(settings, trial, tier)
    # Validate tier/window/signature from metadata before the large parquet reads.
    meta_path = settings.panel_dir("dev") / "meta.json"
    panel_meta = _read_json(meta_path)
    if (
        panel_meta.get("tier") != "dev"
        or pd.Timestamp(panel_meta["window"]["end"]) > DEV_END
        or panel_meta.get("signature") != parent.get("panel_signature")
    ):
        raise validation_error("dev panel signature or window differs from locked parent")
    weights_path = run_dir / "weights.parquet"
    if not weights_path.exists():
        raise validation_error("saved target weights are unavailable")
    weights = pd.read_parquet(weights_path)
    if weights.empty:
        raise validation_error("saved target weights are empty")
    panel = load_panel(settings, "dev", universe="csi800")
    _unique_weights(weights, panel)
    history, history_digest = _sw_history(settings)
    market = benchmark_returns(panel, "zz500")
    if market is None:
        raise validation_error("parent panel lacks zz500 price index")
    universe = universe_mask(panel, "csi500", within_window=False)
    engine = ExposureEngine(panel, universe, market, industry_source="sw1", industry_history=history)
    target_pos = {int(panel.dates.get_loc(d)): d for d in weights.index}
    max_pos = max(target_pos)
    exposures, te_rows = [], []
    style_coverage = []
    history_returns, history_pos = [], []
    prior_snapshot = None
    first_pos = max(0, min(target_pos) - PARAMETERS["factor_window"] - 1)
    for pos in range(first_pos, max_pos + 1):
        # T's factor return uses X at T-1, never the current close exposures.
        if prior_snapshot is not None:
            fitted = fit_factor_returns(prior_snapshot, engine.ret[pos], date=panel.dates[pos])
            history_returns.append(fitted)
            history_pos.append(pos)
        snap = engine.snapshot(pos)
        if pos in target_pos:
            d = target_pos[pos]
            w = weights.loc[d].reindex(snap.codes).to_numpy(dtype=np.float64)
            q = pit_quality(snap, w)
            style_coverage.append(snap.coverage)
            cov = estimate_covariance(history_returns, snap, positions=np.asarray(history_pos), current_pos=pos)
            te = exante_te(snap, cov, w)
            ep = snap.X.T @ w
            eb = snap.X.T @ snap.benchmark
            for j, name in enumerate(snap.factors):
                miss = q["missing_weight"].get(name, {"portfolio": 0.0, "benchmark": 0.0})
                exposures.append(
                    {
                        "date": d,
                        "book": "target",
                        "factor": name,
                        "portfolio": float(ep[j]),
                        "benchmark": float(eb[j]),
                        "active": float(ep[j] - eb[j]),
                        "unit": "weight" if name.startswith("industry:") or name == "market" else "z",
                        "missing_portfolio_weight": miss["portfolio"],
                        "missing_benchmark_weight": miss["benchmark"],
                        "lower": np.nan,
                        "upper": np.nan,
                        "slack": np.nan,
                    }
                )
            te_rows.append(
                {
                    "date": d,
                    "book": "target",
                    "te": te.get("te"),
                    "daily_variance": te.get("daily_variance"),
                    "factor_variance": te.get("factor_variance"),
                    "specific_variance": te.get("specific_variance"),
                    "quality": te["quality"],
                    "factor_days": cov.days,
                    "unknown_portfolio_weight": q["unknown_industry_weight"]["portfolio"],
                    "unknown_benchmark_weight": q["unknown_industry_weight"]["benchmark"],
                    "prior_portfolio_weight": te.get("prior_weight", {}).get("portfolio"),
                    "prior_benchmark_weight": te.get("prior_weight", {}).get("benchmark"),
                    "cash": float(1 - w.sum()),
                    "max_industry_active": float(
                        max(
                            (abs(ep[j] - eb[j]) for j, name in enumerate(snap.factors) if name.startswith("industry:")),
                            default=0.0,
                        )
                    ),
                }
            )
        prior_snapshot = snap
    exposure_frame = pd.DataFrame(exposures)
    te_frame = pd.DataFrame(te_rows)
    numeric = exposure_frame.select_dtypes(include=["number"]).columns
    exposure_frame[numeric] = exposure_frame[numeric].astype("float64")
    numeric = te_frame.select_dtypes(include=["number"]).columns
    te_frame[numeric] = te_frame[numeric].astype("float64")
    coverage = {
        "target_dates": len(te_frame),
        "te_available": int(te_frame.te.notna().sum()),
        "te_provisional": int((te_frame.quality == "provisional").sum()),
        "te_valid": int((te_frame.quality == "valid").sum()),
        "industry_known_benchmark_mean": float(1 - te_frame.unknown_benchmark_weight.mean()),
        "style_raw_coverage_mean": {
            style: float(np.mean([row[style] for row in style_coverage])) for style in style_coverage[0]
        },
    }
    style_active = exposure_frame[exposure_frame.factor.isin(style_coverage[0])].groupby("factor").active
    summary = {
        "target_exante_te_mean": float(te_frame.te.mean()) if te_frame.te.notna().any() else None,
        "target_exante_te_last": float(te_frame.te.dropna().iloc[-1]) if te_frame.te.notna().any() else None,
        "target_exante_te_min": float(te_frame.te.min()) if te_frame.te.notna().any() else None,
        "target_exante_te_max": float(te_frame.te.max()) if te_frame.te.notna().any() else None,
        "style_active_mean": {key: float(value) for key, value in style_active.mean().items()},
        "max_industry_active_mean": float(te_frame.max_industry_active.mean()),
        "cash_mean": float(te_frame.cash.mean()),
    }
    validation = {
        "status": "insufficient_evidence",
        "reason": "actual_unavailable",
        "actual_unavailable": True,
        "strict_pit": False,
        "bias_B": None,
        "realised_te": None,
        "reference_realised_te": _read_json(settings.artifacts_dir / aid / "metrics.json")
        .get("portfolio", {})
        .get("execution", {})
        .get("tracking_error"),
        "reference_realised_te_basis": "saved legacy full-path net excess; not matched to target-close ex-ante TE",
        "pair_count": 0,
        "coverage": coverage,
        "exposures_and_te": summary,
        "warnings": [
            SW_WARNING,
            "Saved parent output contains target weights but no actual-close holdings or daily execution observer; "
            "bias calibration cannot be validated.",
        ],
    }
    manifest = {
        "kind": "risk_report",
        "model": MODEL,
        "risk_spec_hash": SPEC_HASH,
        "parameters": PARAMETERS,
        "evidence_tier": "dev",
        "window": parent["window"],
        "parent_trial_id": trial,
        "parent_artifact_id": aid,
        "parent_manifest_sha256": _sha(settings.artifacts_dir / aid / "manifest.json"),
        "parent_metrics_sha256": _sha(settings.artifacts_dir / aid / "metrics.json"),
        "parent_config_hash": parent["config_hash"],
        "parent_panel_signature": parent["panel_signature"],
        "parent_weights_sha256": _sha(weights_path),
        "industry_source": "sw1_pit",
        "sw_history_sha256": history_digest,
        "pit_complete": False,
        "actual_unavailable": True,
        "parent_code_version": parent.get("code_version"),
        "risk_code_sha256": {
            "risk.py": _sha(Path(__file__).resolve().parents[1] / "strategy" / "risk.py"),
            "risk_report.py": _sha(Path(__file__)),
        },
    }
    return manifest, {"risk_exposures.parquet": exposure_frame, "risk_te.parquet": te_frame}, validation


def write_risk_artifact(settings, manifest: dict, tables: dict[str, pd.DataFrame], validation: dict) -> str:
    if manifest.get("evidence_tier") != "dev" or pd.Timestamp(manifest["window"][1]) > DEV_END:
        raise validation_error("risk artifacts only accept dev through 2022-12-31")
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".risk-", dir=settings.artifacts_dir))
    try:
        for name in ("risk_exposures.parquet", "risk_te.parquet"):
            frame = tables[name].copy()
            if frame.duplicated(
                ["date", "book", "factor"] if name == "risk_exposures.parquet" else ["date", "book"]
            ).any():
                raise validation_error(f"duplicate risk table keys: {name}")
            numeric = frame.select_dtypes(include=["number"]).columns
            frame[numeric] = frame[numeric].astype("float64")
            frame.to_parquet(tmp / name, index=False)
        (tmp / "risk_validation.json").write_text(pretty_json(validation), encoding="utf-8")
        file_digests = {name: _sha(tmp / name) for name in FILES}
        full = json.loads(json.dumps({**manifest, "files": file_digests}))
        aid = artifact_id_for(full)
        (tmp / "manifest.json").write_text(pretty_json(full), encoding="utf-8")
        target = settings.artifacts_dir / aid
        if target.exists():
            if _read_json(target / "manifest.json") != full or any(
                _sha(target / name) != digest for name, digest in file_digests.items()
            ):
                raise validation_error("existing risk artifact id has different content")
            return aid
        try:
            os.rename(tmp, target)
        except FileExistsError as exc:
            if _read_json(target / "manifest.json") != full:
                raise validation_error("risk artifact publication collision") from exc
        return aid
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)


def read_risk_artifact(settings, aid: str) -> tuple[dict, dict]:
    if not aid or len(aid) != 24 or any(c not in "0123456789abcdef" for c in aid):
        raise validation_error("invalid risk artifact id")
    root = settings.artifacts_dir / aid
    manifest = _read_json(root / "manifest.json")
    if manifest.get("kind") != "risk_report" or artifact_id_for(manifest) != aid:
        raise validation_error("risk artifact manifest/id mismatch")
    if (
        manifest.get("model") != MODEL
        or manifest.get("risk_spec_hash") != SPEC_HASH
        or manifest.get("parameters") != json.loads(json.dumps(PARAMETERS))
    ):
        raise validation_error("risk artifact is not the frozen rm1 specification")
    _check_dev(
        {
            "evidence_tier": manifest.get("evidence_tier"),
            "window": manifest.get("window"),
            "kind": "training_run",
            "trial_id": manifest.get("parent_trial_id"),
        },
        tier="dev",
    )
    if set(manifest.get("files", {})) != set(FILES):
        raise validation_error("risk artifact file set mismatch")
    for name, digest in manifest["files"].items():
        if name not in FILES or _sha(root / name) != digest:
            raise validation_error("risk artifact file digest mismatch", file=name)
    return manifest, _read_json(root / "risk_validation.json")
