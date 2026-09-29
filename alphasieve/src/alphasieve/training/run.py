"""Run a TrainingTask end to end: samples -> walk-forward scores -> mandate portfolio -> backtest -> report.

A run is one strategy-layer trial (15 §4 P-5): the local ledger gets a ``started`` record before any computation
and a ``completed`` record with the dev metrics afterwards. Platform runs receive a frozen bundle (task + resolved
feature expressions) so the platform database is never consulted; the local host records the trial.
"""

import json
import sqlite3
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

from alphasieve.artifacts import write_artifact
from alphasieve.config import Settings, load_config
from alphasieve.contracts import TrialLedgerEntry
from alphasieve.data.access import Panel, load_panel
from alphasieve.errors import AlphaSieveError, validation_error
from alphasieve.factors import library as lib
from alphasieve.factors.registry import get_factor
from alphasieve.ledger.ledger import append_trial, strategy_trial_count
from alphasieve.training import mandates
from alphasieve.training.engine import score_diagnostics, to_grid, walk_forward
from alphasieve.training.samples import (
    BENCHMARK_OF,
    benchmark_returns,
    build_cross_sectional,
    rolling_beta,
    universe_mask,
)
from alphasieve.training.task import TrainingTask, parse_task
from alphasieve.util import canonical_json, code_version, pretty_json, sha256_hex

PANEL_OF = {"csi800": None, "csi500": None, "hs300": None, "ashare_all": "ashare_all", "ashare_2020": "ashare_2020",
            "hs300_2020": "hs300_2020", "etf_sector": "etf_sector"}


def resolve_features(conn: sqlite3.Connection, task: TrainingTask) -> list[dict]:
    """Freeze the factor part of the feature set: name, canonical expression, candidate hash, direction."""
    if task.features.factor_refs == "library":
        rows = lib.library_members(conn)
    else:
        rows = [get_factor(conn, ref) for ref in task.features.factor_refs]
        rows = [{**r, "name": r.get("name") or r["factor_id"], "direction": r["spec"]["direction"]} for r in rows]
    return [{"name": r["name"] or r["factor_id"], "canonical": r["canonical_expression"],
             "candidate_hash": r["candidate_hash"], "direction": r["direction"]} for r in rows]


def make_bundle(task: TrainingTask, members: list[dict], trial_id: str) -> dict:
    features = {"factors": members, "panel_fields": task.features.panel_fields}
    return {"task": task.model_dump(mode="json"), "features": features, "trial_id": trial_id,
            "feature_version": sha256_hex(canonical_json(features))[:12], "config_hash": task.config_hash,
            "code_version": code_version()}


def panel_universe(task: TrainingTask) -> str | None:
    return PANEL_OF[task.universe_train]


def _frames(settings: Settings, panel: Panel, bundle: dict) -> dict[str, pd.DataFrame]:
    frames = lib.frames_from_members(settings, panel, bundle["features"]["factors"])
    for f in bundle["features"]["panel_fields"]:
        if not panel.has(f):
            raise validation_error(f"panel field {f} not in the {panel.meta.get('universe')} panel")
        frames[f] = panel.wide(f)
    return frames


def _series(nav: pd.Series, excess: pd.Series | None) -> dict:
    frame = pd.DataFrame({"nav": nav, **({"excess_nav": excess} if excess is not None else {})})
    monthly = frame.resample("ME").last().dropna()
    out = {"month": [str(d.date()) for d in monthly.index], "nav": monthly["nav"].round(6).tolist()}
    if excess is not None:
        out["excess_nav"] = monthly["excess_nav"].round(6).tolist()
    return out


def run_cross_sectional(settings: Settings, task: TrainingTask, bundle: dict, panel: Panel, processes=None,
                        threads=None, progress=None) -> tuple[dict, dict]:
    bench_name = task.portfolio.benchmark or BENCHMARK_OF.get(task.universe_predict, "csi800")
    index_ret = benchmark_returns(panel, bench_name)
    beta = rolling_beta(panel, index_ret) if index_ret is not None else None
    frames = _frames(settings, panel, bundle)
    holdout = panel.tier != "dev"
    table = build_cross_sectional(panel, frames, task, beta, stride=task.sample.train_stride,
                                  train_before_window=holdout)
    window = panel.window_mask().to_numpy()
    run_task = task.model_copy(update={"split": task.split.model_copy(update={"warmup_years": 0.0})}) if holdout \
        else task
    wf = walk_forward(table, panel.dates, window, run_task, processes, threads, progress)
    shape = (len(panel.dates), len(panel.codes))
    score = to_grid(wf["score"], table, shape)
    predict_mask = universe_mask(panel, task.universe_predict)
    raw = {h: panel.wide(f"label_{h}d").to_numpy(dtype=float) for h in task.label.horizons}
    diag = score_diagnostics(score, raw, predict_mask, task.output.decay_lags)
    score_df = pd.DataFrame(score, index=panel.dates, columns=panel.codes)
    costs = load_config(settings, "costs").get("b3", {})
    result = {"model": {k: v for k, v in wf.items() if k not in ("score", "per_horizon")},
              "scores": diag, "features": {"names": table.feature_names, **table.extra["feature_report"]},
              "samples": {"rows": int(len(table.date_pos)), "train_rows": int(np.isfinite(
                  table.Y[task.label.horizons[0]]).sum()), "predict_rows": int(table.predict.sum())}}
    outputs = {"scores": score_df}
    if task.portfolio.kind in ("index_enhancement", "futures_hedged"):
        pf = mandates.index_enhancement(panel, score_df, predict_mask, beta, task.portfolio, costs, bench_name,
                                        index_ret)
        outputs["weights"] = pf.pop("weights")
        daily = pf.pop("_daily")
        nav, excess = pf.pop("_nav"), pf.pop("_excess_nav")
        result["portfolio"] = pf
        result["series"] = _series(nav, excess)
        if task.portfolio.kind == "futures_hedged":
            hedged = mandates.futures_hedged(panel, {"_daily": daily, "weights": outputs["weights"]}, beta,
                                             task.portfolio, index_ret)
            result["hedged"] = {k: v for k, v in hedged.items() if not k.startswith("_")}
            result["series"]["hedged_nav"] = _series(hedged["_nav"], None)["nav"]
            result["acceptance"] = hedged["acceptance"]
        else:
            result["acceptance"] = pf["acceptance"]
    return result, outputs


def execute(settings: Settings, bundle: dict, processes=None, threads=None, progress=None,
            tier: str = "dev") -> tuple[dict, dict]:
    task = parse_task(bundle["task"])
    panel = load_panel(settings, tier, role="system", universe=panel_universe(task))
    if tier == "dev" and panel.tier != "dev":
        raise validation_error("training runs read the dev tier only")
    if task.mandate in ("A", "D"):
        result, outputs = run_cross_sectional(settings, task, bundle, panel, processes, threads, progress)
    elif task.mandate == "C":
        from alphasieve.training.events import run_event_task
        result, outputs = run_event_task(settings, task, bundle, panel, processes, threads, progress)
    else:
        from alphasieve.training.etf import run_etf_task
        result, outputs = run_etf_task(settings, task, bundle, panel, processes, threads, progress)
    result["bundle"] = bundle
    result["manifest"] = {"task_id": task.task_id, "mandate": task.mandate, "config_hash": bundle["config_hash"],
                          "feature_version": bundle["feature_version"], "trial_id": bundle["trial_id"],
                          "panel_signature": panel.signature, "evidence_tier": panel.tier,
                          "window": [str(d.date()) for d in panel.window],
                          "code_version": bundle.get("code_version") or code_version(),
                          "seeds": task.search.seeds}
    return result, outputs


def write_outputs(settings: Settings, task_id: str, run_id: str, result: dict, outputs: dict,
                  bundle: dict) -> Path:
    out_dir = settings.store_root / "models" / task_id / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in outputs.items():
        frame.astype("float32").to_parquet(out_dir / f"{name}.parquet")
    (out_dir / "manifest.json").write_text(pretty_json({**result["manifest"], "bundle": bundle}), encoding="utf-8")
    (out_dir / "metrics.json").write_text(pretty_json({k: v for k, v in result.items() if k != "series"}),
                                          encoding="utf-8")
    return out_dir


def summary_metrics(result: dict) -> dict:
    out = {"acceptance_passed": bool((result.get("acceptance") or {}).get("passed", False))}
    first = next(iter(result.get("scores", {}).get("horizons", {}).values()), {})
    out.update({"rank_ic": first.get("rank_ic"), "icir": first.get("icir")})
    ex = (result.get("portfolio") or {}).get("execution") or {}
    for k in ("annual_excess", "information_ratio", "tracking_error", "max_drawdown_excess", "annual_cost"):
        if k in ex:
            out[k] = ex[k]
    for k, v in (result.get("headline") or {}).items():
        out[k] = v
    return out


def start_trial(conn: sqlite3.Connection, settings: Settings, task: TrainingTask, trial_id: str) -> None:
    append_trial(conn, TrialLedgerEntry(
        trial_id=trial_id, record_kind="started", candidate_hash=task.config_hash, evidence_tier="dev",
        search_space_version=f"training_task:{task.task_id}", created_by=settings.role, layer="strategy",
        scope=task.mandate, metrics={"task_id": task.task_id}))


def complete_trial(conn: sqlite3.Connection, settings: Settings, task: TrainingTask, trial_id: str, result: dict,
                   tier: str = "dev") -> str:
    done = conn.execute("SELECT 1 FROM trials WHERE trial_id = ? AND record_kind IN ('completed', 'failed')",
                        (trial_id,)).fetchone()
    if done is not None:
        raise AlphaSieveError("CONFLICT", f"trial {trial_id} already has a result record")
    manifest = {"kind": "training_run", "evidence_tier": tier, **result["manifest"]}
    report = "# Training run\n\n```json\n" + pretty_json({k: v for k, v in result.items() if k != "series"}) + "\n```\n"
    artifact_id = write_artifact(settings, manifest, metrics={k: v for k, v in result.items()}, report=report)
    metrics = summary_metrics(result)
    metrics["strategy_trials_for_mandate"] = strategy_trial_count(conn, task.mandate)
    append_trial(conn, TrialLedgerEntry(
        trial_id=trial_id, record_kind="completed", candidate_hash=task.config_hash, evidence_tier=tier,
        data_window="/".join(result["manifest"]["window"]), search_space_version=f"training_task:{task.task_id}",
        metrics=metrics, gate_results={"acceptance": result.get("acceptance", {})},
        outcome=f"{tier}_{'passed' if metrics['acceptance_passed'] else 'failed'}", created_by=settings.role,
        artifact_id=artifact_id, layer="strategy", scope=task.mandate))
    return artifact_id


def fail_trial(conn: sqlite3.Connection, settings: Settings, task: TrainingTask, trial_id: str, error: str) -> None:
    append_trial(conn, TrialLedgerEntry(
        trial_id=trial_id, record_kind="failed", candidate_hash=task.config_hash, evidence_tier="dev",
        search_space_version=f"training_task:{task.task_id}", metrics={"error": error[:500]}, outcome="run_failed",
        created_by=settings.role, layer="strategy", scope=task.mandate))


def new_trial_id() -> str:
    return f"S-{uuid.uuid4().hex[:12]}"


def load_bundle(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
