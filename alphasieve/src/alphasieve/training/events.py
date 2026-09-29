"""Mandate C: post-earnings drift as an event-level training task (docs/19 §3).

Events are the first panel day on which a new statement period (``ws_stat_date``), a new earnings forecast
(``fc_age == 0``) or a new preliminary result (a change in the ``ex_*`` values) becomes visible. The panel shows a
statement from the first trading day after its announcement, so an event sample at day t trades at the open of
t+1: labels are ``label_{h}d`` minus the benchmark's open-to-open return over the same window (CAR). Features are
ranked within (event type, calendar month). The tradable rule enters an event whose score beats the 90th
percentile of the scores of the events in the previous 250 trading days, so no threshold uses future scores.
"""

import numpy as np
import pandas as pd

from alphasieve.config import load_config
from alphasieve.data.access import Panel
from alphasieve.errors import validation_error
from alphasieve.strategy.execution import simulate
from alphasieve.training import mandates
from alphasieve.training.engine import group_zscore, walk_forward
from alphasieve.training.samples import BENCHMARK_OF, SampleTable, forward_return, universe_mask

EVENT_TYPES = ("ws_report", "forecast", "express")
EVENT_FEATURES = ("ws_np_surprise_q", "ws_np_growth_q_yoy", "ws_rev_growth_yoy", "fc_chg_mid", "fc_positive",
                  "ex_eps_chg", "ex_roe", "ex_gr_yoy")
LOOKBACK = 250
ENTRY_QUANTILE = 0.9
MAX_NAME_WEIGHT = 0.05
MIN_SLOTS = 20


def _changed(values: pd.DataFrame) -> np.ndarray:
    cur, prev = values, values.shift(1)
    both_nan = cur.isna() & prev.isna()
    return (cur.notna() & ~(cur.eq(prev) | both_nan)).to_numpy()


def detect_events(panel: Panel, types: list[str]) -> pd.DataFrame:
    """One row per (date_pos, code_pos, type); a period that re-appears after a gap is not a new event."""
    rows = []
    if "ws_report" in types and panel.has("ws_stat_date"):
        stat = panel.wide("ws_stat_date")
        new = _changed(stat)
        seen = stat.where(new).stack().dropna()
        first = ~seen.reset_index().duplicated(["code", 0]).to_numpy()
        t = panel.dates.get_indexer(seen.index.get_level_values(0))
        c = pd.Index(panel.codes).get_indexer(seen.index.get_level_values(1))
        rows.append(pd.DataFrame({"t": t[first], "c": c[first], "type": "ws_report"}))
    if "forecast" in types and panel.has("fc_age"):
        t, c = np.nonzero(panel.wide("fc_age").to_numpy() == 0)
        rows.append(pd.DataFrame({"t": t, "c": c, "type": "forecast"}))
    if "express" in types and panel.has("ex_eps_chg"):
        new = np.zeros((len(panel.dates), len(panel.codes)), dtype=bool)
        for f in ("ex_eps_chg", "ex_roe", "ex_gr_yoy"):
            if panel.has(f):
                new |= _changed(panel.wide(f))
        t, c = np.nonzero(new)
        rows.append(pd.DataFrame({"t": t, "c": c, "type": "express"}))
    if not rows:
        raise validation_error("the panel has none of the event fields for the requested event types")
    return pd.concat(rows, ignore_index=True).drop_duplicates(["t", "c", "type"]).sort_values(["t", "c"])


def forward_benchmark(panel: Panel, name: str, h: int) -> np.ndarray:
    col = f"{name}_open"
    if panel.benchmark is None or col not in panel.benchmark.columns:
        raise validation_error(f"benchmark {name} opens are not in the panel")
    b = panel.benchmark.set_index(pd.to_datetime(panel.benchmark["date"]))[col].reindex(panel.dates).ffill()
    px = b.to_numpy(dtype=float)
    out = np.full(len(px), np.nan)
    out[: len(px) - 1 - h] = px[1 + h:] / px[1:len(px) - h] - 1
    return out


def _group_rank_z(values: np.ndarray, groups: np.ndarray) -> np.ndarray:
    s = pd.Series(values)
    r = s.groupby(groups).rank(pct=True)
    return group_zscore(r.to_numpy(), groups)


def build_event_table(panel: Panel, task, bench: str) -> tuple[SampleTable, pd.DataFrame]:
    lab = task.label
    ev = detect_events(panel, lab.event_types)
    window = panel.window_mask().to_numpy()
    uni = universe_mask(panel, task.universe_train)
    ev = ev[window[ev["t"]] & uni[ev["t"], ev["c"]]].reset_index(drop=True)
    t, c = ev["t"].to_numpy(), ev["c"].to_numpy()
    month = panel.dates[t].year * 12 + panel.dates[t].month
    type_month = pd.Series(ev["type"].to_numpy()).astype(str) + ":" + pd.Series(month).astype(str)
    names, cols = [], []
    for et in EVENT_TYPES:
        if et in lab.event_types:
            names.append(f"is_{et}")
            cols.append((ev["type"].to_numpy() == et).astype(float))
    fields = list(dict.fromkeys([f for f in EVENT_FEATURES if panel.has(f)] + list(task.features.panel_fields)))
    pre = pd.DataFrame(panel.wide("ret_1d").to_numpy(dtype=float)).rolling(20, min_periods=10).sum().to_numpy()
    grids = {f: panel.wide(f).to_numpy(dtype=float) for f in fields if panel.has(f)}
    grids["pre_event_ret_20d"] = pre
    grids["log_circ_mv"] = np.log(panel.wide("circ_mv").to_numpy(dtype=float).clip(min=1.0))
    for f, g in grids.items():
        v = g[t, c]
        z = _group_rank_z(v, type_month.to_numpy())
        if np.isnan(z).mean() > 0.01:
            names.append(f"missing_{f}")
            cols.append(np.isnan(z).astype(float))
        names.append(f)
        cols.append(np.nan_to_num(z, nan=0.0))
    table = SampleTable(date_pos=t, code_pos=c, X=np.column_stack(cols).astype(np.float32), feature_names=names,
                        group=month.to_numpy())
    for h in lab.horizons:
        raw = forward_return(panel, h)[t, c] - forward_benchmark(panel, bench, h)[t]
        table.R[h] = raw
        table.Y[h] = _group_rank_z(raw, month.to_numpy())
        table.label_end[h] = t + h + 1
    table.predict = np.ones(len(t), dtype=bool)
    return table, ev


def decile_report(score: np.ndarray, car: np.ndarray, block: np.ndarray) -> dict:
    ok = np.isfinite(score) & np.isfinite(car)
    df = pd.DataFrame({"s": score[ok], "car": car[ok], "b": block[ok]})
    if len(df) < 200:
        return {"events": int(len(df))}
    df["d"] = df.groupby("b")["s"].transform(lambda v: pd.qcut(v.rank(method="first"), 10, labels=False))
    means = df.groupby("d")["car"].mean()
    top, bottom = df.loc[df["d"] == 9, "car"], df.loc[df["d"] == 0, "car"]
    se = np.sqrt(top.var() / len(top) + bottom.var() / len(bottom))
    mono = float(pd.Series(means.to_numpy()).corr(pd.Series(np.arange(len(means))), method="spearman"))
    return {"events": int(len(df)), "decile_mean_car": [round(float(v), 5) for v in means],
            "top_minus_bottom": float(top.mean() - bottom.mean()), "t_stat": float((top.mean() - bottom.mean()) / se),
            "monotonicity": mono, "note": "t ignores overlap between events of the same stock and date"}


def event_weights(panel: Panel, ev: pd.DataFrame, score: np.ndarray, holding: int) -> tuple[pd.DataFrame, dict]:
    """Target weights for entries that beat the trailing threshold.

    Each entry gets a fixed slot of ``1 / K`` (K = trailing 250-day mean of open positions, at least 20) that is
    kept until its exit, so other positions are not re-weighted when names come and go. Targets are emitted only
    on days when the set of positions changes; rows above 100% are scaled down.
    """
    n_dates = len(panel.dates)
    order = np.argsort(ev["t"].to_numpy(), kind="stable")
    t_all, c_all, s_all = ev["t"].to_numpy()[order], ev["c"].to_numpy()[order], score[order]
    entries = []
    for i in range(len(t_all)):
        if not np.isfinite(s_all[i]):
            continue
        lo = np.searchsorted(t_all, t_all[i] - LOOKBACK)
        past = s_all[lo:np.searchsorted(t_all, t_all[i])]
        past = past[np.isfinite(past)]
        if len(past) >= 100 and s_all[i] >= np.quantile(past, ENTRY_QUANTILE):
            entries.append((int(t_all[i]), int(c_all[i])))
    counts = np.zeros(n_dates)
    for t, _ in entries:
        counts[t:min(n_dates, t + holding)] += 1
    w = np.zeros((n_dates, len(panel.codes)))
    for t, c in entries:
        past = counts[max(0, t - LOOKBACK):t]
        k = max(MIN_SLOTS, float(past.mean()) if len(past) else MIN_SLOTS)
        w[t:min(n_dates, t + holding), c] += 1.0 / k
    w = np.minimum(w, MAX_NAME_WEIGHT)
    total = w.sum(axis=1, keepdims=True)
    w = np.where(total > 1, w / np.where(total > 0, total, 1), w)
    held = w > 0
    change = np.ones(n_dates, dtype=bool)
    change[1:] = (held[1:] != held[:-1]).any(axis=1)
    days = np.flatnonzero(change & (held.any(axis=1) | np.r_[False, held[:-1].any(axis=1)]))
    frame = pd.DataFrame(w[days], index=panel.dates[days], columns=panel.codes)
    active = held.sum(axis=1)
    return frame, {"entries": len(entries), "mean_positions": float(active[active > 0].mean()) if active.any() else 0.0,
                   "invested_mean": float(w.sum(axis=1)[active > 0].mean()) if active.any() else 0.0,
                   "rebalance_days": int(len(days))}


def run_event_task(settings, task, bundle, panel: Panel, processes=None, threads=None, progress=None):
    bench = task.portfolio.benchmark or BENCHMARK_OF.get(task.universe_train, "zz500")
    table, ev = build_event_table(panel, task, bench)
    window = panel.window_mask().to_numpy()
    wf = walk_forward(table, panel.dates, window, task, processes, threads, progress)
    score = wf["score"]
    h_max = max(task.label.horizons)
    quarter = panel.dates[table.date_pos].year * 4 + (panel.dates[table.date_pos].month - 1) // 3
    report = {str(h): decile_report(score, table.R[h], np.asarray(quarter)) for h in task.label.horizons}
    weights, entry_info = event_weights(panel, ev, score, task.portfolio.holding_days)
    costs = load_config(settings, "costs").get("b3", {})
    sim = simulate(weights, panel, costs, None, aum=task.portfolio.aum,
                   max_participation=task.portfolio.max_participation)
    vs_index = simulate(weights, panel, costs, bench, aum=task.portfolio.aum,
                        max_participation=task.portfolio.max_participation)
    vs_index = {k: v for k, v in vs_index.items() if not k.startswith("_")}
    rule = mandates.ACCEPTANCE["C"]
    main = report[str(h_max)]
    checks = {"car_spread_t": [main.get("t_stat"), rule["car_spread_t_min"],
                               (main.get("t_stat") or 0) >= rule["car_spread_t_min"]],
              "decile_monotonicity": [main.get("monotonicity"), rule["decile_monotonicity_min"],
                                      (main.get("monotonicity") or 0) >= rule["decile_monotonicity_min"]],
              "annual_excess": [sim["annual_excess"], rule["annual_excess_min"],
                                sim["annual_excess"] >= rule["annual_excess_min"]]}
    acceptance = {"checks": checks, "passed": all(c[2] for c in checks.values())}
    nav, excess = sim.pop("_nav"), sim.pop("_excess_nav")
    sim.pop("_daily", None)
    by_type = ev.assign(scored=np.isfinite(score)).groupby("type")["scored"].agg(["size", "sum"])
    result = {"model": {k: v for k, v in wf.items() if k not in ("score", "per_horizon")},
              "events": {"total": int(len(ev)), "by_type": {k: {"events": int(r["size"]), "scored": int(r["sum"])}
                                                            for k, r in by_type.iterrows()}},
              "features": {"names": table.feature_names}, "car_deciles": report,
              "portfolio": {"entry_rule": entry_info, "benchmark_basis": "total-return universe proxy (cap-weighted)",
                            "execution": sim, "execution_vs_price_index": vs_index, "acceptance": acceptance},
              "acceptance": acceptance, "headline": {"car_spread_t": main.get("t_stat"),
                                                     "annual_excess": sim["annual_excess"]}}
    from alphasieve.training.run import _series

    result["series"] = _series(nav, excess)
    scores = pd.DataFrame({"date": panel.dates[table.date_pos], "code": np.asarray(panel.codes)[table.code_pos],
                           "type": ev["type"].to_numpy(), "score": score})
    return result, {"event_scores": scores.set_index(["date", "code"])[["score"]], "weights": weights}
