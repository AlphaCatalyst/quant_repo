"""Mandate B: sector-ETF rotation (docs/mandates/training-tasks §4, docs/mandates/training-round2 §4).

Features are each ETF's own price and volume history plus, when the task sets ``etf_mapping``, the industry- or
basket-mapped stock features built by ``etf_industry`` from the current holdings snapshot. Labels are the forward
open-to-open return in excess of the equal-weighted ETF universe. The portfolio holds the ``top_k`` ETFs by score
with equal weights, rebalanced every ``rebalance_every`` days, against the equal-weighted universe. The headline
and acceptance use real ETF rows only; rows where the tracked index stands in before listing are an appendix.
"""

import numpy as np
import pandas as pd

from alphasieve.config import load_config
from alphasieve.data.access import Panel
from alphasieve.errors import validation_error
from alphasieve.strategy.execution import simulate
from alphasieve.training import mandates
from alphasieve.training.engine import score_diagnostics, to_grid, walk_forward
from alphasieve.training.samples import SampleTable, _rank_z, _winsor_z, universe_mask

ETF_COSTS = {"stamp_duty_sell": 0.0, "slippage": 0.0003}


def etf_features(panel: Panel) -> dict[str, np.ndarray]:
    close = pd.DataFrame(panel.wide("close").to_numpy(dtype=float))
    amount = pd.DataFrame(panel.wide("amount").to_numpy(dtype=float)).where(lambda a: a > 0)
    ret = close.pct_change()
    out = {f"mom_{n}": (close / close.shift(n) - 1) for n in (20, 60, 120)}
    out["mom_120_20"] = close.shift(20) / close.shift(120) - 1
    out["rev_5"] = -(close / close.shift(5) - 1)
    out["vol_20"] = ret.rolling(20, min_periods=15).std()
    out["vol_60"] = ret.rolling(60, min_periods=40).std()
    out["drawdown_60"] = close / close.rolling(60, min_periods=40).max() - 1
    out["dist_high_250"] = close / close.rolling(250, min_periods=120).max() - 1
    out["ma_gap_60"] = close / close.rolling(60, min_periods=40).mean() - 1
    out["amount_ratio_5_60"] = amount.rolling(5, min_periods=3).mean() / amount.rolling(60, min_periods=30).mean()
    return {k: v.to_numpy() for k, v in out.items()}


def relative_labels(panel: Panel, mask: np.ndarray, horizons: list[int]) -> dict[int, np.ndarray]:
    out = {}
    for h in horizons:
        raw = panel.wide(f"label_{h}d").to_numpy(dtype=float)
        ok = mask & np.isfinite(raw)
        mean = np.where(ok.sum(axis=1) > 0, np.nansum(np.where(ok, raw, 0), axis=1) / np.maximum(ok.sum(axis=1), 1),
                        np.nan)
        out[h] = np.where(ok, raw - mean[:, None], np.nan)
    return out


def mapped_features(panel: Panel, frames: dict[str, pd.DataFrame], mask: np.ndarray,
                    min_coverage: float) -> tuple[dict[str, np.ndarray], dict]:
    """Align the mapped features to the panel; drop those covering < ``min_coverage`` of the masked rows."""
    kept, dropped = {}, {}
    for name, frame in frames.items():
        grid = frame.reindex(index=panel.dates, columns=panel.codes).to_numpy(dtype=float)
        cover = float(np.isfinite(grid[mask]).mean()) if mask.any() else 0.0
        if cover >= min_coverage:
            kept[name] = grid
        else:
            dropped[name] = round(cover, 3)
    return kept, {"kept": sorted(kept), "dropped_low_coverage": dropped}


def build_etf_table(panel: Panel, task, mapped: dict[str, pd.DataFrame] | None = None) -> SampleTable:
    mask = universe_mask(panel, task.universe_train)
    thin = mask.sum(axis=1) < task.sample.min_names_per_date
    mask = mask & ~thin[:, None]
    feats = etf_features(panel)
    report = None
    if mapped:
        extra, report = mapped_features(panel, mapped, mask, task.features.min_feature_coverage)
        feats.update(extra)
    names, grids = [], []
    for name, v in feats.items():
        z = _rank_z(v, mask)
        missing = mask & ~np.isfinite(z)
        if missing[mask].mean() > 0.01:
            names.append(f"missing_{name}")
            grids.append(missing.astype(float))
        names.append(name)
        grids.append(np.where(mask, np.nan_to_num(z, nan=0.0), np.nan))
    rel = relative_labels(panel, mask, task.label.horizons)
    t, c = np.nonzero(mask)
    table = SampleTable(date_pos=t, code_pos=c, X=np.column_stack([g[t, c] for g in grids]).astype(np.float32),
                        feature_names=names)
    for h in task.label.horizons:
        table.R[h] = rel[h][t, c]
        table.Y[h] = _winsor_z(rel[h], mask, *task.label.winsorize)[t, c]
        table.label_end[h] = t + h + 1
    table.predict = np.ones(len(t), dtype=bool)
    table.extra["mask"] = mask
    table.extra["names_per_date"] = mask.sum(axis=1)
    table.extra["mapped_report"] = report
    return table


def top_k_weights(panel: Panel, score: np.ndarray, mask: np.ndarray, k: int, every: int) -> pd.DataFrame:
    rows = {}
    active = np.flatnonzero(np.isfinite(np.where(mask, score, np.nan)).any(axis=1))
    for t in active[::every]:
        s = np.where(mask[t] & np.isfinite(score[t]), score[t], -np.inf)
        n = int(np.isfinite(s).sum())
        if n == 0:
            continue
        pick = np.argsort(-s)[:min(k, n)]
        w = np.zeros(len(panel.codes))
        w[pick] = 1.0 / len(pick)
        rows[panel.dates[t]] = w
    return pd.DataFrame.from_dict(rows, orient="index", columns=panel.codes)


def _rotation(panel: Panel, score: np.ndarray, mask: np.ndarray, task, costs: dict) -> dict:
    weights = top_k_weights(panel, score, mask, task.portfolio.top_k, task.portfolio.rebalance_every)
    sim = simulate(weights, panel, costs, None, universe_mask=mask)
    daily = sim.pop("_daily")
    nav, excess = sim.pop("_nav"), sim.pop("_excess_nav")
    bench_nav = (1 + daily["bench"]).cumprod()
    ret = daily["ret"]
    vol = float(ret.std() * np.sqrt(252))
    return {"execution": sim, "sharpe": float(ret.mean() * 252 / vol) if vol > 0 else float("nan"),
            "max_drawdown": float((nav / nav.cummax() - 1).min()),
            "equal_weight_max_drawdown": float((bench_nav / bench_nav.cummax() - 1).min()),
            "_weights": weights, "_nav": nav, "_excess_nav": excess}


def _acceptance(pf: dict, diag: dict) -> dict:
    rule = mandates.ACCEPTANCE["B"]
    ics = [h["rank_ic"] for h in diag["horizons"].values() if h["rank_ic"] is not None]
    ic = float(np.mean(ics)) if ics else float("nan")
    excess, sharpe = pf["execution"]["annual_excess"], pf["sharpe"]
    checks = {"rank_ic": [ic, rule["rank_ic_min"], ic >= rule["rank_ic_min"]],
              "annual_excess": [excess, rule["annual_excess_min"], excess >= rule["annual_excess_min"]],
              "sharpe": [sharpe, rule["sharpe_min"], sharpe >= rule["sharpe_min"]],
              "max_drawdown_vs_equal_weight": [pf["max_drawdown"], pf["equal_weight_max_drawdown"],
                                               pf["max_drawdown"] >= pf["equal_weight_max_drawdown"]]}
    return {"checks": checks, "passed": all(c[2] for c in checks.values())}


def load_mapped(settings, panel: Panel, frozen: dict) -> tuple[dict[str, pd.DataFrame], dict]:
    from alphasieve.training.etf_industry import read_etf_industry

    frames, info = read_etf_industry(settings, panel.tier, "system", frozen["mode"])
    for key in ("mapping_asof", "holdings_sha256"):
        if info[key] != frozen[key]:
            raise validation_error(f"ETF industry features were built from {key}={info[key]}, the bundle froze"
                                   f" {frozen[key]}; rebuild with 'alphasieve data build-etf-industry'")
    return frames, info


def run_etf_task(settings, task, bundle, panel: Panel, processes=None, threads=None, progress=None):
    frozen = bundle["features"].get("etf_mapping")
    mapped, mapping_info = load_mapped(settings, panel, frozen) if frozen else (None, None)
    table = build_etf_table(panel, task, mapped)
    window = panel.window_mask().to_numpy()
    wf = walk_forward(table, panel.dates, window, task, processes, threads, progress)
    shape = (len(panel.dates), len(panel.codes))
    score = to_grid(wf["score"], table, shape)
    mask = table.extra["mask"]
    rel = relative_labels(panel, mask, task.label.horizons)
    diag = score_diagnostics(score, rel, mask, task.output.decay_lags)
    real = mask & ~panel.mask("is_proxy").to_numpy() if panel.has("is_proxy") else mask
    real = real & (real.sum(axis=1) >= task.sample.min_names_per_date)[:, None]
    real_rel = relative_labels(panel, real, task.label.horizons)
    diag_real = score_diagnostics(score, real_rel, real, task.output.decay_lags)
    proxy_share = float(1 - real[mask].mean()) if mask.any() else 0.0
    costs = {**load_config(settings, "costs").get("b3", {}), **ETF_COSTS}
    pf_real = _rotation(panel, score, real, task, costs)
    pf_all = _rotation(panel, score, mask, task, costs)
    acceptance = _acceptance(pf_real, diag_real)
    names = table.extra["names_per_date"]
    scored_days = np.isfinite(score).any(axis=1)
    real_days = scored_days & real.any(axis=1)
    features = {"names": table.feature_names}
    if frozen:
        features["etf_mapping"] = {**mapping_info, **table.extra["mapped_report"]}
    public = {k: v for k, v in pf_real.items() if not k.startswith("_")}
    result = {"model": {k: v for k, v in wf.items() if k not in ("score", "per_horizon")}, "scores": diag_real,
              "scores_with_proxy_rows": diag, "proxy_row_share": proxy_share, "features": features,
              "universe": {"names_per_scored_day_mean": float(names[scored_days].mean()) if scored_days.any() else 0,
                           "first_scored_day": str(panel.dates[np.flatnonzero(scored_days)[0]].date())
                           if scored_days.any() else None,
                           "first_real_etf_day": str(panel.dates[np.flatnonzero(real_days)[0]].date())
                           if real_days.any() else None},
              "portfolio": {**public, "acceptance": acceptance,
                            "with_proxy_rows": {k: v for k, v in pf_all.items() if not k.startswith("_")}},
              "acceptance": acceptance,
              "headline": {"segment": "real_etf", "annual_excess": pf_real["execution"]["annual_excess"],
                           "sharpe": pf_real["sharpe"]}}
    from alphasieve.training.run import _series

    result["series"] = _series(pf_real["_nav"], pf_real["_excess_nav"])
    return result, {"scores": pd.DataFrame(score, index=panel.dates, columns=panel.codes),
                    "weights": pf_real["_weights"]}
