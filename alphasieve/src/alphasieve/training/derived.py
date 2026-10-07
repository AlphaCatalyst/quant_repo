"""Pre-registered derived factor candidates for the A task (training-round2 §3) and their outcome-free screen.

Every candidate is a function of panel fields at or before day t (statement fields are already shown from the first
trading day after publication). The screen that decides which candidates enter a task looks only at data coverage
and at correlation with the features already in use; forward returns are reported but never used to select, so the
dev window that judges the task is not also the one that picked its inputs.
"""

import numpy as np
import pandas as pd

from alphasieve.data.access import Panel
from alphasieve.errors import validation_error

MIN_COVERAGE = 0.8
MAX_CORR = 0.7
SCREEN_EVERY = 20
PERIODS = (("2012", "2015"), ("2016", "2019"), ("2020", "2022"))


def _w(panel: Panel, field: str) -> pd.DataFrame:
    return panel.wide(field).astype(float)


def _market(panel: Panel) -> pd.Series:
    ret = _w(panel, "ret_1d")
    member = panel.wide("in_csi800").astype(bool) if panel.has("in_csi800") else ret.notna()
    return ret.where(member).mean(axis=1)


def _idio_vol(panel: Panel, n: int = 60) -> pd.DataFrame:
    ret = _w(panel, "ret_1d")
    mkt = _market(panel)
    cov = ret.rolling(n, min_periods=n // 2).cov(mkt)
    beta = cov.div(mkt.rolling(n, min_periods=n // 2).var(), axis=0)
    return ret.sub(beta.mul(mkt, axis=0)).rolling(n, min_periods=n // 2).std()


def _limit_up_count(panel: Panel, n: int = 20) -> pd.DataFrame:
    hit = (_w(panel, "close_raw") >= _w(panel, "limit_up") * 0.9995).astype(float)
    return hit.where(_w(panel, "close_raw").notna()).rolling(n, min_periods=n // 2).sum()


def _rank(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rank(axis=1, pct=True)


CANDIDATES = {
    "mom_60": ("momentum", lambda p: _w(p, "close") / _w(p, "close").shift(60) - 1),
    "mom_250_20": ("momentum", lambda p: _w(p, "close").shift(20) / _w(p, "close").shift(250) - 1),
    "dist_high_250": ("momentum", lambda p: _w(p, "close") / _w(p, "close").rolling(250, min_periods=120).max() - 1),
    "idio_vol_60": ("risk", _idio_vol),
    "ret_skew_60": ("risk", lambda p: _w(p, "ret_1d").rolling(60, min_periods=30).skew()),
    "downside_vol_60": ("risk", lambda p: _w(p, "ret_1d").clip(upper=0).rolling(60, min_periods=30).std()),
    "turnover_cv_20": ("liquidity", lambda p: _w(p, "turnover_rate").rolling(20, min_periods=10).std()
                       / _w(p, "turnover_rate").rolling(20, min_periods=10).mean()),
    "limit_up_20": ("liquidity", _limit_up_count),
    "log_price": ("liquidity", lambda p: np.log(_w(p, "close_raw").clip(lower=0.01))),
    "sp": ("value", lambda p: 1 / _w(p, "ps_ttm").where(_w(p, "ps_ttm") > 0)),
    "cfp": ("value", lambda p: _w(p, "ws_ocf_ttm") / _w(p, "circ_mv").where(_w(p, "circ_mv") > 0)),
    "value_mix": ("value", lambda p: (_rank(1 / _w(p, "pe_ttm").where(_w(p, "pe_ttm") > 0))
                                      + _rank(1 / _w(p, "pb_mrq").where(_w(p, "pb_mrq") > 0))
                                      + _rank(1 / _w(p, "ps_ttm").where(_w(p, "ps_ttm") > 0))) / 3),
    "op_margin": ("quality", lambda p: _w(p, "ws_op_margin_ttm")),
    "cash_quality": ("quality", lambda p: _w(p, "ws_cash_to_assets") - _w(p, "ws_debt_to_assets")),
    "rd_intensity": ("quality", lambda p: _w(p, "ws_rd_to_rev_q")),
    "goodwill_neg": ("quality", lambda p: -_w(p, "ws_goodwill_to_equity")),
    "ibd_neg": ("quality", lambda p: -_w(p, "ws_ibd_to_equity")),
    "ocf_to_np": ("quality", lambda p: (_w(p, "ws_ocf_ttm") / _w(p, "ws_np_ttm").abs().where(
        _w(p, "ws_np_ttm").abs() > 0)).clip(-10, 10)),
    "roe_change_250": ("quality", lambda p: _w(p, "ws_roe_ttm") - _w(p, "ws_roe_ttm").shift(250)),
    "gm_change_250": ("quality", lambda p: _w(p, "ws_gross_margin_ttm") - _w(p, "ws_gross_margin_ttm").shift(250)),
    "yoy_equity": ("growth", lambda p: _w(p, "yoy_equity")),
    "rev_growth_accel_60": ("growth", lambda p: _w(p, "ws_rev_growth_yoy") - _w(p, "ws_rev_growth_yoy").shift(60)),
    "main_flow_5": ("flow", lambda p: _w(p, "mf_main_net_ratio").rolling(5, min_periods=3).mean()),
    "block_flow_20": ("flow", lambda p: _w(p, "mf_block_net_ratio").rolling(20, min_periods=10).mean()),
}


def derived_frames(panel: Panel, names: list[str]) -> dict[str, pd.DataFrame]:
    unknown = [n for n in names if n not in CANDIDATES]
    if unknown:
        raise validation_error("unknown derived factors", unknown=unknown, known=sorted(CANDIDATES))
    return {n: CANDIDATES[n][1](panel).replace([np.inf, -np.inf], np.nan) for n in names}


def _rank_rows(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    v = np.where(mask & np.isfinite(values), values, np.nan)
    return pd.DataFrame(v).rank(axis=1, pct=True).to_numpy()


def _row_corr(a: np.ndarray, b: np.ndarray) -> float:
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 30:
        return np.nan
    x, y = a[ok] - a[ok].mean(), b[ok] - b[ok].mean()
    den = np.sqrt((x * x).sum() * (y * y).sum())
    return float((x * y).sum() / den) if den > 0 else np.nan


def screen(panel: Panel, existing: dict[str, pd.DataFrame], mask: np.ndarray, predict: np.ndarray,
           labels: dict[int, np.ndarray]) -> dict:
    """Coverage and correlation decide; RankIC by period is reported only."""
    window = panel.window_mask().to_numpy()
    rows = np.flatnonzero(window)[::SCREEN_EVERY]
    years = panel.dates.year.to_numpy()
    ranked_existing = {k: _rank_rows(f.reindex(index=panel.dates, columns=panel.codes).to_numpy(float), mask)[rows]
                       for k, f in existing.items()}
    kept: dict[str, np.ndarray] = {}
    report = {}
    for name, frame in derived_frames(panel, list(CANDIDATES)).items():
        v = frame.reindex(index=panel.dates, columns=panel.codes).to_numpy(float)
        m = mask & window[:, None]
        coverage = float(np.isfinite(v[m]).mean())
        by_year = {int(y): round(float(np.isfinite(v[m & (years == y)[:, None]]).mean()), 3)
                   for y in np.unique(years[window])}
        r = _rank_rows(v, mask)[rows]

        def mean_corr(other: np.ndarray, r=r) -> float:
            vals = [_row_corr(r[i], other[i]) for i in range(len(rows))]
            return float(np.nanmean(vals)) if np.isfinite(vals).any() else np.nan

        corr_existing = {k: mean_corr(o) for k, o in ranked_existing.items()}
        corr_kept = {k: mean_corr(o) for k, o in kept.items()}
        worst = max({**corr_existing, **corr_kept}.items(), key=lambda kv: abs(np.nan_to_num(kv[1])),
                    default=(None, 0.0))
        ic = {}
        for h, y in labels.items():
            per = {}
            for lo, hi in PERIODS:
                sel = [i for i, t in enumerate(rows) if lo <= str(years[t]) <= hi]
                vals = [_row_corr(np.where(predict[rows[i]], r[i], np.nan),
                                  _rank_rows(y[rows[i]][None, :], predict[rows[i]][None, :])[0]) for i in sel]
                per[f"{lo}-{hi}"] = round(float(np.nanmean(vals)), 4) if np.isfinite(vals).any() else None
            ic[f"{h}d"] = per
        if coverage < MIN_COVERAGE:
            decision = "drop_coverage"
        elif abs(np.nan_to_num(worst[1])) > MAX_CORR:
            decision = "drop_correlation"
        else:
            decision = "keep"
            kept[name] = r
        report[name] = {"family": CANDIDATES[name][0], "coverage": round(coverage, 3), "coverage_by_year": by_year,
                        "max_abs_corr": [worst[0], round(float(np.nan_to_num(worst[1])), 3)],
                        "rank_ic_report_only": ic, "decision": decision}
    return {"rule": {"min_coverage": MIN_COVERAGE, "max_abs_corr": MAX_CORR, "sample_every": SCREEN_EVERY,
                     "selection_uses_returns": False},
            "kept": list(kept), "candidates": report}
