"""Marginal contribution of a candidate over a baseline feature set (ridge, yearly walk-forward).

Ridge is solved in closed form from per-date sufficient statistics, which is exactly sklearn's
``Ridge(alpha, fit_intercept=True)`` (centred normal equations, intercept unpenalised). The baseline block
of features and statistics does not depend on the candidate, so it is cached per ``cache_key`` (panel
signature, horizon, baseline names); only the candidate's cross terms are computed per evaluation.
"""

from collections import OrderedDict

import numpy as np
import pandas as pd

from alphasieve.evaluation import fastops
from alphasieve.evaluation.metrics import MIN_NAMES

MIN_TRAIN_YEARS = 2
RIDGE_ALPHA = 10.0
_BASE_CACHE: OrderedDict = OrderedDict()
_BASE_CACHE_SIZE = 2


def _centered_rank(frame: pd.DataFrame, valid: np.ndarray, like: pd.DataFrame) -> np.ndarray:
    values = frame.reindex(index=like.index, columns=like.columns).to_numpy(dtype=float)
    return fastops.centered_rank(values, valid)


def _base_block(baseline: dict[str, pd.DataFrame], valid: np.ndarray, target: np.ndarray, mask: np.ndarray,
                like: pd.DataFrame, chunk: int = 256) -> dict:
    t, n = valid.shape
    k = len(baseline)
    feats = np.empty((t, n, k))
    for j, frame in enumerate(baseline.values()):
        feats[..., j] = _centered_rank(frame, valid, like)
    y = np.where(mask, target, 0.0)
    xx = np.zeros((t, k, k))
    xy = np.zeros((t, k))
    sx = np.zeros((t, k))
    for lo in range(0, t, chunk):
        x = np.where(mask[lo:lo + chunk, :, None], feats[lo:lo + chunk], 0.0)
        sx[lo:lo + chunk] = x.sum(axis=1)
        xx[lo:lo + chunk] = x.transpose(0, 2, 1) @ x
        xy[lo:lo + chunk] = (x * y[lo:lo + chunk, :, None]).sum(axis=1)
    return {"feats": feats, "sx": sx, "xx": xx, "xy": xy, "y": y}


def _full_stats(base: dict, cand: np.ndarray, mask: np.ndarray) -> dict:
    t, k = base["sx"].shape
    c = np.where(mask, cand, 0.0)
    y = base["y"]
    xx = np.zeros((t, k + 1, k + 1))
    xx[:, :k, :k] = base["xx"]
    if k:
        cross = np.einsum("tnk,tn->tk", np.where(mask[..., None], base["feats"], 0.0), c)
        xx[:, :k, k] = cross
        xx[:, k, :k] = cross
    xx[:, k, k] = (c * c).sum(axis=1)
    return {"n": mask.sum(axis=1).astype(float), "sy": y.sum(axis=1),
            "sx": np.concatenate([base["sx"], c.sum(axis=1)[:, None]], axis=1), "xx": xx,
            "xy": np.concatenate([base["xy"], (c * y).sum(axis=1)[:, None]], axis=1)}


def _solve(stats: dict, idx: np.ndarray, k: int) -> tuple[np.ndarray, float]:
    n = stats["n"][idx].sum()
    sx = stats["sx"][idx, :k].sum(axis=0)
    sy = stats["sy"][idx].sum()
    xx = stats["xx"][idx, :k, :k].sum(axis=0)
    xy = stats["xy"][idx, :k].sum(axis=0)
    mx, my = sx / n, sy / n
    w = np.linalg.solve(xx - n * np.outer(mx, mx) + RIDGE_ALPHA * np.eye(k), xy - n * mx * my)
    return w, my - mx @ w


def _walk_forward(base_feats: np.ndarray, cand: np.ndarray, stats: dict, label: np.ndarray, valid: np.ndarray,
                  dates: pd.DatetimeIndex, window: np.ndarray, embargo: int, with_candidate: bool) -> tuple[float, int]:
    kb = base_feats.shape[-1]
    k = kb + 1 if with_candidate else kb
    years = sorted({d.year for d in dates[window]})
    preds = np.full(label.shape, np.nan)
    folds = 0
    for year in years[MIN_TRAIN_YEARS:]:
        test_idx = np.flatnonzero(window & (dates.year == year))
        if len(test_idx) == 0:
            continue
        train_end = test_idx[0] - embargo
        train_idx = np.flatnonzero(window[:max(train_end, 0)])
        if len(train_idx) < 250 or stats["n"][train_idx].sum() < 1000:
            continue
        w, b = _solve(stats, train_idx, k)
        pred = base_feats[test_idx] @ w[:kb] + b
        if with_candidate:
            pred = pred + cand[test_idx] * w[kb]
        preds[test_idx] = pred
        folds += 1
    ic = fastops.rank_corr(preds, label, valid & np.isfinite(preds), MIN_NAMES)
    return (float(np.nanmean(ic)) if np.isfinite(ic).any() else float("nan")), folds


def marginal_contribution(candidate: pd.DataFrame, baseline: dict[str, pd.DataFrame], label: pd.DataFrame,
                          valid: pd.DataFrame, window: pd.Series, horizon: int, cache_key=None) -> dict:
    valid_np = valid.to_numpy(dtype=bool)
    label_np = label.to_numpy(dtype=float)
    target = fastops.centered_rank(label_np, valid_np)
    mask = valid_np & np.isfinite(label_np)
    target = np.where(mask, target, np.nan)
    key = (cache_key, tuple(baseline)) if cache_key is not None else None
    base = _BASE_CACHE.get(key) if key is not None else None
    if base is None:
        base = _base_block(baseline, valid_np, target, mask, label)
        if key is not None:
            _BASE_CACHE[key] = base
            while len(_BASE_CACHE) > _BASE_CACHE_SIZE:
                _BASE_CACHE.popitem(last=False)
    else:
        _BASE_CACHE.move_to_end(key)
    cand = _centered_rank(candidate, valid_np, label)
    stats = _full_stats(base, cand, mask)
    dates, win = label.index, window.to_numpy()
    embargo = horizon + 1
    args = (base["feats"], cand, stats, label_np, valid_np, dates, win, embargo)
    ic_with, folds = _walk_forward(*args, with_candidate=True)
    ic_without = _walk_forward(*args, with_candidate=False)[0] if baseline else 0.0
    return {
        "model": "ridge",
        "baseline_features": list(baseline.keys()),
        "ic_with": ic_with,
        "ic_without": ic_without,
        "marginal_ic": ic_with - ic_without if np.isfinite(ic_with) and np.isfinite(ic_without) else float("nan"),
        "folds": folds,
    }
