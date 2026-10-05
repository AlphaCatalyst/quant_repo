"""Observation-only industry sensitivity of saved dev artifacts.

Reads saved target weights and factor caches. Never runs evaluation, training,
portfolio construction, simulation, or any ledger-writing API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from scipy.stats import rankdata

from alphasieve.data.panel import attach_sw_industry

START = pd.Timestamp("2012-01-01")
END = pd.Timestamp("2022-12-31")
REFERENCE = {
    "v4": ("S-1f2df27729ff", "a_csi500_residual_v4/alphasieve-train-S-1f2df27729ff-20260929-134139"),
    "P1": ("S-05cc830d4f41", "a_csi500_portfolio_cost_v1/S-05cc830d4f41"),
    "P2": ("S-d610f6423c1e", "a_csi500_portfolio_ema_v1/S-d610f6423c1e"),
    "P3": ("S-c2c559d39a80", "a_csi500_portfolio_liquidity_v1/S-c2c559d39a80"),
    "P4": ("S-ec1d810e9ae1", "a_csi500_portfolio_size_v1/S-ec1d810e9ae1"),
    "C2": ("S-10570ee00d4b", "/taijifs_zw35/r2/felixjjiang/alphasieve/store/models/"
           "a_csi500_portfolio_cost_capacity_v2/alphasieve-train-S-10570ee00d4b-20261002-203058"),
}
FACTORS = ("F-000091", "F-000479", "F-000481", "F-000082")
FACTOR_CACHE_SIGNATURES = {"F-000091": "2fad39c1d5d2", "F-000479": "7a9b15e97889",
                           "F-000481": "7a9b15e97889", "F-000082": "7a9b15e97889"}


def read_dev(path: Path, columns: list[str] | None = None, date_col: str = "date") -> pd.DataFrame:
    """Enforce the dev cutoff in the Parquet scan, then again in pandas."""
    dtype = pq.read_schema(path).field(date_col).type
    cutoff = END if pa.types.is_timestamp(dtype) else "2022-12-31"
    frame = pd.read_parquet(path, columns=columns, filters=[(date_col, "<=", cutoff)])
    return frame.loc[pd.to_datetime(frame[date_col]) <= END].copy()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wide(frame: pd.DataFrame, name: str, dates: pd.DatetimeIndex, codes: pd.Index) -> pd.DataFrame:
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.pivot(index="date", columns="code", values=name).reindex(index=dates, columns=codes)


def _labels(rows: pd.DataFrame, history: pd.DataFrame, calendar: list[str], conservative: bool,
            dates: pd.DatetimeIndex, codes: pd.Index) -> pd.DataFrame:
    joined = attach_sw_industry(rows[["date", "code"]], history, calendar, conservative=conservative)
    return _wide(joined, "sw1", dates, codes).fillna("unknown")


def _snapshot_map(raw: Path, codes: pd.Index) -> pd.Series:
    frame = pd.read_parquet(raw, columns=["code", "level", "sector_name"])
    frame = frame[frame["level"] == 1].drop_duplicates("code", keep="last")
    return frame.set_index("code")["sector_name"].reindex(codes).fillna("unknown")


def _dev_end_map(history: pd.DataFrame, codes: pd.Index) -> pd.Series:
    """Use only assignments both effective and last updated by dev end."""
    eligible = history[(pd.to_datetime(history["effective_date"]) <= END)
                       & (pd.to_datetime(history["updated_at"]) <= END)]
    latest = eligible.sort_values(["code", "effective_date", "updated_at"]).drop_duplicates(
        "code", keep="last")
    return latest.set_index("code")["l1_name"].reindex(codes).fillna("unknown")


def _official_at_rebalances(raw: Path, panel: pd.DataFrame, dates: pd.DatetimeIndex,
                            codes: pd.Index) -> pd.DataFrame:
    """Month-end snapshots, then adjusted-close drift as in official_weights.daily_weights."""
    snapshots = read_dev(raw, ["trade_date", "stock_code", "weight"], "trade_date")
    snapshots["trade_date"] = pd.to_datetime(snapshots["trade_date"])
    stock = snapshots["stock_code"].str.lower()
    snapshots["code"] = stock.str[-2:] + "." + stock.str[:6]
    daily_dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
    close = _wide(panel[["date", "code", "close"]].copy(), "close", daily_dates, codes)
    snap = snapshots.pivot(index="trade_date", columns="code", values="weight").reindex(columns=codes).fillna(0)
    snap = snap.sort_index() / 100.0
    out = []
    for date in dates:
        prior = snap.index[snap.index <= date]
        if prior.empty:
            out.append(np.full(len(codes), np.nan))
            continue
        when = prior[-1]
        weight = snap.loc[when].to_numpy(dtype=float).copy()
        at_date = close.loc[date].to_numpy(dtype=float)
        if when in close.index:
            at_snapshot = close.loc[when].to_numpy(dtype=float)
            held = weight > 0
            ratio = np.divide(at_date, at_snapshot, out=np.full(len(codes), np.nan),
                              where=held & np.isfinite(at_snapshot) & (at_snapshot > 0))
            weight *= np.where(held, ratio, 1.0)
        if not np.isfinite(weight).all() or weight.sum() <= 0:
            out.append(np.full(len(codes), np.nan))
        else:
            out.append(weight / weight.sum())
    return pd.DataFrame(out, index=dates, columns=codes)


def active_exposures(target: pd.DataFrame, benchmark: pd.DataFrame, labels: pd.DataFrame,
                     trial: str, basis: str, source: str) -> pd.DataFrame:
    rows = []
    for day in target.index:
        w = target.loc[day].to_numpy(dtype=float)
        b = benchmark.loc[day].to_numpy(dtype=float)
        if not np.isfinite(w).all() or not np.isfinite(b).all() or b.sum() <= 0:
            continue
        groups = labels.loc[day].fillna("unknown").to_numpy(dtype=str)
        for group in np.unique(groups):
            selected = groups == group
            rows.append((trial, basis, source, day, group, float(w[selected].sum()),
                         float(b[selected].sum()), float((w[selected] - b[selected]).sum())))
    return pd.DataFrame(rows, columns=["trial", "benchmark", "industry_source", "date", "industry",
                                       "target_weight", "benchmark_weight", "active_weight"])


def exposure_summary(exposures: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if exposures.empty:
        return pd.DataFrame(), pd.DataFrame()
    per_day = exposures.assign(abs_active=lambda x: x.active_weight.abs()).sort_values(
        "abs_active", ascending=False).drop_duplicates(["trial", "benchmark", "industry_source", "date"])
    summary = per_day.groupby(["trial", "benchmark", "industry_source"], sort=False).agg(
        rebalances=("date", "size"), mean_max=("abs_active", "mean"),
        median_max=("abs_active", "median"), p90_max=("abs_active", lambda x: x.quantile(.9)),
        worst_max=("abs_active", "max"), over_2pct=("abs_active", lambda x: (x > .02 + 1e-8).mean()),
    ).reset_index()
    drivers = exposures.assign(abs_active=lambda x: x.active_weight.abs()).groupby(
        ["trial", "benchmark", "industry_source", "industry"], sort=False).agg(
            exceed_days=("abs_active", lambda x: int((x > .02 + 1e-8).sum())),
            max_abs=("abs_active", "max"), mean_abs=("abs_active", "mean"),
        ).reset_index().sort_values(["trial", "benchmark", "industry_source", "exceed_days", "max_abs"],
                                    ascending=[True, True, True, False, False])
    drivers = drivers.groupby(["trial", "benchmark", "industry_source"], sort=False).head(5)
    return summary, drivers


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 20:
        return np.nan
    ra, rb = rankdata(a), rankdata(b)
    return float(np.corrcoef(ra, rb)[0, 1]) if ra.std() > 0 and rb.std() > 0 else np.nan


def neutral_ic_day(factor: np.ndarray, label: np.ndarray, size: np.ndarray,
                   valid: np.ndarray, groups: np.ndarray, min_extra: int = 5) -> float:
    """Same one-hot industry plus log(size) OLS, then Spearman with raw label."""
    names = valid & np.isfinite(factor) & np.isfinite(label) & np.isfinite(size)
    count = int(names.sum())
    if count < 20:
        return np.nan
    all_groups = np.unique(groups)
    if count < len(all_groups) + min_extra:
        return np.nan
    used = groups[names]
    dummy = (used[:, None] == all_groups[None, :]).astype(float)
    design = np.column_stack((dummy, size[names]))
    residual = factor[names] - design @ np.linalg.lstsq(design, factor[names], rcond=None)[0]
    return _spearman(residual, label[names])


def factor_diagnostics(db: Path, cache: Path, panel: pd.DataFrame, csrc: pd.Series,
                       sw: pd.DataFrame, codes: pd.Index,
                       panel_signature: str) -> tuple[pd.DataFrame, list[str], list[Path]]:
    connection = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    try:
        records = connection.execute(
            "SELECT factor_id,name,candidate_hash,spec_json,state FROM factor_specs WHERE factor_id IN (?,?,?,?)",
            FACTORS).fetchall()
    finally:
        connection.close()
    dates = pd.DatetimeIndex(sorted(set(panel["date"]) & set(sw.index)))
    base = panel[panel["date"].isin(dates)].copy()
    size = np.log(_wide(base[["date", "code", "circ_mv"]].copy(), "circ_mv", dates, codes).to_numpy(dtype=float))
    universe = _wide(base[["date", "code", "in_universe"]].copy(), "in_universe", dates, codes).fillna(0).to_numpy(bool)
    csrc_groups = csrc.reindex(codes).fillna("unknown").to_numpy(dtype=str)
    sw_groups = sw.reindex(index=dates, columns=codes).fillna("unknown").to_numpy(dtype=str)
    output, warnings, cache_paths = [], [], []
    labels_by_horizon = {}
    for factor_id, name, candidate_hash, spec_json, state in records:
        spec = json.loads(spec_json)
        horizon = int(spec["horizon"])
        if horizon not in labels_by_horizon:
            labels_by_horizon[horizon] = _wide(base[["date", "code", f"label_{horizon}d"]].copy(),
                                               f"label_{horizon}d", dates, codes).to_numpy(dtype=float)
        label = labels_by_horizon[horizon]
        cache_path = cache / f"{candidate_hash}-{FACTOR_CACHE_SIGNATURES[factor_id]}.parquet"
        if not cache_path.exists():
            warnings.append(f"{factor_id}: no saved factor cache")
            continue
        cache_paths.append(cache_path)
        # Wide cache has a DatetimeIndex; index filtering is applied during read.
        frame = pd.read_parquet(cache_path, filters=[("__index_level_0__", "<=", END)])
        frame.index = pd.to_datetime(frame.index)
        frame = frame.loc[START:END].reindex(index=dates, columns=codes)
        values = frame.to_numpy(dtype=float) * float(spec["direction"])
        valid = universe & np.isfinite(label)
        for source in ("csrc_current", "sw1_as_effective"):
            group_matrix = np.broadcast_to(csrc_groups, values.shape) if source == "csrc_current" else sw_groups
            daily = [neutral_ic_day(values[t], label[t], size[t], valid[t], group_matrix[t])
                     for t in range(len(dates))]
            series = pd.Series(daily, index=dates).dropna()
            raw = pd.Series([_spearman(values[t, valid[t] & np.isfinite(values[t])],
                                      label[t, valid[t] & np.isfinite(values[t])])
                             for t in range(len(dates))]).dropna()
            mean = float(series.mean()) if len(series) else np.nan
            sd = float(series.std(ddof=1)) if len(series) > 1 else np.nan
            raw_mean = float(raw.mean()) if len(raw) else np.nan
            output.append(dict(factor_id=factor_id, name=name, state=state, horizon=horizon,
                               industry_source=source, cache_signature=cache_path.stem.split("-")[-1],
                               panel_signature=panel_signature[:12], days=len(series), ic_mean=mean,
                               icir=mean / sd if sd > 0 else np.nan, raw_ic_mean=raw_mean,
                               neutral_ratio=mean / raw_mean if raw_mean and np.isfinite(raw_mean) else np.nan,
                               l2_ratio_pass=(bool(mean / raw_mean >= .5)
                                              if raw_mean and np.isfinite(raw_mean) else None),
                               embedded_group_rank="group_rank" in spec["expression"]))
    return pd.DataFrame(output), warnings, cache_paths


def run(args: argparse.Namespace) -> dict:
    hot, store, out = args.hot_root, args.store_root, args.output
    panel_path = hot / "data/panel/dev/panel.parquet"
    meta = json.loads((panel_path.parent / "meta.json").read_text())
    if meta.get("tier") != "dev" or meta["window"]["end"] > "2022-12-31":
        raise ValueError("expected a dev-only panel ending no later than 2022-12-31")
    panel = read_dev(panel_path, ["date", "code", "close", "circ_mv", "in_zz500", "in_universe",
                                   "industry", "label_5d", "label_20d"])
    panel["date"] = pd.to_datetime(panel["date"])
    codes = pd.Index(sorted(panel["code"].unique()))
    calendar = sorted(panel["date"].dt.strftime("%Y-%m-%d").unique())
    history_path = args.history_path or hot / "data/raw/swsresearch/sw_industry_hist.parquet"
    history = read_dev(history_path, ["code", "effective_date", "updated_at", "l1_name", "l2_name"],
                       "effective_date")
    csrc = panel.drop_duplicates("code", keep="last").set_index("code")["industry"].reindex(codes).fillna("unknown")
    current_path = hot / "data/raw/westock/sw_industry/2026-10-03.parquet"
    current = _dev_end_map(history, codes) if args.dev_snapshot else _snapshot_map(current_path, codes)
    official_path = args.official_path or hot / "data/raw/dolthub/index_weights/000905.SH.parquet"
    input_paths = (history_path, panel_path, official_path) if args.dev_snapshot else (
        history_path, panel_path, official_path, current_path)
    exposure_rows, input_hashes = [], {str(path): sha256(path) for path in input_paths}
    for key, (_trial, rel) in REFERENCE.items():
        location = (store / "models" / Path(rel).parent.name / Path(rel).name
                    if args.ray_worker and Path(rel).is_absolute()
                    else Path(rel) if Path(rel).is_absolute() else store / "models" / rel)
        path = location / "weights.parquet"
        if not path.exists():
            continue
        target = pd.read_parquet(path, filters=[("__index_level_0__", "<=", END)])
        target.index = pd.to_datetime(target.index)
        target = target.loc[START:END].reindex(columns=codes).fillna(0)
        dates = target.index
        rows = panel[panel["date"].isin(dates)].copy()
        sw_effective = _labels(rows, history, calendar, False, dates, codes)
        sw_conservative = _labels(rows, history, calendar, True, dates, codes)
        industry_maps = {
            "csrc_current": pd.DataFrame(np.broadcast_to(csrc.to_numpy(), target.shape), index=dates, columns=codes),
            ("sw1_dev_end" if args.dev_snapshot else "sw1_current"):
                pd.DataFrame(np.broadcast_to(current.to_numpy(), target.shape), index=dates, columns=codes),
            "sw1_as_effective": sw_effective, "sw1_conservative": sw_conservative,
        }
        member = _wide(rows[["date", "code", "in_zz500"]].copy(), "in_zz500", dates, codes).fillna(0).to_numpy(bool)
        cap = _wide(rows[["date", "code", "circ_mv"]].copy(), "circ_mv", dates, codes).to_numpy(float)
        eligible = member & np.isfinite(cap) & (cap > 0)
        proxy = np.where(eligible, cap, 0.0)
        proxy /= proxy.sum(axis=1, keepdims=True)
        proxy = pd.DataFrame(proxy, index=dates, columns=codes)
        official = _official_at_rebalances(official_path, panel, dates, codes)
        restricted = np.where(eligible, official.to_numpy(float), 0.0)
        restricted /= restricted.sum(axis=1, keepdims=True)
        benchmarks = {"D31_proxy": proxy, "official_member": pd.DataFrame(restricted, index=dates, columns=codes)}
        for basis, benchmark in benchmarks.items():
            for source, labels in industry_maps.items():
                exposure_rows.append(active_exposures(target, benchmark, labels, key, basis, source))
        input_hashes[str(path)] = sha256(path)
    if not exposure_rows:
        raise ValueError("no saved A target-weight artifacts")
    exposures = pd.concat(exposure_rows, ignore_index=True)
    summary, drivers = exposure_summary(exposures)
    out.mkdir(parents=True, exist_ok=True)
    exposures.to_parquet(out / "active_industry.parquet", index=False)
    summary.to_csv(out / "portfolio_summary.csv", index=False)
    drivers.to_csv(out / "industry_drivers.csv", index=False)
    warnings = []
    if not args.portfolio_only:
        factor_rows = panel[panel["date"] >= START][["date", "code"]]
        sw_all = _labels(factor_rows, history, calendar, False,
                         pd.DatetimeIndex(sorted(factor_rows["date"].unique())), codes)
        factors, warnings, cache_paths = factor_diagnostics(hot / "state/alphasieve.db",
                                                            hot / "cache/factors/dev", panel, csrc,
                                                            sw_all, codes, meta["signature"])
        input_hashes.update({str(path): sha256(path) for path in cache_paths})
        factors.to_csv(out / "factor_neutral_ic.csv", index=False)
    result = {"window": [str(START.date()), str(END.date())], "trials": sorted(exposures.trial.unique()),
              "inputs_sha256": input_hashes, "warnings": warnings,
              "outputs": ["active_industry.parquet", "portfolio_summary.csv", "industry_drivers.csv"]
              + ([] if args.portfolio_only else ["factor_neutral_ic.csv"])}
    (out / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def _dev_weights(source: Path, dest: Path) -> None:
    frame = pd.read_parquet(source, filters=[("__index_level_0__", "<=", END)])
    frame.index = pd.to_datetime(frame.index)
    if frame.empty or (frame.index > END).any():
        raise ValueError(f"refusing non-dev weights: {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(dest)


def _remote_root() -> Path:
    return Path(os.environ.get("ALPHASIEVE_REMOTE_ROOT", "/taijifs_zw35/r2/felixjjiang/alphasieve"))


def _collect(job_id: str, output: Path) -> dict:
    if not re.fullmatch(r"alphasieve-sw-sensitivity-[A-Za-z0-9_-]+", job_id):
        raise ValueError("invalid SW sensitivity job ID")
    source = _remote_root() / "runs" / job_id
    result = json.loads((source / "result.json").read_text())
    outputs = result.get("outputs", [])
    output.mkdir(parents=True, exist_ok=True)
    for name in [*outputs, "manifest.json"]:
        if Path(name).name != name:
            raise ValueError("invalid result file name")
        shutil.copy2(source / name, output / name)
    return {"job_id": job_id, "output": str(output), "result": result}


def submit_ray(args: argparse.Namespace) -> dict:
    if args.start < START or args.end > END or args.start > args.end:
        raise ValueError("refusing Ray submission: only 2012-01-01..2022-12-31 dev period is allowed")
    if args.start != START or args.end != END:
        raise ValueError("this saved-artifact report supports only the full 2012-01-01..2022-12-31 dev window")
    args.portfolio_only = True  # Remote factor specs/caches are incomplete.
    remote = _remote_root()
    hot = remote / "hot"
    if (hot / "data/panel/holdout").exists() or (hot / "data/panel/fresh").exists():
        raise ValueError("refusing Ray submission: holdout/fresh panel found remotely")
    meta = json.loads((hot / "data/panel/dev/meta.json").read_text())
    if meta.get("tier") != "dev" or meta["window"]["end"] > str(END.date()):
        raise ValueError("refusing Ray submission: remote panel is not dev-only")
    repo = Path(__file__).resolve().parents[1]
    token = uuid.uuid4().hex
    remote_tar = remote / "batch_inputs" / f"sw-sensitivity-{token}.tar"
    with tempfile.TemporaryDirectory(prefix="alphasieve-sw-ray-") as temp:
        staging = Path(temp)
        history = pd.read_parquet(args.hot_root / "data/raw/swsresearch/sw_industry_hist.parquet")
        history = history[(pd.to_datetime(history["effective_date"]) <= END)
                          & (pd.to_datetime(history["updated_at"]) <= END)].copy()
        if history.empty:
            raise ValueError("no dev-only SW history rows")
        staged_history = staging / "data/raw/swsresearch/sw_industry_hist.parquet"
        staged_history.parent.mkdir(parents=True)
        history.to_parquet(staged_history, index=False)
        official = pd.read_parquet(args.hot_root / "data/raw/dolthub/index_weights/000905.SH.parquet")
        official = official[pd.to_datetime(official["trade_date"]) <= END].copy()
        if official.empty:
            raise ValueError("no dev-only official CSI 500 weights")
        staged_official = staging / "data/raw/dolthub/index_weights/000905.SH.parquet"
        staged_official.parent.mkdir(parents=True)
        official.to_parquet(staged_official, index=False)
        for _key, (_trial, rel) in REFERENCE.items():
            source = ((Path(rel) if Path(rel).is_absolute() else args.store_root / "models" / rel)
                      / "weights.parquet")
            if not source.exists():
                raise ValueError(f"missing saved A weights: {source}")
            target_rel = Path(rel).parent.name / Path(rel).name if Path(rel).is_absolute() else Path(rel)
            _dev_weights(source, staging / "models" / target_rel / "weights.parquet")
        local_tar = staging / "inputs.tar"
        with tarfile.open(local_tar, "w") as archive:
            archive.add(staging / "models", arcname="models")
            archive.add(staging / "data", arcname="data")
        remote_tar.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_tar, remote_tar)
    command = [str(repo / "deploy/ray/submit_batch.sh"), "sw-sensitivity",
               "tools/sw_industry_sensitivity.py", "--ray-worker", "--portfolio-only",
               "--dev-snapshot", "--hot-root", str(hot), "--store-root", str(remote_tar.with_suffix("")),
               "--history-path", str(remote_tar.with_suffix("") / "data/raw/swsresearch/sw_industry_hist.parquet"),
               "--official-path", str(remote_tar.with_suffix("") / "data/raw/dolthub/index_weights/000905.SH.parquet")]
    env = {**os.environ, "ALPHASIEVE_BATCH_INPUT_TAR": str(remote_tar)}
    completed = subprocess.run(command, env=env, text=True, capture_output=True, check=True)
    match = re.search(r"job (alphasieve-sw-sensitivity-[A-Za-z0-9_-]+);", completed.stdout)
    if match is None:
        raise RuntimeError(f"Ray submission did not report a job ID: {completed.stdout[-500:]}")
    job_id = match.group(1)
    if os.environ.get("ALPHASIEVE_NO_WAIT"):
        return {"job_id": job_id, "collect": f"python tools/sw_industry_sensitivity.py --collect {job_id}"}
    return _collect(job_id, args.output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hot-root", type=Path, default=Path("/data/alphasieve"))
    parser.add_argument("--store-root", type=Path, default=Path("/mnt/private_felixjjiang/alphasieve"))
    parser.add_argument("--history-path", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--official-path", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, default=Path("/data/alphasieve/reports/sw_sensitivity"))
    parser.add_argument("--portfolio-only", action="store_true", help="Refresh portfolio output; keep prior factor CSV")
    parser.add_argument("--ray", action="store_true", help="submit dev portfolio analysis to Ray")
    parser.add_argument("--ray-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dev-snapshot", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--collect", metavar="JOB_ID", help="collect a completed Ray job")
    parser.add_argument("--start", type=pd.Timestamp, default=START)
    parser.add_argument("--end", type=pd.Timestamp, default=END)
    args = parser.parse_args()
    if args.collect:
        result = _collect(args.collect, args.output)
    elif args.ray:
        result = submit_ray(args)
    else:
        if args.ray_worker:
            job_id = os.environ["ALPHASIEVE_JOB_ID"]
            args.output = _remote_root() / "runs" / job_id
        result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
