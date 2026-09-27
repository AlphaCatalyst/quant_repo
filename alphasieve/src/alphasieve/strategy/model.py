"""Model layer (S-6): combine factor features into one out-of-sample score with walk-forward retraining.

Features are centred cross-sectional ranks of each factor; the target is the centred rank of ``label_{h}d``.
At every retrain date the model is fit on the trailing window that ends ``horizon + 1`` trading days before it
(label embargo), then scores the dates up to the next retrain. Only dev-tier panels are accepted.
"""

import numpy as np
import pandas as pd

from alphasieve.data.access import Panel
from alphasieve.errors import permission_denied, validation_error
from alphasieve.evaluation import fastops
from alphasieve.evaluation.metrics import MIN_NAMES, summarize_ic

RIDGE_ALPHA = 10.0
LGBM_PARAMS = {"n_estimators": 300, "learning_rate": 0.05, "num_leaves": 31, "min_child_samples": 200,
               "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.8, "reg_lambda": 1.0, "verbose": -1}


def retrain_dates(dates: pd.DatetimeIndex, window: np.ndarray, every: str, warmup_years: int) -> list[int]:
    idx = np.flatnonzero(window)
    if len(idx) == 0:
        return []
    first = dates[idx[0]] + pd.DateOffset(years=warmup_years)
    positions = [i for i in idx if dates[i] >= first]
    if every == "yearly":
        keys = [dates[i].year for i in positions]
    elif every == "monthly":
        keys = [(dates[i].year, dates[i].month) for i in positions]
    else:
        raise validation_error("retrain must be monthly or yearly")
    out, last = [], None
    for i, key in zip(positions, keys, strict=True):
        if key != last:
            out.append(i)
            last = key
    return out


def _stack(features: np.ndarray, mask: np.ndarray, rows: np.ndarray) -> np.ndarray:
    sub = features[rows]
    return sub[mask[rows]]


def walk_forward_scores(features: dict[str, pd.DataFrame], panel: Panel, horizon: int, model: str = "ridge",
                        retrain: str = "monthly", train_years: int = 5, warmup_years: int = 2,
                        n_jobs: int = 8, seed: int = 0) -> tuple[pd.DataFrame, dict]:
    if panel.tier != "dev":
        raise permission_denied("the model layer only trains on dev-tier panels")
    if not features:
        raise validation_error("no feature factors given")
    dates, codes = panel.dates, panel.codes
    window = panel.window_mask().to_numpy()
    universe = panel.mask("in_universe").to_numpy() & window[:, None]
    label = panel.wide(f"label_{horizon}d").to_numpy(dtype=float)
    names = list(features)
    x = np.empty((len(dates), len(codes), len(names)))
    for j, name in enumerate(names):
        values = features[name].reindex(index=dates, columns=codes).to_numpy(dtype=float)
        x[..., j] = fastops.centered_rank(values, universe)
    target = fastops.centered_rank(label, universe)
    train_mask = universe & np.isfinite(label)
    scores = np.full((len(dates), len(codes)), np.nan)
    retrains = retrain_dates(dates, window, retrain, warmup_years)
    fits, importances = 0, np.zeros(len(names))
    for k, start in enumerate(retrains):
        stop = retrains[k + 1] if k + 1 < len(retrains) else len(dates)
        test_rows = np.arange(start, stop)
        test_rows = test_rows[window[test_rows]]
        lo = dates.searchsorted(dates[start] - pd.DateOffset(years=train_years))
        train_rows = np.arange(lo, max(start - (horizon + 1), lo))
        train_rows = train_rows[window[train_rows]]
        xt, yt = _stack(x, train_mask, train_rows), _stack(target, train_mask, train_rows)
        if len(yt) < 1000 or len(test_rows) == 0:
            continue
        if model == "ridge":
            mx, my = xt.mean(axis=0), yt.mean()
            xc = xt - mx
            w = np.linalg.solve(xc.T @ xc + RIDGE_ALPHA * np.eye(len(names)), xc.T @ (yt - my))
            pred = x[test_rows] @ w + (my - mx @ w)
            importances += np.abs(w)
        elif model == "lgbm":
            import lightgbm as lgb

            reg = lgb.LGBMRegressor(**LGBM_PARAMS, n_jobs=n_jobs, random_state=seed)
            reg.fit(xt, yt)
            pred = reg.predict(x[test_rows].reshape(-1, len(names))).reshape(len(test_rows), len(codes))
            importances += reg.feature_importances_
        else:
            raise validation_error("model must be ridge or lgbm")
        scores[test_rows] = np.where(universe[test_rows], pred, np.nan)
        fits += 1
    score_frame = pd.DataFrame(scores, index=dates, columns=codes)
    ic = pd.Series(fastops.rank_corr(scores, label, universe, MIN_NAMES), index=dates)
    composite = np.where(universe & np.isfinite(scores), x.mean(axis=-1), np.nan)
    ic_equal = pd.Series(fastops.rank_corr(composite, label, universe, MIN_NAMES), index=dates)
    by_year = ic.dropna().groupby(ic.dropna().index.year).mean()
    total = importances.sum() or 1.0
    info = {"model": model, "retrain": retrain, "train_years": train_years, "fits": fits, "features": names,
            "ic": summarize_ic(ic), "ic_equal_weight_composite": summarize_ic(ic_equal),
            "ic_by_year": {str(k): float(v) for k, v in by_year.items()},
            "feature_importance": {n: float(v / total) for n, v in zip(names, importances, strict=True)}}
    return score_frame, info
