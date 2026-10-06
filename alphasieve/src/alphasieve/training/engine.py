"""Walk-forward training engine (docs/19 §1.2, §2.4).

At the first retrain point of each year the candidate grid is compared on inner forward-chaining folds of the
training window (each fold purged); the winner per horizon is refit at every retrain point of that year on the
trailing window that ends ``purge_days`` before it, with every declared seed. Horizon predictions are z-scored per
group and combined with the declared weights. Jobs run in a fork-based process pool that shares the sample arrays.
"""

import json
import math
import os
import tempfile
import warnings
from concurrent.futures import ProcessPoolExecutor
from contextvars import ContextVar
from pathlib import Path

import numpy as np
import pandas as pd

from alphasieve.training.samples import SampleTable

MIN_GROUP = 10
RIDGE_DEFAULT_ALPHA = 10.0
LGBM_BASE = {"n_estimators": 300, "learning_rate": 0.05, "num_leaves": 31, "min_child_samples": 200,
             "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.8, "reg_lambda": 1.0, "verbose": -1}
_STATE: dict = {}
_CHECKPOINT: ContextVar[tuple[Path, str] | None] = ContextVar("training_checkpoint", default=None)


def retrain_points(dates: pd.DatetimeIndex, window: np.ndarray, every: str, warmup_years: float) -> list[int]:
    idx = np.flatnonzero(window)
    if len(idx) == 0:
        return []
    first = dates[idx[0]] + pd.DateOffset(months=int(round(warmup_years * 12)))
    keyf = {"monthly": lambda d: (d.year, d.month), "quarterly": lambda d: (d.year, (d.month - 1) // 3),
            "weekly": lambda d: tuple(d.isocalendar())[:2]}[every]
    out, last = [], None
    for i in idx:
        if dates[i] < first:
            continue
        key = keyf(dates[i])
        if key != last:
            out.append(int(i))
            last = key
    return out


def group_ic(pred: np.ndarray, y: np.ndarray, group: np.ndarray) -> np.ndarray:
    """Spearman correlation per group (groups with fewer than MIN_GROUP finite pairs are skipped)."""
    ok = np.isfinite(pred) & np.isfinite(y)
    df = pd.DataFrame({"g": group[ok], "p": pred[ok], "y": y[ok]})
    if df.empty:
        return np.array([])
    df["p"] = df.groupby("g")["p"].rank()
    df["y"] = df.groupby("g")["y"].rank()
    sizes = df.groupby("g").size()
    corr = df.groupby("g")[["p", "y"]].corr().xs("p", level=1)["y"]
    return corr[sizes.reindex(corr.index) >= MIN_GROUP].dropna().to_numpy()


def icir(values: np.ndarray) -> float:
    if len(values) < 3 or values.std() == 0:
        return float("nan")
    return float(values.mean() / values.std())


def group_zscore(values: np.ndarray, group: np.ndarray) -> np.ndarray:
    s = pd.Series(values)
    g = s.groupby(group)
    sd = g.transform("std")
    return ((s - g.transform("mean")) / sd.where(sd > 0)).to_numpy()


def _fit_predict(cand: dict, xtr, ytr, gtr, xte, seed: int, threads: int) -> np.ndarray:
    family, params = cand["family"], dict(cand["params"])
    if family == "ridge":
        alpha = float(params.get("alpha", RIDGE_DEFAULT_ALPHA))
        mx, my = xtr.mean(axis=0), ytr.mean()
        xc = xtr - mx
        w = np.linalg.solve(xc.T @ xc + alpha * len(ytr) / 1000.0 * np.eye(xtr.shape[1]), xc.T @ (ytr - my))
        return xte @ w + (my - mx @ w)
    import lightgbm as lgb

    if family == "lgbm":
        model = lgb.LGBMRegressor(**{**LGBM_BASE, **params}, n_jobs=threads, random_state=seed)
        model.fit(xtr, ytr)
        return model.predict(xte)
    if family == "lambdarank":
        order = np.argsort(gtr, kind="stable")
        xs, ys, gs = xtr[order], ytr[order], gtr[order]
        rel = pd.Series(ys).groupby(gs).transform(
            lambda v: np.floor(v.rank(pct=True) * 4.999)).to_numpy().astype(int)
        _, counts = np.unique(gs, return_counts=True)
        model = lgb.LGBMRanker(**{**LGBM_BASE, "objective": "lambdarank", **params}, n_jobs=threads,
                               random_state=seed)
        model.fit(xs, rel, group=counts)
        return model.predict(xte)
    raise ValueError(f"unknown model family {family}")


def _train_rows(p: int, h: int, lo_date: int) -> np.ndarray:
    st = _STATE
    t = st["table"].date_pos
    purge = st["purge"]
    return np.flatnonzero((t >= lo_date) & (t < p - purge) & np.isfinite(st["table"].Y[h]))


def _lo(p: int) -> int:
    st = _STATE
    if st["window"] == "expanding":
        return st["window_start"]
    return int(st["dates"].searchsorted(st["dates"][p] - pd.DateOffset(months=int(round(st["train_years"] * 12)))))


def _select_job(args):
    s, h, ci, fold = args
    st = _STATE
    table = st["table"]
    rows = _train_rows(s, h, _lo(s))
    if len(rows) == 0:
        return (s, h, ci, fold, float("nan"))
    t = table.date_pos[rows]
    edges = np.linspace(t.min(), t.max() + 1, st["folds"] + 2).astype(int)
    v_lo, v_hi = edges[fold + 1], edges[fold + 2]
    tr = rows[t < v_lo - st["purge"]]
    va = rows[(t >= v_lo) & (t < v_hi)]
    if len(tr) < st["min_rows"] // 2 or len(va) == 0:
        return (s, h, ci, fold, float("nan"))
    pred = _fit_predict(st["candidates"][ci], table.X[tr], table.Y[h][tr], table.group[tr], table.X[va],
                        st["seeds"][0], st["threads"])
    return (s, h, ci, fold, icir(group_ic(pred, table.Y[h][va], table.group[va])))


def _refit_job(args):
    p, stop, h, ci = args
    st = _STATE
    table = st["table"]
    rows = _train_rows(p, h, _lo(p))
    test = np.flatnonzero(table.predict & (table.date_pos >= p) & (table.date_pos < stop))
    if len(rows) < st["min_rows"] or len(test) == 0:
        return (p, h, test, None, len(rows))
    preds = []
    for seed in (st["seeds"] if st["candidates"][ci]["family"] != "ridge" else st["seeds"][:1]):
        pred = _fit_predict(st["candidates"][ci], table.X[rows], table.Y[h][rows], table.group[rows],
                            table.X[test], seed, st["threads"])
        preds.append(group_zscore(pred, table.group[test]))
    return (p, h, test, np.nanmean(np.vstack(preds), axis=0), len(rows))


def _pool(processes: int):
    import multiprocessing as mp

    return ProcessPoolExecutor(max_workers=processes, mp_context=mp.get_context("fork"))


def _read_unit(path: Path, bundle_hash: str, point: int, stop: int, horizons: list[int], nrows: int):
    if not path.exists():
        return None
    try:
        unit = json.loads(path.read_text(encoding="utf-8"))
        if (unit["bundle_sha256"] != bundle_hash or unit["point"] != point or unit["stop"] != stop
                or set(unit["horizons"]) != {str(h) for h in horizons}):
            return None
        results = []
        for h in horizons:
            item = unit["horizons"][str(h)]
            test = np.asarray(item["test"], dtype=np.int64)
            pred = None if item["pred"] is None else np.asarray(item["pred"], dtype=float)
            if ((test < 0).any() or (test >= nrows).any() or
                    (pred is not None and len(pred) != len(test))):
                return None
            results.append((point, h, test, pred, int(item["train_rows"])))
        return results
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _write_unit(path: Path, bundle_hash: str, point: int, stop: int, results: list[tuple]) -> None:
    unit = {"bundle_sha256": bundle_hash, "point": point, "stop": stop,
            "horizons": {str(h): {"test": test.tolist(), "pred": None if pred is None else pred.tolist(),
                                   "train_rows": n_rows}
                         for _, h, test, pred, n_rows in results}}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as f:
            tmp = Path(f.name)
            json.dump(unit, f, ensure_ascii=False, allow_nan=True, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def walk_forward(table: SampleTable, dates: pd.DatetimeIndex, window: np.ndarray, task, processes: int | None = None,
                 threads: int | None = None, progress=None, *, units_dir: Path | None = None,
                 bundle_hash: str | None = None) -> dict:
    if units_dir is None and bundle_hash is None and (checkpoint := _CHECKPOINT.get()) is not None:
        units_dir, bundle_hash = checkpoint
    if (units_dir is None) != (bundle_hash is None):
        raise ValueError("units_dir and bundle_hash must be supplied together")
    split, sample = task.split, task.sample
    horizons = task.label.horizons
    candidates = task.candidates()
    processes = processes or max(1, min(task.platform.processes, (os.cpu_count() or 4) // 4))
    threads = threads or max(1, (os.cpu_count() or 4) // processes)
    points = retrain_points(dates, window, split.retrain, split.warmup_years)
    if not points:
        raise ValueError("no retrain point inside the dev window after warm-up")
    _STATE.clear()
    _STATE.update(table=table, dates=dates, purge=sample.purge_days, window=split.window,
                  window_start=int(np.flatnonzero(window)[0]), train_years=split.train_years,
                  folds=split.inner_folds, candidates=candidates, seeds=list(task.search.seeds),
                  threads=threads, min_rows=sample.min_train_rows)
    years = sorted({dates[p].year for p in points})
    sel_points = {y: next(p for p in points if dates[p].year == y) for y in years}
    chosen: dict[tuple[int, int], int] = {}
    selection: dict = {}
    with _pool(processes) as pool:
        if split.select_every == "yearly" and len(candidates) > 1:
            jobs = [(s, h, ci, f) for s in sel_points.values() for h in horizons
                    for ci in range(len(candidates)) for f in range(split.inner_folds)]
            scores: dict = {}
            for i, (s, h, ci, _f, v) in enumerate(pool.map(_select_job, jobs, chunksize=1), start=1):
                scores.setdefault((s, h, ci), []).append(v)
                if progress and i % 20 == 0:
                    progress("select", i, len(jobs))
            for y, s in sel_points.items():
                for h in horizons:
                    means = [np.nanmean(scores[(s, h, ci)]) if np.isfinite(scores[(s, h, ci)]).any() else -np.inf
                             for ci in range(len(candidates))]
                    best = int(np.argmax(means))
                    chosen[(y, h)] = best
                    selection[f"{y}/h{h}"] = {"chosen": candidates[best],
                                              "inner_icir": [None if not math.isfinite(m) else round(float(m), 4)
                                                             for m in means]}
        else:
            chosen = {(y, h): 0 for y in years for h in horizons}
        bounds = points[1:] + [int(np.flatnonzero(window)[-1]) + 1]
        jobs = [(p, stop, h, chosen[(dates[p].year, h)]) for p, stop in zip(points, bounds, strict=True)
                for h in horizons]
        per_h = {h: np.full(len(table.date_pos), np.nan) for h in horizons}
        fits = skipped = 0
        train_rows = []
        if units_dir is None:
            result_iter = pool.map(_refit_job, jobs, chunksize=1)
        else:
            def checkpointed_results():
                for point_index, (p, stop) in enumerate(zip(points, bounds, strict=True), start=1):
                    path = Path(units_dir) / f"{dates[p].date().isoformat()}.json"
                    results = _read_unit(path, bundle_hash, p, stop, horizons, len(table.date_pos))
                    if results is None:
                        point_jobs = jobs[(point_index - 1) * len(horizons):point_index * len(horizons)]
                        results = list(pool.map(_refit_job, point_jobs, chunksize=1))
                        _write_unit(path, bundle_hash, p, stop, results)
                    yield from results
            result_iter = checkpointed_results()
        for i, (_p, h, test, pred, n_rows) in enumerate(result_iter, start=1):
                if pred is None:
                    skipped += 1
                else:
                    per_h[h][test] = pred
                    fits += 1
                    train_rows.append(n_rows)
                if progress and i % 20 == 0:
                    progress("refit", i, len(jobs))
    weights = task.ensemble.horizon_weights or {h: 1.0 / len(horizons) for h in horizons}
    combo = np.zeros(len(table.date_pos))
    have = np.zeros(len(table.date_pos), dtype=bool)
    for h in horizons:
        z = group_zscore(per_h[h], table.group)
        combo += weights[h] * np.nan_to_num(z)
        have |= np.isfinite(z)
    combo = np.where(have, combo, np.nan)
    return {"score": combo, "per_horizon": per_h, "retrain_points": len(points), "fits": fits,
            "skipped_retrains": skipped, "selection": selection, "processes": processes, "threads": threads,
            "train_rows_median": int(np.median(train_rows)) if train_rows else 0}


def to_grid(values: np.ndarray, table: SampleTable, shape: tuple[int, int]) -> np.ndarray:
    grid = np.full(shape, np.nan)
    grid[table.date_pos, table.code_pos] = values
    return grid


def score_diagnostics(score: np.ndarray, raw: dict[int, np.ndarray], mask: np.ndarray, lags: list[int]) -> dict:
    """RankIC / ICIR / decile spread per horizon, IC decay by lag, rank autocorrelation and turnover proxy."""
    from alphasieve.evaluation import fastops

    scored = np.isfinite(score).any(axis=1)
    if scored.any():
        first = int(np.flatnonzero(scored)[0])
        mask = mask.copy()
        mask[:first] = False
    s = np.where(mask, score, np.nan)
    out: dict = {"coverage": float(np.isfinite(s[mask]).mean()) if mask.any() else 0.0,
                 "scored_from_row": int(np.flatnonzero(scored)[0]) if scored.any() else None, "horizons": {}}
    for h, r in raw.items():
        ic = fastops.rank_corr(s, r, mask & np.isfinite(s), MIN_GROUP)
        ic = ic[np.isfinite(ic)]
        spreads = []
        for t in range(s.shape[0]):
            ok = np.isfinite(s[t]) & np.isfinite(r[t]) & mask[t]
            if ok.sum() < 50:
                continue
            q = pd.qcut(pd.Series(s[t, ok]).rank(method="first"), 10, labels=False).to_numpy()
            rr = r[t, ok]
            spreads.append(rr[q == 9].mean() - rr[q == 0].mean())
        decay = {}
        for lag in lags:
            shifted = np.full(s.shape, np.nan)
            shifted[lag:] = s[:-lag]
            v = fastops.rank_corr(shifted, r, mask & np.isfinite(shifted), MIN_GROUP)
            decay[str(lag)] = float(np.nanmean(v)) if np.isfinite(v).any() else None
        out["horizons"][str(h)] = {"rank_ic": float(ic.mean()) if len(ic) else None, "icir": icir(ic),
                                   "ic_positive_share": float((ic > 0).mean()) if len(ic) else None,
                                   "top_minus_bottom_decile": float(np.mean(spreads)) if spreads else None,
                                   "ic_decay_by_lag": decay}
    prev = np.full(s.shape, np.nan)
    prev[1:] = s[:-1]
    auto = fastops.rank_corr(s, prev, mask & np.isfinite(s) & np.isfinite(prev), MIN_GROUP)
    out["rank_autocorr_1d"] = float(np.nanmean(auto)) if np.isfinite(auto).any() else None
    rk = pd.DataFrame(s).rank(axis=1, pct=True).to_numpy()
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        turn = np.nanmean(np.abs(rk[1:] - rk[:-1]), axis=1)
    out["turnover_proxy"] = float(0.5 * np.nanmean(turn)) if np.isfinite(turn).any() else None
    return out
