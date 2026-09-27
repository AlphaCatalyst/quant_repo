"""Operator implementations on wide frames (index: dates, columns: codes).

Every operator only looks backwards in time: time-series operators use trailing
windows and positive lags, cross-sectional operators use the same date only.
"""

import numpy as np
import pandas as pd

Frame = pd.DataFrame


def _clean(x):
    if isinstance(x, pd.DataFrame):
        return x.replace([np.inf, -np.inf], np.nan)
    return x


def ts_mean(x: Frame, w: int) -> Frame:
    return x.rolling(w, min_periods=w).mean()


def ts_std(x: Frame, w: int) -> Frame:
    return x.rolling(w, min_periods=w).std()


def ts_sum(x: Frame, w: int) -> Frame:
    return x.rolling(w, min_periods=w).sum()


def ts_max(x: Frame, w: int) -> Frame:
    return x.rolling(w, min_periods=w).max()


def ts_min(x: Frame, w: int) -> Frame:
    return x.rolling(w, min_periods=w).min()


def ts_rank(x: Frame, w: int) -> Frame:
    return x.rolling(w, min_periods=w).rank(pct=True)


def ts_delta(x: Frame, w: int) -> Frame:
    return x - x.shift(w)


def ts_delay(x: Frame, w: int) -> Frame:
    return x.shift(w)


def ts_zscore(x: Frame, w: int) -> Frame:
    roll = x.rolling(w, min_periods=w)
    return _clean((x - roll.mean()) / roll.std())


def ts_decay_linear(x: Frame, w: int) -> Frame:
    total = None
    for k in range(w):
        term = x.shift(k) * (w - k)
        total = term if total is None else total + term
    return total / (w * (w + 1) / 2)


def ts_corr(x: Frame, y: Frame, w: int) -> Frame:
    return _clean(x.rolling(w, min_periods=w).corr(y))


def ts_cov(x: Frame, y: Frame, w: int) -> Frame:
    return x.rolling(w, min_periods=w).cov(y)


def cs_rank(x: Frame) -> Frame:
    from alphasieve.evaluation.fastops import row_rank

    return pd.DataFrame(row_rank(x.to_numpy(dtype=float), pct=True), index=x.index, columns=x.columns)


def cs_zscore(x: Frame) -> Frame:
    return _clean(x.sub(x.mean(axis=1), axis=0).div(x.std(axis=1), axis=0))


def cs_demean(x: Frame) -> Frame:
    return x.sub(x.mean(axis=1), axis=0)


def cs_winsorize(x: Frame) -> Frame:
    lower = x.quantile(0.01, axis=1)
    upper = x.quantile(0.99, axis=1)
    return x.clip(lower=lower, upper=upper, axis=0)


def group_rank(x: Frame, groups: pd.Series) -> Frame:
    return x.T.groupby(groups.reindex(x.columns).to_numpy()).rank(pct=True).T


def group_demean(x: Frame, groups: pd.Series) -> Frame:
    means = x.T.groupby(groups.reindex(x.columns).to_numpy()).transform("mean").T
    return x - means


def cs_neutralize(x: Frame, groups: pd.Series, size: Frame) -> Frame:
    codes = x.columns
    dummies = pd.get_dummies(groups.reindex(codes).fillna("unknown")).to_numpy(dtype=float)
    xv = x.to_numpy(dtype=float)
    sv = size.reindex(index=x.index, columns=codes).to_numpy(dtype=float)
    out = np.full_like(xv, np.nan)
    for t in range(xv.shape[0]):
        valid = np.isfinite(xv[t]) & np.isfinite(sv[t])
        if valid.sum() < dummies.shape[1] + 5:
            continue
        design = np.column_stack([dummies[valid], sv[t, valid]])
        beta, *_ = np.linalg.lstsq(design, xv[t, valid], rcond=None)
        out[t, valid] = xv[t, valid] - design @ beta
    return pd.DataFrame(out, index=x.index, columns=codes)


def abs_(x):
    return np.abs(x)


def log(x):
    if isinstance(x, pd.DataFrame):
        return np.log(x.where(x > 0))
    return np.log(x) if x > 0 else np.nan


def sqrt(x):
    if isinstance(x, pd.DataFrame):
        return np.sqrt(x.where(x >= 0))
    return np.sqrt(x) if x >= 0 else np.nan


def sign(x):
    return np.sign(x)


def signed_power(x, p: float):
    return np.sign(x) * np.abs(x) ** p


def add(x, y):
    return x + y


def sub(x, y):
    return x - y


def mul(x, y):
    return x * y


def div(x, y):
    if isinstance(y, pd.DataFrame):
        y = y.where(y != 0)
    elif y == 0:
        return np.nan * x
    return _clean(x / y)


def neg(x):
    return -x


def _broadcast(a, like: Frame):
    if isinstance(a, pd.DataFrame):
        return a
    return pd.DataFrame(a, index=like.index, columns=like.columns)


def max2(x, y):
    like = x if isinstance(x, pd.DataFrame) else y
    return pd.DataFrame(np.maximum(_broadcast(x, like).to_numpy(), _broadcast(y, like).to_numpy()),
                        index=like.index, columns=like.columns)


def min2(x, y):
    like = x if isinstance(x, pd.DataFrame) else y
    return pd.DataFrame(np.minimum(_broadcast(x, like).to_numpy(), _broadcast(y, like).to_numpy()),
                        index=like.index, columns=like.columns)


def _compare(fn, x, y):
    like = x if isinstance(x, pd.DataFrame) else y
    xa, ya = _broadcast(x, like).to_numpy(dtype=float), _broadcast(y, like).to_numpy(dtype=float)
    out = np.where(np.isnan(xa) | np.isnan(ya), np.nan, fn(xa, ya).astype(float))
    return pd.DataFrame(out, index=like.index, columns=like.columns)


def gt(x, y):
    return _compare(np.greater, x, y)


def lt(x, y):
    return _compare(np.less, x, y)


def ge(x, y):
    return _compare(np.greater_equal, x, y)


def le(x, y):
    return _compare(np.less_equal, x, y)


def where(cond, a, b):
    like = next(v for v in (cond, a, b) if isinstance(v, pd.DataFrame))
    c = _broadcast(cond, like).to_numpy(dtype=float)
    av, bv = _broadcast(a, like).to_numpy(dtype=float), _broadcast(b, like).to_numpy(dtype=float)
    out = np.where(np.isnan(c), np.nan, np.where(c > 0, av, bv))
    return pd.DataFrame(out, index=like.index, columns=like.columns)


# argspec letters: x = series, e = series or constant, w = window, c = numeric constant, g = groups, s = size
OPS: dict[str, tuple[object, str, str]] = {
    "ts_mean": (ts_mean, "xw", "ts"),
    "ts_std": (ts_std, "xw", "ts"),
    "ts_sum": (ts_sum, "xw", "ts"),
    "ts_max": (ts_max, "xw", "ts"),
    "ts_min": (ts_min, "xw", "ts"),
    "ts_rank": (ts_rank, "xw", "ts"),
    "ts_delta": (ts_delta, "xw", "ts"),
    "ts_delay": (ts_delay, "xw", "ts"),
    "ts_zscore": (ts_zscore, "xw", "ts"),
    "ts_decay_linear": (ts_decay_linear, "xw", "ts"),
    "ts_corr": (ts_corr, "xxw", "ts"),
    "ts_cov": (ts_cov, "xxw", "ts"),
    "cs_rank": (cs_rank, "x", "cs"),
    "cs_zscore": (cs_zscore, "x", "cs"),
    "cs_demean": (cs_demean, "x", "cs"),
    "cs_winsorize": (cs_winsorize, "x", "cs"),
    "cs_neutralize": (cs_neutralize, "x", "group"),
    "group_rank": (group_rank, "x", "group"),
    "group_demean": (group_demean, "x", "group"),
    "abs": (abs_, "e", "elem"),
    "log": (log, "e", "elem"),
    "sqrt": (sqrt, "e", "elem"),
    "sign": (sign, "e", "elem"),
    "signed_power": (signed_power, "ec", "elem"),
    "add": (add, "ee", "elem"),
    "sub": (sub, "ee", "elem"),
    "mul": (mul, "ee", "elem"),
    "div": (div, "ee", "elem"),
    "neg": (neg, "e", "elem"),
    "max2": (max2, "ee", "elem"),
    "min2": (min2, "ee", "elem"),
    "gt": (gt, "ee", "elem"),
    "lt": (lt, "ee", "elem"),
    "ge": (ge, "ee", "elem"),
    "le": (le, "ee", "elem"),
    "where": (where, "eee", "elem"),
}
COMMUTATIVE = {"add", "mul", "max2", "min2"}
