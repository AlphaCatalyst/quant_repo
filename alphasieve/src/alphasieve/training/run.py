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


def make_bundle(task: TrainingTask, members: list[dict], trial_id: str, settings: Settings | None = None) -> dict:
    if task.score_source is not None:
        from alphasieve.training.score_source import freeze_source as freeze_scores

        if settings is None:
            raise validation_error("freezing a score source needs the settings")
        features = {"score_source": freeze_scores(settings, task.score_source.task_id, task.score_source.trial_id)}
        return {"task": task.model_dump(mode="json"), "features": features, "trial_id": trial_id,
                "feature_version": sha256_hex(canonical_json(features))[:12], "config_hash": task.config_hash,
                "code_version": code_version()}
    features = {"factors": members, "panel_fields": task.features.panel_fields}
    if task.features.derived_fields:
        features["derived_fields"] = task.features.derived_fields
    src = task.features.event_source
    if src is not None:
        from alphasieve.training.event_features import freeze_source

        if settings is None:
            raise validation_error("freezing an event-score source needs the settings")
        features["event_source"] = freeze_source(settings, src.task_id, src.trial_id)
    mapping = task.features.etf_mapping
    if mapping is not None:
        from alphasieve.training.etf_industry import load_holdings

        if settings is None:
            raise validation_error("freezing an ETF mapping needs the settings")
        features["etf_mapping"] = {"mode": mapping.mode, "mapping_asof": mapping.mapping_asof,
                                   "holdings_sha256": load_holdings(settings, mapping.mapping_asof)[1]}
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
    if bundle["features"].get("derived_fields"):
        from alphasieve.training.derived import derived_frames

        frames.update(derived_frames(panel, bundle["features"]["derived_fields"]))
    return frames


def _series(nav: pd.Series, excess: pd.Series | None) -> dict:
    frame = pd.DataFrame({"nav": nav, **({"excess_nav": excess} if excess is not None else {})})
    monthly = frame.resample("ME").last().dropna()
    out = {"month": [str(d.date()) for d in monthly.index], "nav": monthly["nav"].round(6).tolist()}
    if excess is not None:
        out["excess_nav"] = monthly["excess_nav"].round(6).tolist()
    return out


def _walk_forward_scores(settings: Settings, task: TrainingTask, bundle: dict, panel: Panel, beta, processes,
                         threads, progress) -> tuple[pd.DataFrame, dict]:
    frames = _frames(settings, panel, bundle)
    event_report = None
    if bundle["features"].get("event_source"):
        from alphasieve.training.event_features import event_frames

        extra, event_report = event_frames(settings, panel, bundle["features"]["event_source"],
                                           task.features.event_lag_days, task.features.event_half_lives)
        frames.update(extra)
    holdout = panel.tier != "dev"
    table = build_cross_sectional(panel, frames, task, beta, stride=task.sample.train_stride,
                                  train_before_window=holdout)
    window = panel.window_mask().to_numpy()
    run_task = task.model_copy(update={"split": task.split.model_copy(update={"warmup_years": 0.0})}) if holdout \
        else task
    wf = walk_forward(table, panel.dates, window, run_task, processes, threads, progress)
    score = to_grid(wf["score"], table, (len(panel.dates), len(panel.codes)))
    result = {"model": {k: v for k, v in wf.items() if k not in ("score", "per_horizon")},
              "features": {"names": table.feature_names, **table.extra["feature_report"],
                           **({"event_source": event_report} if event_report else {})},
              "samples": {"rows": int(len(table.date_pos)), "train_rows": int(np.isfinite(
                  table.Y[task.label.horizons[0]]).sum()), "predict_rows": int(table.predict.sum())}}
    return pd.DataFrame(score, index=panel.dates, columns=panel.codes), result


def run_cross_sectional(settings: Settings, task: TrainingTask, bundle: dict, panel: Panel, processes=None,
                        threads=None, progress=None) -> tuple[dict, dict]:
    bench_name = task.portfolio.benchmark or BENCHMARK_OF.get(task.universe_predict, "csi800")
    index_ret = benchmark_returns(panel, bench_name)
    beta = rolling_beta(panel, index_ret) if index_ret is not None else None
    predict_mask = universe_mask(panel, task.universe_predict)
    raw = {h: panel.wide(f"label_{h}d").to_numpy(dtype=float) for h in task.label.horizons}
    frozen = bundle["features"].get("score_source")
    if frozen and panel.tier == "dev":
        from alphasieve.training.score_source import load_scores

        score_df, source = load_scores(settings, panel, frozen)
        result = {"model": {"reused_scores": True, "fits": 0}, "features": {"score_source": source}}
    elif frozen:
        from alphasieve.training.score_source import source_bundle

        src_bundle = source_bundle(settings, frozen)
        src_task = parse_task(src_bundle["task"])
        if (src_task.universe_train, src_task.universe_predict) != (task.universe_train, task.universe_predict):
            raise validation_error("the score source trains or predicts on a different universe")
        score_df, result = _walk_forward_scores(settings, src_task, src_bundle, panel, beta, processes, threads,
                                                progress)
        result["features"]["score_source"] = {**frozen, "rescored_on_tier": panel.tier}
    else:
        score_df, result = _walk_forward_scores(settings, task, bundle, panel, beta, processes, threads, progress)
    score = score_df.to_numpy(dtype=float)
    result["scores"] = score_diagnostics(score, raw, predict_mask, task.output.decay_lags)
    costs = load_config(settings, "costs").get("b3", {})
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
            from alphasieve.data.futures import read_hedge_leg

            leg = read_hedge_leg(settings, panel.tier, "system", task.portfolio.hedge or "IC", panel_universe(task))
            hedged = mandates.futures_hedged(panel, {"_daily": daily, "weights": outputs["weights"]}, beta,
                                             task.portfolio, index_ret, leg)
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


EULER_GAMMA = 0.5772156649


def search_discount(n_trials: int, days: int, ratio: float | None) -> dict:
    """Expected best annualised IR / Sharpe among ``n_trials`` null strategies over ``days`` trading days
    (Bailey & Lopez de Prado), and the observed ratio net of it: the per-mandate search discount (15 §4 P-5)."""
    from scipy.stats import norm

    years = days / 252 if days else 0.0
    if n_trials < 1 or years <= 0:
        return {"trials": n_trials}
    if n_trials == 1:
        e_max = 0.0
    else:
        e_max = ((1 - EULER_GAMMA) * norm.ppf(1 - 1 / n_trials)
                 + EULER_GAMMA * norm.ppf(1 - 1 / (n_trials * np.e)))
    hurdle = float(e_max / np.sqrt(years))
    out = {"trials": n_trials, "years": round(years, 2), "null_expected_max_ratio": hurdle}
    if ratio is not None and np.isfinite(ratio):
        out["deflated_ratio"] = float(ratio - hurdle)
    return out


def _headline_ratio(result: dict) -> tuple[float | None, int]:
    ex = (result.get("portfolio") or {}).get("execution") or {}
    if "hedged" in result:
        return result["hedged"].get("sharpe"), ex.get("days", 0)
    if "information_ratio" in ex:
        return ex["information_ratio"], ex.get("days", 0)
    return (result.get("portfolio") or {}).get("sharpe"), ex.get("days", 0)


def start_trial(conn: sqlite3.Connection, settings: Settings, task: TrainingTask, trial_id: str,
                check_budget: bool = True) -> None:
    budget = mandates.STRATEGY_TRIAL_BUDGET[task.mandate]
    if check_budget and strategy_trial_count(conn, task.mandate, "dev") >= budget:
        raise AlphaSieveError("BUDGET_EXHAUSTED", f"mandate {task.mandate} has used its {budget} approved"
                              " strategy trials; a larger budget needs a recorded decision")
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
    n = strategy_trial_count(conn, task.mandate, tier)
    metrics["strategy_trials_for_mandate"] = n
    ratio, days = _headline_ratio(result)
    metrics["search_discount"] = search_discount(n, days, ratio)
    result["search_discount"] = metrics["search_discount"]
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
