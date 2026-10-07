"""Samples, labels and features for training tasks (docs/mandates/training-tasks §1, §2.1–2.3).

Everything here works on wide (date x code) grids of one dev-tier panel and returns a long sample table:
``date_pos`` / ``code_pos`` index the grid, ``X`` holds preprocessed features, ``Y[h]`` the training label for
horizon ``h``, ``R[h]`` the raw forward return used for reporting. Cross-sectional transforms use one date at a
time, so no information crosses dates; nothing is fitted on future rows.
"""

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from alphasieve.data.access import Panel
from alphasieve.errors import validation_error

EVENT_NEUTRAL_FIELDS = ("fc_chg_mid", "fc_positive", "ex_eps_chg", "ex_roe", "ex_gr_yoy")
BETA_WINDOW = 60
UNIVERSE_FLAGS = {"csi800": None, "csi500": "in_zz500", "hs300": "in_hs300", "ashare_all": None,
                  "ashare_2020": None, "hs300_2020": None, "etf_sector": None}
BENCHMARK_OF = {"csi500": "zz500", "csi800": "csi800", "hs300": "hs300", "hs300_2020": "hs300",
                "ashare_all": "zz500", "ashare_2020": "zz500"}


@dataclass
class SampleTable:
    date_pos: np.ndarray
    code_pos: np.ndarray
    X: np.ndarray
    feature_names: list[str]
    Y: dict[int, np.ndarray] = field(default_factory=dict)
    R: dict[int, np.ndarray] = field(default_factory=dict)
    group: np.ndarray | None = None          # ranking / IC group (a date for cross-sectional tasks)
    label_end: dict[int, np.ndarray] = field(default_factory=dict)
    predict: np.ndarray | None = None        # rows that receive an out-of-sample score
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.group is None:
            self.group = self.date_pos


def universe_mask(panel: Panel, name: str, within_window: bool = True) -> np.ndarray:
    if name not in UNIVERSE_FLAGS:
        raise validation_error(f"unknown training universe {name}", allowed=sorted(UNIVERSE_FLAGS))
    mask = panel.mask("in_universe").to_numpy().copy()
    flag = UNIVERSE_FLAGS[name]
    if flag is not None:
        if not panel.has(flag):
            raise validation_error(f"panel has no {flag}; universe {name} needs the csi800 panel")
        mask &= panel.mask(flag).to_numpy()
    return mask & panel.window_mask().to_numpy()[:, None] if within_window else mask


def benchmark_returns(panel: Panel, name: str, field_name: str = "close") -> np.ndarray | None:
    if panel.benchmark is None or f"{name}_{field_name}" not in panel.benchmark.columns:
        return None
    b = panel.benchmark.set_index(pd.to_datetime(panel.benchmark["date"]))[f"{name}_{field_name}"]
    px = b.reindex(panel.dates).ffill().to_numpy(dtype=float)
    out = np.full(len(px), np.nan)
    out[1:] = px[1:] / px[:-1] - 1
    return out


def forward_return(panel: Panel, h: int) -> np.ndarray:
    """``label_{h}d`` when the panel has it, else the same definition computed here: open(t+1+h) / open(t+1) - 1
    on adjusted opens, missing when t+1 is not buyable or t+1+h falls after the panel window."""
    if panel.has(f"label_{h}d"):
        return panel.wide(f"label_{h}d").to_numpy(dtype=float)
    px = panel.wide("open").to_numpy(dtype=float)
    buy = panel.mask("tradable_buy").to_numpy()
    n = len(px)
    out = np.full(px.shape, np.nan)
    if n > h + 1:
        with np.errstate(invalid="ignore", divide="ignore"):
            out[: n - 1 - h] = px[1 + h:] / px[1:n - h] - 1
        out[: n - 1][~buy[1:]] = np.nan
    last = int(np.flatnonzero(panel.window_mask().to_numpy())[-1])
    out[max(0, last - h):] = np.nan
    return out


def rolling_beta(panel: Panel, market: np.ndarray, window: int = BETA_WINDOW) -> np.ndarray:
    r = pd.DataFrame(panel.wide("ret_1d").to_numpy(dtype=float), index=panel.dates)
    m = pd.Series(market, index=panel.dates)
    rm = r.mul(m, axis=0)
    mp = window // 2
    cov = rm.rolling(window, min_periods=mp).mean() - r.rolling(window, min_periods=mp).mean().mul(
        m.rolling(window, min_periods=mp).mean(), axis=0)
    var = m.rolling(window, min_periods=mp).var(ddof=0)
    return cov.div(var.where(var > 0), axis=0).to_numpy()


def rolling_vol(panel: Panel, window: int = 20) -> np.ndarray:
    r = pd.DataFrame(panel.wide("ret_1d").to_numpy(dtype=float), index=panel.dates)
    return r.rolling(window, min_periods=window // 2).std().to_numpy()


def _winsor_z(values: np.ndarray, mask: np.ndarray, lo: float, hi: float) -> np.ndarray:
    out = np.full(values.shape, np.nan)
    for t in range(values.shape[0]):
        ok = mask[t] & np.isfinite(values[t])
        if ok.sum() < 3:
            continue
        v = values[t, ok]
        a, b = np.quantile(v, [lo, hi])
        v = np.clip(v, a, b)
        sd = v.std()
        if sd > 0:
            out[t, ok] = (v - v.mean()) / sd
    return out


def _rank_centered(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = np.full(values.shape, np.nan)
    for t in range(values.shape[0]):
        ok = mask[t] & np.isfinite(values[t])
        if ok.sum() < 3:
            continue
        out[t, ok] = pd.Series(values[t, ok]).rank(pct=True).to_numpy() - 0.5
    return out


def residualize(values: np.ndarray, mask: np.ndarray, industry_codes: np.ndarray, extra: list[np.ndarray],
                min_names: int = 100) -> np.ndarray:
    """Per-date OLS residual on industry dummies plus ``extra`` regressors; industry-mean residual when thin."""
    out = np.full(values.shape, np.nan)
    n_ind = int(industry_codes.max()) + 1 if len(industry_codes) else 0
    for t in range(values.shape[0]):
        ok = mask[t] & np.isfinite(values[t])
        cols = [e[t] for e in extra]
        for c in cols:
            ok &= np.isfinite(c)
        if ok.sum() < 3:
            continue
        y = values[t, ok]
        ind = industry_codes[ok]
        dummies = np.zeros((ok.sum(), n_ind))
        dummies[np.arange(ok.sum()), ind] = 1.0
        dummies = dummies[:, dummies.sum(axis=0) > 0]
        if ok.sum() >= min_names and cols:
            design = np.column_stack([dummies] + [c[ok] for c in cols])
            beta, *_ = np.linalg.lstsq(design, y, rcond=None)
            out[t, ok] = y - design @ beta
        else:
            means = (dummies.T @ y) / dummies.sum(axis=0)
            out[t, ok] = y - dummies @ means
    return out


def industry_codes(panel: Panel) -> np.ndarray:
    ind = panel.industry()
    return pd.Categorical(ind.to_numpy()).codes.astype(int)


def neutral_regressors(panel: Panel, names: list[str], beta: np.ndarray | None) -> list[np.ndarray]:
    lmv = np.log(panel.wide("circ_mv").to_numpy(dtype=float).clip(min=1.0))
    out = []
    for n in names:
        if n == "log_circ_mv":
            out.append(lmv)
        elif n == "log_circ_mv_sq":
            z = lmv - np.nanmean(lmv, axis=1, keepdims=True)
            out.append(z * z)
        elif n == "beta_60d":
            if beta is None:
                raise validation_error("beta_60d needs a benchmark index series in the panel")
            out.append(beta)
    return out


def labels(panel: Panel, horizons: list[int], train_mask: np.ndarray, kind: str, neutralize: list[str],
           winsor: tuple[float, float], standardize: str, beta: np.ndarray | None,
           min_names: int = 100) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray]]:
    """(training label grid, raw forward return grid) per horizon."""
    ind = industry_codes(panel)
    regs = neutral_regressors(panel, [n for n in neutralize if n != "industry"], beta)
    ys, raws = {}, {}
    for h in horizons:
        raw = forward_return(panel, h)
        raws[h] = raw
        y = raw
        if kind in ("regression_residual", "rank_residual", "residual_plus_basis") and neutralize:
            y = residualize(raw, train_mask, ind, regs, min_names) if "industry" in neutralize else raw
        if standardize == "cross_sectional_zscore" or kind == "regression_residual":
            y = _winsor_z(y, train_mask, *winsor)
        elif standardize == "cross_sectional_rank" or kind == "rank_residual":
            y = _rank_centered(y, train_mask)
        ys[h] = y
    return ys, raws


def _row_z(ranked: np.ndarray) -> np.ndarray:
    with warnings.catch_warnings(), np.errstate(invalid="ignore", divide="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)
        mu = np.nanmean(ranked, axis=1, keepdims=True)
        sd = np.nanstd(ranked, axis=1, keepdims=True)
        return np.where(sd > 0, (ranked - mu) / sd, np.nan)


def _industry_rank_z(values: np.ndarray, mask: np.ndarray, ind: np.ndarray) -> np.ndarray:
    df = pd.DataFrame(np.where(mask, values, np.nan))
    ranked = np.full(values.shape, np.nan)
    for g in np.unique(ind):
        cols = np.flatnonzero(ind == g)
        if len(cols) < 2:
            ranked[:, cols] = np.where(np.isfinite(df.iloc[:, cols].to_numpy()), 0.5, np.nan)
            continue
        ranked[:, cols] = df.iloc[:, cols].rank(axis=1, pct=True).to_numpy()
    return _row_z(ranked)


def _rank_z(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    ranked = pd.DataFrame(np.where(mask, values, np.nan)).rank(axis=1, pct=True).to_numpy()
    return _row_z(ranked)


def feature_grids(panel: Panel, frames: dict[str, pd.DataFrame], mask: np.ndarray, preprocess: str,
                  missing_policy: str, min_coverage: float) -> tuple[list[str], list[np.ndarray], dict]:
    """Preprocessed feature grids; fields below ``min_coverage`` inside ``mask`` are dropped (reported)."""
    ind = industry_codes(panel)
    names, grids, report = [], [], {"dropped_low_coverage": {}, "missing_indicators": []}
    for name, frame in frames.items():
        v = frame.reindex(index=panel.dates, columns=panel.codes).to_numpy(dtype=float)
        if name in EVENT_NEUTRAL_FIELDS or missing_policy == "semantic_neutral":
            v = np.where(mask & ~np.isfinite(v), 0.0, v)
        cov = float(np.isfinite(v[mask]).mean()) if mask.any() else 0.0
        if cov < min_coverage:
            report["dropped_low_coverage"][name] = round(cov, 3)
            continue
        z = _industry_rank_z(v, mask, ind) if preprocess == "industry_rank_then_zscore" else _rank_z(v, mask)
        missing = mask & ~np.isfinite(z)
        if missing_policy == "median_plus_indicator" and missing[mask].mean() > 0.01:
            names.append(f"missing_{name}")
            grids.append(missing.astype(float))
            report["missing_indicators"].append(name)
        names.append(name)
        grids.append(np.where(mask, np.nan_to_num(z, nan=0.0), np.nan))
    return names, grids, report


def build_cross_sectional(panel: Panel, frames: dict[str, pd.DataFrame], task, beta: np.ndarray | None,
                          stride: int = 1, train_before_window: bool = False) -> SampleTable:
    """Sample table for daily cross-sectional tasks (A, D, B): rows = predict pool U train pool.

    ``train_before_window`` lets a holdout read train on the history that precedes its scoring window.
    """
    train_mask = universe_mask(panel, task.universe_train, within_window=not train_before_window)
    predict_mask = universe_mask(panel, task.universe_predict)
    feature_mask = train_mask | predict_mask
    lab = task.label
    ys, raws = labels(panel, lab.horizons, train_mask, lab.kind, lab.neutralize, lab.winsorize, lab.standardize,
                      beta, task.sample.min_names_per_date)
    names, grids, report = feature_grids(panel, frames, feature_mask, task.features.preprocess,
                                         task.features.missing_policy, task.features.min_feature_coverage)
    if not names:
        raise validation_error("no feature passed the coverage filter", dropped=report["dropped_low_coverage"])
    keep_dates = np.zeros(len(panel.dates), dtype=bool)
    keep_dates[::stride] = True
    train_rows = train_mask & keep_dates[:, None]
    rows = train_rows | predict_mask
    t_idx, c_idx = np.nonzero(rows)
    X = np.column_stack([g[t_idx, c_idx] for g in grids]).astype(np.float32)
    table = SampleTable(date_pos=t_idx, code_pos=c_idx, X=X, feature_names=names)
    for h in lab.horizons:
        y = ys[h][t_idx, c_idx]
        table.Y[h] = np.where(train_rows[t_idx, c_idx], y, np.nan)
        table.R[h] = raws[h][t_idx, c_idx]
        table.label_end[h] = t_idx + h + 1
    table.predict = predict_mask[t_idx, c_idx]
    table.extra["feature_report"] = report
    return table
