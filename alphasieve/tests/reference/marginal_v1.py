"""Marginal contribution of a candidate over a baseline feature set (ridge, yearly walk-forward)."""

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from reference.metrics_v1 import rank_corr_series

MIN_TRAIN_YEARS = 2
RIDGE_ALPHA = 10.0


def _prepare(frames: list[pd.DataFrame], valid: pd.DataFrame) -> np.ndarray:
    layers = []
    for frame in frames:
        ranked = frame.where(valid).rank(axis=1, pct=True) - 0.5
        layers.append(ranked.fillna(0.0).to_numpy())
    return np.stack(layers, axis=-1)


def _walk_forward(features: np.ndarray, target: np.ndarray, label: pd.DataFrame, valid: pd.DataFrame,
                  window: pd.Series, embargo: int) -> tuple[float, int]:
    dates = label.index
    years = sorted({d.year for d in dates[window.to_numpy()]})
    valid_np = valid.to_numpy() & np.isfinite(target)
    preds = np.full(target.shape, np.nan)
    folds = 0
    in_window = window.to_numpy()
    for year in years[MIN_TRAIN_YEARS:]:
        test_idx = np.flatnonzero(in_window & (dates.year == year))
        if len(test_idx) == 0:
            continue
        train_end = test_idx[0] - embargo
        train_idx = np.flatnonzero(in_window[:max(train_end, 0)])
        if len(train_idx) < 250:
            continue
        mask = valid_np[train_idx]
        x = features[train_idx][mask]
        y = target[train_idx][mask]
        if len(y) < 1000:
            continue
        model = Ridge(alpha=RIDGE_ALPHA, fit_intercept=True).fit(x, y)
        test_x = features[test_idx].reshape(-1, features.shape[-1])
        preds[test_idx] = model.predict(test_x).reshape(len(test_idx), -1)
        folds += 1
    pred_frame = pd.DataFrame(preds, index=dates, columns=label.columns)
    ic = rank_corr_series(pred_frame, label, valid & pred_frame.notna())
    return (float(ic.mean()) if ic.notna().any() else float("nan")), folds


def marginal_contribution(candidate: pd.DataFrame, baseline: dict[str, pd.DataFrame], label: pd.DataFrame,
                          valid: pd.DataFrame, window: pd.Series, horizon: int) -> dict:
    target_frame = label.where(valid).rank(axis=1, pct=True) - 0.5
    target = target_frame.to_numpy()
    base_frames = list(baseline.values())
    embargo = horizon + 1
    with_feats = _prepare(base_frames + [candidate], valid)
    ic_with, folds = _walk_forward(with_feats, target, label, valid, window, embargo)
    if base_frames:
        ic_without, _ = _walk_forward(with_feats[..., :-1], target, label, valid, window, embargo)
    else:
        ic_without = 0.0
    return {
        "model": "ridge",
        "baseline_features": list(baseline.keys()),
        "ic_with": ic_with,
        "ic_without": ic_without,
        "marginal_ic": ic_with - ic_without if np.isfinite(ic_with) and np.isfinite(ic_without) else float("nan"),
        "folds": folds,
    }
