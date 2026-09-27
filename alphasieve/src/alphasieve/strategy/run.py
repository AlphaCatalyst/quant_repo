"""Strategy backtest (S-6): factors -> model score -> index-enhancement weights -> simulated execution.

Runs on the dev tier only (holdout strategy evidence needs an approved read, 15 §4 P-4). Designed to run as a
platform job; results go to an artifact, the scores to ``<store>/models/<run_id>/``, and a summary plus NAV
series to RunLab through the generic tracking hook.
"""

import sqlite3
import uuid

import pandas as pd

from alphasieve.artifacts import write_artifact
from alphasieve.audit import record_event
from alphasieve.config import Settings, load_config
from alphasieve.data.access import load_panel
from alphasieve.errors import validation_error
from alphasieve.factors import library as lib
from alphasieve.factors.dsl import compile_expression, evaluate
from alphasieve.factors.registry import get_factor
from alphasieve.search_space import load_search_space
from alphasieve.strategy.execution import simulate
from alphasieve.strategy.model import walk_forward_scores
from alphasieve.strategy.portfolio import build_weights, weight_diagnostics
from alphasieve.util import code_version, pretty_json

BENCHMARKS = {"csi800": "csi800"}


def feature_set(settings: Settings, conn: sqlite3.Connection, panel, factor_refs: list[str] | None) -> dict:
    if not factor_refs:
        frames = lib.library_frames(settings, conn, panel)
        if not frames:
            raise validation_error("the factor library is empty; run 'alphasieve library seed' or pass --factors")
        return frames
    space = load_search_space(settings)
    out = {}
    for ref in factor_refs:
        f = get_factor(conn, ref)
        out[f"{f['factor_id']}@{f['version']}"] = evaluate(compile_expression(f["canonical_expression"], space),
                                                          panel) * f["spec"]["direction"]
    return out


def backtest(settings: Settings, conn: sqlite3.Connection, universe: str = "csi800", horizon: int = 20,
             model: str = "ridge", retrain: str = "monthly", train_years: int = 5, rebalance_every: int = 5,
             industry_dev: float = 0.03, name_cap: float = 0.02, turnover_cap: float = 0.30,
             factor_refs: list[str] | None = None, seed_library: bool = False, n_jobs: int = 8,
             warmup_years: int = 2) -> dict:
    panel = load_panel(settings, "dev", role="system", universe=universe)
    if seed_library and not lib.library_members(conn):
        lib.seed_library(settings, conn, panel)
    features = feature_set(settings, conn, panel, factor_refs)
    scores, model_info = walk_forward_scores(features, panel, horizon, model, retrain, train_years, warmup_years,
                                             n_jobs=n_jobs)
    weights = build_weights(scores, panel, rebalance_every, industry_dev, name_cap, turnover_cap)
    diag = weight_diagnostics(weights, panel)
    costs = load_config(settings, "costs").get("b3", {})
    sim = simulate(weights, panel, costs, BENCHMARKS.get(universe))
    run_id = uuid.uuid4().hex[:12]
    out_dir = settings.store_root / "models" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    scores.astype("float32").to_parquet(out_dir / "scores.parquet")
    weights.astype("float32").to_parquet(out_dir / "weights.parquet")
    nav, excess_nav = sim.pop("_nav", pd.Series(dtype=float)), sim.pop("_excess_nav", pd.Series(dtype=float))
    monthly = pd.DataFrame({"nav": nav, "excess_nav": excess_nav}).resample("ME").last().dropna()
    config = {"universe": universe, "horizon": horizon, "model": model, "retrain": retrain,
              "train_years": train_years, "warmup_years": warmup_years, "rebalance_every": rebalance_every,
              "industry_dev": industry_dev, "name_cap": name_cap, "turnover_cap": turnover_cap,
              "features": list(features), "costs": costs, "panel_signature": panel.signature,
              "window": [str(d.date()) for d in panel.window]}
    result = {"run_id": run_id, "config": config, "model": model_info, "portfolio": diag, "execution": sim,
              "outputs": str(out_dir),
              "series": {"month": [str(d.date()) for d in monthly.index], "nav": monthly["nav"].round(6).tolist(),
                         "excess_nav": monthly["excess_nav"].round(6).tolist()}}
    manifest = {"kind": "strategy_backtest", "evidence_tier": "dev", "code_version": code_version(), **config}
    result["artifact_id"] = write_artifact(settings, manifest, metrics={k: v for k, v in result.items()},
                                           report="# Strategy backtest (dev)\n\n```json\n" + pretty_json(
                                               {k: v for k, v in result.items() if k != "series"}) + "\n```\n")
    record_event(conn, settings, "strategy.backtest", object_type="strategy", object_id=run_id,
                 payload={"artifact_id": result["artifact_id"], "ic": model_info["ic"].get("icir"),
                          "information_ratio": sim.get("information_ratio")})
    return result
