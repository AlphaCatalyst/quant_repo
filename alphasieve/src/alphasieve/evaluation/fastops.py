"""numba / numpy primitives for cross-sectional evaluation. Semantics match the pandas reference
implementations (average ranks for ties, NaN preserved); tests/test_fastops.py pins that equivalence."""

import numba as nb
import numpy as np


@nb.njit(cache=True, inline="always")
def _rank_subset(vals: np.ndarray, pct: bool) -> np.ndarray:
    m = vals.shape[0]
    ranks = np.empty(m)
    order = np.argsort(vals, kind="mergesort")
    k = 0
    while k < m:
        e = k
        v = vals[order[k]]
        while e + 1 < m and vals[order[e + 1]] == v:
            e += 1
        r = (k + e) / 2.0 + 1.0
        if pct:
            r = r / m
        for q in range(k, e + 1):
            ranks[order[q]] = r
        k = e + 1
    return ranks


@nb.njit(parallel=True, cache=True)
def _row_rank(x: np.ndarray, pct: bool) -> np.ndarray:
    t, n = x.shape
    out = np.full((t, n), np.nan)
    for i in nb.prange(t):
        idx = np.empty(n, np.int64)
        m = 0
        for j in range(n):
            if np.isfinite(x[i, j]):
                idx[m] = j
                m += 1
        if m == 0:
            continue
        vals = np.empty(m)
        for k in range(m):
            vals[k] = x[i, idx[k]]
        ranks = _rank_subset(vals, pct)
        for k in range(m):
            out[i, idx[k]] = ranks[k]
    return out


@nb.njit(parallel=True, cache=True)
def _rank_corr(a: np.ndarray, b: np.ndarray, valid: np.ndarray, min_names: int) -> np.ndarray:
    t, n = a.shape
    out = np.full(t, np.nan)
    for i in nb.prange(t):
        idx = np.empty(n, np.int64)
        m = 0
        for j in range(n):
            if valid[i, j] and np.isfinite(a[i, j]) and np.isfinite(b[i, j]):
                idx[m] = j
                m += 1
        if m < min_names or m == 0:
            continue
        va = np.empty(m)
        vb = np.empty(m)
        for k in range(m):
            va[k] = a[i, idx[k]]
            vb[k] = b[i, idx[k]]
        ra = _rank_subset(va, False)
        rb = _rank_subset(vb, False)
        ma = ra.mean()
        mb = rb.mean()
        num = 0.0
        da = 0.0
        db = 0.0
        for k in range(m):
            xa = ra[k] - ma
            xb = rb[k] - mb
            num += xa * xb
            da += xa * xa
            db += xb * xb
        denom = np.sqrt(da * db)
        if denom > 0:
            out[i] = num / denom
    return out


@nb.njit(parallel=True, cache=True)
def _centered_rank(x: np.ndarray, valid: np.ndarray) -> np.ndarray:
    t, n = x.shape
    out = np.zeros((t, n))
    for i in nb.prange(t):
        idx = np.empty(n, np.int64)
        m = 0
        for j in range(n):
            if valid[i, j] and np.isfinite(x[i, j]):
                idx[m] = j
                m += 1
        if m == 0:
            continue
        vals = np.empty(m)
        for k in range(m):
            vals[k] = x[i, idx[k]]
        ranks = _rank_subset(vals, True)
        for k in range(m):
            out[i, idx[k]] = ranks[k] - 0.5
    return out


def centered_rank(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Percentile rank minus 0.5 over valid names, 0 elsewhere (the ridge feature transform)."""
    return _centered_rank(np.ascontiguousarray(values, dtype=np.float64), np.ascontiguousarray(valid, dtype=np.bool_))


def row_rank(values: np.ndarray, pct: bool = False) -> np.ndarray:
    """Average-tie ranks along axis 1, starting at 1; NaN stays NaN (pandas ``rank(axis=1)``)."""
    x = np.ascontiguousarray(values, dtype=np.float64)
    if x.shape[1] == 0:
        return x.copy()
    return _row_rank(x, pct)


def masked(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    return np.where(mask, values, np.nan)


def rank_corr(a: np.ndarray, b: np.ndarray, valid: np.ndarray, min_names: int) -> np.ndarray:
    """Daily cross-sectional Spearman correlation of a and b over valid names."""
    return _rank_corr(np.ascontiguousarray(a, dtype=np.float64), np.ascontiguousarray(b, dtype=np.float64),
                      np.ascontiguousarray(valid, dtype=np.bool_), int(min_names))


def neutralize(x: np.ndarray, dummies: np.ndarray, size: np.ndarray, min_extra: int = 5) -> np.ndarray:
    """Residual of x on industry dummies and size, per date (equal to per-date lstsq residuals).

    With one-hot dummies the normal equations reduce to per-industry counts and sums, built with matrix
    products. Industries absent on a date have all-zero columns; pinning their diagonal to 1 keeps the
    system full rank without changing the projection.
    """
    t, n = x.shape
    g = dummies.shape[1]
    valid = np.isfinite(x) & np.isfinite(size)
    vf = valid.astype(float)
    sv = np.where(valid, size, 0.0)
    xv = np.where(valid, x, 0.0)
    gram = np.zeros((t, g + 1, g + 1))
    idx = np.arange(g)
    counts = vf @ dummies
    gram[:, idx, idx] = counts
    size_by_group = sv @ dummies
    gram[:, idx, g] = size_by_group
    gram[:, g, idx] = size_by_group
    gram[:, g, g] = (sv * sv).sum(axis=1)
    rhs = np.concatenate([xv @ dummies, (xv * sv).sum(axis=1, keepdims=True)], axis=1)
    absent = counts == 0
    gram[:, idx, idx] += absent
    enough = valid.sum(axis=1) >= g + min_extra
    gram[~enough] = np.eye(g + 1)
    try:
        beta = np.linalg.solve(gram, rhs[..., None])[..., 0]
    except np.linalg.LinAlgError:
        beta = np.stack([np.linalg.lstsq(gram[i], rhs[i], rcond=None)[0] for i in range(t)])
    fitted = beta[:, :g] @ dummies.T + beta[:, g:] * sv
    out = np.where(valid, xv - fitted, np.nan)
    out[~enough] = np.nan
    return out
