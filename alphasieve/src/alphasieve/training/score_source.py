"""Frozen walk-forward scores of a completed dev run, reused by portfolio-construction trials (docs/21 §3).

The bundle records the sha256 of the source run's ``scores.parquet`` and ``manifest.json`` and the source's identity
(task, trial, config hash, panel signature); a run accepts only the same bytes. No model is refitted on dev.

Frozen dev scores cannot score another tier (docs/21 §6). A holdout read of such a trial instead re-runs the source
trial's own training bundle, which its frozen ``manifest.json`` carries, on the holdout window
(``source_bundle``), and feeds those scores to the locked portfolio configuration.
"""

import json

import pandas as pd

from alphasieve.config import Settings
from alphasieve.data.access import Panel
from alphasieve.errors import validation_error
from alphasieve.util import file_sha256

DEV_END = pd.Timestamp("2022-12-31")


def source_dir(settings: Settings, task_id: str, trial_id: str):
    runs = sorted((settings.store_root / "models" / task_id).glob(f"*{trial_id}*"))
    if len(runs) != 1 or not all((runs[0] / f).exists() for f in ("scores.parquet", "manifest.json")):
        raise validation_error(f"expected one {task_id} run of trial {trial_id} with scores and a manifest",
                               found=[str(r) for r in runs])
    return runs[0]


def freeze_source(settings: Settings, task_id: str, trial_id: str) -> dict:
    run = source_dir(settings, task_id, trial_id)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    identity = {k: manifest.get(k) for k in ("task_id", "trial_id", "evidence_tier", "mandate")}
    if identity != {"task_id": task_id, "trial_id": trial_id, "evidence_tier": "dev", "mandate": "A"}:
        raise validation_error("score source manifest does not describe a dev A run of this trial", **identity)
    return {"task_id": task_id, "trial_id": trial_id, "run": run.name,
            "scores_sha256": file_sha256(run / "scores.parquet"),
            "manifest_sha256": file_sha256(run / "manifest.json"),
            "config_hash": manifest["config_hash"], "feature_version": manifest["feature_version"],
            "panel_signature": manifest["panel_signature"], "window": manifest["window"]}


def source_bundle(settings: Settings, frozen: dict) -> dict:
    """The source trial's training bundle from its frozen manifest, checked against the frozen identity."""
    from alphasieve.training.task import parse_task

    run = source_dir(settings, frozen["task_id"], frozen["trial_id"])
    if file_sha256(run / "manifest.json") != frozen["manifest_sha256"]:
        raise validation_error("the frozen score source manifest.json changed since the bundle was made", run=str(run))
    bundle = json.loads((run / "manifest.json").read_text(encoding="utf-8")).get("bundle")
    if bundle is None:
        raise validation_error("the score source manifest carries no training bundle", run=str(run))
    found = {"trial_id": bundle.get("trial_id"), "config_hash": bundle.get("config_hash"),
             "feature_version": bundle.get("feature_version")}
    if found != {k: frozen[k] for k in found} or parse_task(bundle["task"]).config_hash != frozen["config_hash"]:
        raise validation_error("the score source bundle does not match the frozen identity", **found)
    if bundle["features"].get("score_source"):
        raise validation_error("a score source must itself be a trained run, not another frozen-score run")
    return bundle


def load_scores(settings: Settings, panel: Panel, frozen: dict) -> tuple[pd.DataFrame, dict]:
    run = source_dir(settings, frozen["task_id"], frozen["trial_id"])
    for name, key in (("scores.parquet", "scores_sha256"), ("manifest.json", "manifest_sha256")):
        if file_sha256(run / name) != frozen[key]:
            raise validation_error(f"the frozen score source {name} changed since the bundle was made", run=str(run))
    if panel.tier != "dev":
        raise validation_error("frozen dev scores cannot score another tier")
    scores = pd.read_parquet(run / "scores.parquet")
    scores.index = pd.to_datetime(scores.index)
    if scores.index.max() > DEV_END or scores.index.has_duplicates or scores.columns.has_duplicates:
        raise validation_error("frozen scores must be unique dev dates and codes")
    report = {**frozen, "panel_signature_matches": panel.signature == frozen["panel_signature"],
              "reused_scores": True}
    return scores.reindex(index=panel.dates, columns=panel.codes).astype(float), report
