"""Fixed-parameter operational refits for an already locked forward cohort.

This module accepts prepared, point-in-time samples. It never selects a model or
loads a panel; the caller supplies the source selection and frozen feature order.
"""

import hashlib
import pickle

import numpy as np
import pandas as pd

from alphasieve.errors import validation_error
from alphasieve.training.engine import LGBM_BASE, group_zscore


def _reject_score_source(bundle: dict | None, task) -> None:
    # This check must precede even inspecting a source path or a panel object.
    if (bundle and bundle.get("features", {}).get("score_source")) or task.score_source is not None:
        raise validation_error("forward cannot consume a score_source bundle; lock its source training bundle")


def lock_selection(selection: dict, horizons: list[int]) -> dict[int, dict]:
    """Freeze the final recorded dev choice for every horizon, with no candidate fallback."""
    out = {}
    for h in horizons:
        records = []
        for key, value in selection.items():
            year, sep, suffix = key.partition("/h")
            if sep and year.isdigit() and suffix == str(h) and isinstance(value, dict):
                records.append((int(year), value.get("chosen")))
        if not records:
            raise validation_error(f"source selection has no chosen model for horizon {h}")
        chosen = max(records)[1]
        if not isinstance(chosen, dict) or chosen.get("family") not in {"ridge", "lgbm", "lambdarank"} \
                or not isinstance(chosen.get("params"), dict):
            raise validation_error(f"invalid source selection for horizon {h}")
        out[h] = {"family": chosen["family"], "params": dict(chosen["params"])}
    return out


def _fit(cand: dict, x: np.ndarray, y: np.ndarray, group: np.ndarray, seed: int, threads: int):
    family, params = cand["family"], cand["params"]
    if family == "ridge":
        alpha = float(params.get("alpha", 10.0))
        mx, my = x.mean(axis=0), y.mean()
        xc = x - mx
        weight = np.linalg.solve(xc.T @ xc + alpha * len(y) / 1000.0 * np.eye(x.shape[1]),
                                 xc.T @ (y - my))
        return ("ridge", weight, float(my - mx @ weight))
    import lightgbm as lgb

    if family == "lgbm":
        model = lgb.LGBMRegressor(**{**LGBM_BASE, **params}, n_jobs=threads, random_state=seed)
        model.fit(x, y)
    elif family == "lambdarank":
        order = np.argsort(group, kind="stable")
        xs, ys, gs = x[order], y[order], group[order]
        rel = pd.Series(ys).groupby(gs).transform(
            lambda v: np.floor(v.rank(pct=True) * 4.999)).to_numpy().astype(int)
        _, counts = np.unique(gs, return_counts=True)
        model = lgb.LGBMRanker(**{**LGBM_BASE, "objective": "lambdarank", **params}, n_jobs=threads,
                               random_state=seed)
        model.fit(xs, rel, group=counts)
    else:
        raise validation_error(f"unknown forward model family {family}")
    return (family, model)


def _predict(model, x: np.ndarray) -> np.ndarray:
    if model[0] == "ridge":
        return x @ model[1] + model[2]
    return model[1].predict(x)


def fit_asof(table, dates: pd.DatetimeIndex, asof, task, selection: dict[int, dict],
             expected_features: list[str], *, bundle: dict | None = None, previous: dict | None = None,
             window_start: int = 0, threads: int = 1) -> dict:
    """Bootstrap or refit on the first available trading day of a month.

    ``asof`` is a date in ``dates``. The caller must construct ``table`` from
    immutable as-of inputs; this function independently enforces purge and
    mature label endpoints before fitting.
    """
    _reject_score_source(bundle, task)
    if table.feature_names != expected_features or not expected_features:
        raise validation_error("forward feature columns differ from the locked order")
    if not isinstance(dates, pd.DatetimeIndex) or dates.has_duplicates or not dates.is_monotonic_increasing:
        raise validation_error("forward dates must be unique and ordered")
    stamp = pd.Timestamp(asof)
    loc = dates.get_indexer([stamp])
    if loc[0] < 0:
        raise validation_error("asof must be a trading date in the sealed context")
    p = int(loc[0])
    selection = {int(h): candidate for h, candidate in selection.items()}
    if previous is not None:
        if previous["asof"] > stamp:
            raise validation_error("cannot use a future model snapshot")
        if previous["selection"] != selection:
            raise validation_error("forward model selection changed inside a cohort")
        if (previous["asof"].year, previous["asof"].month) == (stamp.year, stamp.month):
            return previous
    if task.split.retrain != "monthly":
        raise validation_error("forward v1 requires a locked monthly retrain calendar")
    if set(selection) != set(task.label.horizons):
        raise validation_error("forward source selection must cover every horizon")
    if p < window_start:
        raise validation_error("asof precedes the locked training start")
    if task.split.window == "rolling":
        lo = int(dates.searchsorted(stamp - pd.DateOffset(months=int(round(task.split.train_years * 12)))))
    else:
        lo = window_start
    lo = max(lo, window_start)
    models, counts, max_date, max_label = {}, {}, {}, {}
    for h in task.label.horizons:
        if h not in table.Y or h not in table.label_end:
            raise validation_error(f"forward sample table lacks mature labels for horizon {h}")
        label_end = np.asarray(table.label_end[h])
        rows = np.flatnonzero((table.date_pos >= lo) & (table.date_pos < p - task.sample.purge_days)
                             & (label_end <= p) & np.isfinite(table.Y[h]))
        if len(rows) < task.sample.min_train_rows:
            raise validation_error(f"insufficient mature training rows for horizon {h}", rows=len(rows))
        candidate = selection[h]
        if candidate.get("family") not in {"ridge", "lgbm", "lambdarank"} or not isinstance(
                candidate.get("params"), dict):
            raise validation_error(f"invalid locked model for horizon {h}")
        seeds = task.search.seeds[:1] if candidate["family"] == "ridge" else task.search.seeds
        models[h] = [_fit(candidate, table.X[rows], table.Y[h][rows], table.group[rows], seed, threads)
                     for seed in seeds]
        counts[h] = len(rows)
        max_date[h] = str(dates[int(table.date_pos[rows].max())].date())
        max_label[h] = str(dates[int(label_end[rows].max())].date())
    digest = hashlib.sha256(pickle.dumps(models, protocol=4)).hexdigest()
    return {"asof": stamp, "dates": dates, "models": models, "model_digest": digest,
            "features": list(expected_features),
            "selection": selection, "train_rows": counts, "max_train_date": max_date,
            "max_label_end": max_label, "cutoff": str(stamp.date()), "purge_days": task.sample.purge_days,
            "weights": task.ensemble.horizon_weights or {h: 1.0 / len(models) for h in models}}


def score_asof(snapshot: dict, table, asof, *, bundle: dict | None = None, task=None,
               dates: pd.DatetimeIndex | None = None) -> np.ndarray:
    """Score only rows at ``asof`` using the last sealed model snapshot."""
    if task is not None:
        _reject_score_source(bundle, task)
    elif bundle and bundle.get("features", {}).get("score_source"):
        raise validation_error("forward cannot consume a score_source bundle")
    if table.feature_names != snapshot["features"]:
        raise validation_error("forward feature columns differ from the locked order")
    stamp = pd.Timestamp(asof)
    if stamp < snapshot["asof"]:
        raise validation_error("cannot score before the model cutoff")
    if (stamp.year, stamp.month) != (snapshot["asof"].year, snapshot["asof"].month):
        raise validation_error("monthly forward model refit is due before scoring")
    # The sample's date positions use the same sealed date index as fit_asof.
    dates = dates if dates is not None else snapshot.get("dates")
    if dates is None:
        raise validation_error("snapshot needs sealed date index for scoring")
    loc = dates.get_indexer([stamp])
    if loc[0] < 0:
        raise validation_error("asof must be a trading date in the sealed context")
    test = np.flatnonzero((table.date_pos == loc[0]) & table.predict)
    score = np.full(len(table.date_pos), np.nan)
    if not len(test):
        return score
    combined = np.zeros(len(test))
    have = np.zeros(len(test), dtype=bool)
    for h, models in snapshot["models"].items():
        per_seed = [group_zscore(_predict(m, table.X[test]), table.group[test]) for m in models]
        z = group_zscore(np.nanmean(np.vstack(per_seed), axis=0), table.group[test])
        combined += snapshot["weights"][h] * np.nan_to_num(z)
        have |= np.isfinite(z)
    score[test] = np.where(have, combined, np.nan)
    return score
