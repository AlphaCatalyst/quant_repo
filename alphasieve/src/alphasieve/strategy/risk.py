"""Frozen rm1 report-only factor risk model (docs/25). All returns and variances are daily."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

import numpy as np
import pandas as pd

MODEL = "rm1"
STYLE = ("size_z", "beta_z", "momentum_z", "volatility_z", "liquidity_z", "value_z")
PARAMETERS = {
    "regression_ridge": 1e-6,
    "factor_window": 504,
    "factor_min_days": 252,
    "factor_half_life": 60,
    "specific_half_life": 90,
    "specific_min_days": 126,
    "specific_shrink": 0.5,
    "factor_diag_shrink": 0.1,
    "factor_floor": 1e-12,
    "specific_floor": 1e-8,
    "annual_days": 252,
    "style_min_names": 100,
    "style_min_coverage": 0.8,
    "provisional_weight": 0.05,
    "industry_min_days": 126,
    "bias_min_pairs": 252,
    "bias_min_coverage": 0.8,
    "bias_bounds": (0.8, 1.2),
    "subperiod_min_pairs": 126,
    "subperiod_bias_bounds": (0.7, 1.3),
}
SPEC_HASH = sha256(
    json.dumps(
        {"model": MODEL, "style": STYLE, "parameters": PARAMETERS}, sort_keys=True, separators=(",", ":")
    ).encode()
).hexdigest()
SW_WARNING = (
    "SW history may be restated after the fact: 更新日期 can be later than 计入日期; "
    "first publication cannot be proven. This report is provisional, never strict PIT."
)


@dataclass
class RiskSnapshot:
    date: pd.Timestamp
    codes: tuple[str, ...]
    factors: tuple[str, ...]
    X: np.ndarray
    benchmark: np.ndarray
    universe: np.ndarray
    cap: np.ndarray
    industry: np.ndarray
    stats: dict
    coverage: dict
    missing: dict
    quality: str
    warnings: tuple[str, ...]
    raw_beta: np.ndarray
    legacy_size_z: np.ndarray
    source_version: str


@dataclass
class FactorReturn:
    date: pd.Timestamp
    factors: tuple[str, ...]
    values: np.ndarray
    residuals: np.ndarray
    valid: bool
    industries_observed: tuple[str, ...]


@dataclass
class RiskCovariance:
    factors: tuple[str, ...]
    F: np.ndarray
    D: np.ndarray
    prior: np.ndarray
    valid: bool
    quality: str
    days: int
    industry_days: dict
    warnings: tuple[str, ...]


def industry_asof(history: pd.DataFrame, day, codes: list[str] | tuple[str, ...], calendar) -> pd.Series:
    """The effective and known dates both gate a label; the next trading day is first usable."""
    required = {"code", "industry", "effective_date", "known_at", "source_version"}
    if not required.issubset(history):
        raise ValueError(f"industry history needs {sorted(required)}")
    day = pd.Timestamp(day)
    cal = pd.DatetimeIndex(calendar)
    rows = history.copy()
    rows["effective_date"] = pd.to_datetime(rows["effective_date"])
    rows["known_at"] = pd.to_datetime(rows["known_at"])
    gate = rows[["effective_date", "known_at"]].max(axis=1)
    pos = cal.searchsorted(gate.to_numpy(dtype="datetime64[ns]"), side="right")
    rows = rows.loc[pos < len(cal)].copy()
    rows["available"] = cal[pos[pos < len(cal)]].to_numpy()
    rows = rows[(rows["available"] <= day) & (rows["effective_date"] <= day) & (rows["known_at"] <= day)]
    rows = rows.sort_values(["code", "available", "effective_date", "known_at"])
    rows = rows.drop_duplicates("code", keep="last")
    return rows.set_index("code")["industry"].reindex(codes).fillna("unknown").replace("", "unknown")


def standardize(raw: np.ndarray, universe: np.ndarray, *, size: bool = False) -> tuple[np.ndarray, dict, np.ndarray]:
    """Fit only the current universe, then transform every held name with the same statistics."""
    raw = np.asarray(raw, dtype=np.float64)
    universe = np.asarray(universe, dtype=bool)
    good = universe & np.isfinite(raw)
    n = int(universe.sum())
    coverage = float(good.sum() / n) if n else 0.0
    info = {
        "count": int(good.sum()),
        "coverage": coverage,
        "available": False,
        "median": None,
        "mean": None,
        "std": None,
        "lower": None,
        "upper": None,
    }
    missing = ~np.isfinite(raw)
    out = np.zeros(len(raw), dtype=np.float64)
    if not good.any():
        return out, info, missing
    lo, hi = (-np.inf, np.inf) if size else tuple(np.quantile(raw[good], [0.01, 0.99]))
    clipped = np.clip(raw, lo, hi)
    median = float(np.median(clipped[good]))
    filled = np.where(np.isfinite(clipped), clipped, median)
    # Size uses only positive-cap observations, exactly as legacy _size_z's fit.
    fit = clipped[good] if size else filled[universe]
    mean, std = float(np.mean(fit)), float(np.std(fit, ddof=0))
    info.update(
        median=median,
        mean=mean,
        std=std,
        lower=None if size else float(lo),
        upper=None if size else float(hi),
        available=good.sum() >= PARAMETERS["style_min_names"]
        and coverage >= PARAMETERS["style_min_coverage"]
        and std > 0,
    )
    if std > 0:
        out = (filled - mean) / std
        if size:
            out[missing] = 0.0
    return out, info, missing


class ExposureEngine:
    """Precompute causal raw styles once; daily statistics are fitted at each close."""

    def __init__(
        self,
        panel,
        universe: np.ndarray,
        market_returns: np.ndarray,
        *,
        industry_source: str = "sw1",
        industry_history: pd.DataFrame | None = None,
    ):
        self.panel = panel
        self.universe = np.asarray(universe, dtype=bool)
        self.market = np.asarray(market_returns, dtype=float)
        self.dates = panel.dates
        self.codes = tuple(panel.codes)
        self.source = industry_source
        self.history = industry_history
        self.industry_daily = None
        if industry_history is not None:
            rows = industry_history.copy()
            rows["effective_date"] = pd.to_datetime(rows["effective_date"])
            rows["known_at"] = pd.to_datetime(rows["known_at"])
            gate = rows[["effective_date", "known_at"]].max(axis=1)
            available = self.dates.searchsorted(gate.to_numpy(dtype="datetime64[ns]"), side="right")
            rows = rows.loc[available < len(self.dates)].copy()
            rows["position"] = available[available < len(self.dates)]
            rows = rows.sort_values(["position", "effective_date", "known_at"])
            events: dict[int, list] = {}
            for row in rows.itertuples(index=False):
                events.setdefault(int(row.position), []).append(row)
            code_pos = {code: i for i, code in enumerate(self.codes)}
            current = np.full(len(self.codes), "unknown", dtype=object)
            effective = np.full(len(self.codes), pd.Timestamp.min, dtype=object)
            daily = np.empty((len(self.dates), len(self.codes)), dtype=object)
            for t in range(len(self.dates)):
                for row in events.get(t, ()):
                    i = code_pos.get(row.code)
                    if i is not None and row.effective_date >= effective[i]:
                        effective[i] = row.effective_date
                        current[i] = row.industry if isinstance(row.industry, str) and row.industry else "unknown"
                daily[t] = current
            self.industry_daily = daily
        self.cap = panel.wide("circ_mv").to_numpy(dtype=float)
        ret = panel.wide("ret_1d").to_numpy(dtype=float)
        px = panel.wide("close").to_numpy(dtype=float)
        amt = panel.wide("amount").to_numpy(dtype=float)
        pb = panel.wide("pb_mrq").to_numpy(dtype=float)
        rr = pd.DataFrame(ret)
        aa = pd.DataFrame(np.where(np.isfinite(amt) & (amt >= 0), amt, np.nan))
        beta = np.full(ret.shape, np.nan)
        for t in range(len(self.dates)):
            lo = max(0, t - 59)
            m = self.market[lo : t + 1]
            v = ret[lo : t + 1]
            ok = np.isfinite(v) & np.isfinite(m[:, None])
            n = ok.sum(axis=0)
            if n.max() < 30:
                continue
            my = np.divide(np.where(ok, v, 0).sum(axis=0), n, out=np.zeros(len(self.codes)), where=n > 0)
            mx = np.divide(
                (ok * np.where(np.isfinite(m), m, 0)[:, None]).sum(axis=0),
                n,
                out=np.zeros(len(self.codes)),
                where=n > 0,
            )
            dm = np.where(ok, m[:, None] - mx, 0)
            var = np.sum(dm * dm, axis=0)
            cov = np.sum(dm * np.where(ok, v - my, 0), axis=0)
            beta[t] = np.divide(cov, var, out=np.full(len(self.codes), np.nan), where=(n >= 30) & (var > 0))
        mom = np.full(ret.shape, np.nan)
        if len(px) > 252:
            with np.errstate(divide="ignore", invalid="ignore"):
                mom[252:] = np.log(px[232:-20] / px[:-252])
        vol = rr.rolling(60, min_periods=40).std(ddof=0).to_numpy()
        liq = aa.rolling(20, min_periods=15).mean().to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            self.raw = {
                "size_z": np.log(np.where(self.cap > 0, self.cap, np.nan)),
                "beta_z": beta,
                "momentum_z": mom,
                "volatility_z": np.log(np.where(vol > 0, vol * np.sqrt(252), np.nan)),
                "liquidity_z": np.log(np.where(liq > 0, liq, np.nan)),
                "value_z": np.log(np.where(pb > 0, 1 / pb, np.nan)),
            }
        self.ret = ret

    def snapshot(self, pos: int) -> RiskSnapshot:
        from alphasieve.strategy.portfolio import _size_z

        d = self.dates[pos]
        universe = self.universe[pos]
        cap = self.cap[pos]
        valid_cap = universe & np.isfinite(cap) & (cap > 0)
        b = np.where(valid_cap, cap, 0.0)
        b = b / b.sum() if b.sum() > 0 else b
        if self.industry_daily is not None:
            ind = self.industry_daily[pos].astype(str)
        else:
            ind = self.panel.industry_asof(d, self.source).reindex(self.codes).to_numpy(dtype=str)
        ind = np.where((ind == "") | (ind == "nan") | (ind == "None"), "unknown", ind)
        labels = sorted(set(ind[universe]) | {"unknown"})
        stats, coverage, missing, z = {}, {}, {}, []
        for name in STYLE:
            zz, st, miss = standardize(self.raw[name][pos], universe, size=name == "size_z")
            stats[name], coverage[name], missing[name] = st, st["coverage"], miss
            z.append(zz)
        X = np.column_stack([np.ones(len(self.codes)), *(ind == label for label in labels), *z]).astype(float)
        unknown = float(b[ind == "unknown"].sum())
        warnings = [SW_WARNING] if self.source == "sw1" else ["Industry is a current snapshot, not PIT"]
        if unknown > 0.05:
            warnings.append("unknown benchmark industry exceeds 5%")
        quality = "provisional" if all(stats[x]["available"] for x in STYLE) and b.sum() > 0 else "unavailable"
        return RiskSnapshot(
            d,
            self.codes,
            tuple(["market", *("industry:" + x for x in labels), *STYLE]),
            X,
            b,
            universe,
            cap,
            ind,
            stats,
            coverage,
            missing,
            quality,
            tuple(warnings),
            self.raw["beta_z"][pos],
            _size_z(cap, universe) if valid_cap.any() else np.zeros(len(cap), dtype=float),
            self.source,
        )


def exposures_at_close(panel, date, universe_mask, market_returns, **kwargs) -> RiskSnapshot:
    engine = ExposureEngine(panel, universe_mask, market_returns, **kwargs)
    return engine.snapshot(int(panel.dates.get_loc(pd.Timestamp(date))))


def pit_quality(snapshot: RiskSnapshot, weights: np.ndarray) -> dict:
    w = np.asarray(weights, dtype=float)
    missing_weight = {
        k: {"portfolio": float(w[v].sum()), "benchmark": float(snapshot.benchmark[v].sum())}
        for k, v in snapshot.missing.items()
    }
    unknown = {
        "portfolio": float(w[snapshot.industry == "unknown"].sum()),
        "benchmark": float(snapshot.benchmark[snapshot.industry == "unknown"].sum()),
    }
    quality = snapshot.quality
    if any(max(x.values()) > 0.05 for x in missing_weight.values()) or max(unknown.values()) > 0.05:
        quality = "provisional" if quality != "unavailable" else quality
    return {
        "quality": quality,
        "pit_complete": False,
        "industry_basis": "sw1_pit_provisional" if snapshot.source_version == "sw1" else "current_snapshot_non_pit",
        "unknown_industry_weight": unknown,
        "missing_weight": missing_weight,
        "warnings": list(snapshot.warnings),
    }


def fit_factor_returns(previous: RiskSnapshot, next_returns: np.ndarray, date=None) -> FactorReturn:
    """Constrained WLS: one industry coefficient is removed by the benchmark-weighted zero-sum rule."""
    y = np.asarray(next_returns, dtype=float)
    factors = previous.factors
    out = np.zeros(len(factors))
    residual = np.full(len(y), np.nan)
    industry_idx = [i for i, f in enumerate(factors) if f.startswith("industry:")]
    observed = []
    valid = previous.universe & np.isfinite(y) & np.isfinite(previous.cap) & (previous.cap > 0)
    if previous.quality == "unavailable" or valid.sum() < 100:
        return FactorReturn(pd.Timestamp(date or previous.date), factors, out, residual, False, ())
    bg = np.array([previous.benchmark[previous.X[:, j] > 0].sum() for j in industry_idx])
    if any(bg[k] > 0 and not (valid & (previous.X[:, j] > 0)).any() for k, j in enumerate(industry_idx)):
        return FactorReturn(pd.Timestamp(date or previous.date), factors, out, residual, False, ())
    active = [j for j in industry_idx if (valid & (previous.X[:, j] > 0)).any()]
    observed = [factors[j][9:] for j in active]
    # Weighted contrast removes the industry/market collinearity.
    reference = max(active, key=lambda j: bg[industry_idx.index(j)])
    cols = [0] + [j for j in active if j != reference] + list(range(len(factors) - 6, len(factors)))
    A = previous.X[valid][:, cols].copy()
    ref_b = bg[industry_idx.index(reference)]
    for c, j in enumerate(cols):
        if j in active and j != reference:
            A[:, c] -= previous.X[valid, reference] * bg[industry_idx.index(j)] / ref_b
    omega = np.sqrt(previous.cap[valid])
    omega /= omega.sum()
    lhs = A.T @ (omega[:, None] * A)
    lhs[-6:, -6:] += np.eye(6) * PARAMETERS["regression_ridge"]
    rhs = A.T @ (omega * y[valid])
    try:
        coef = np.linalg.solve(lhs, rhs)
    except np.linalg.LinAlgError:
        return FactorReturn(pd.Timestamp(date or previous.date), factors, out, residual, False, tuple(observed))
    for c, j in enumerate(cols):
        out[j] = coef[c]
    out[reference] = -sum(bg[industry_idx.index(j)] * out[j] for j in active if j != reference) / ref_b
    residual[valid] = y[valid] - previous.X[valid] @ out
    return FactorReturn(pd.Timestamp(date or previous.date), factors, out, residual, True, tuple(observed))


def _ew_variance(values: np.ndarray, positions: np.ndarray, end: int, half_life: int) -> np.ndarray:
    q = 2.0 ** (-(end - positions) / half_life)
    q /= q.sum()
    mean = q @ values
    centered = values - mean
    return (centered * q[:, None]).T @ centered / (1.0 - np.sum(q * q))


def estimate_covariance(
    history: list[FactorReturn],
    current: RiskSnapshot,
    *,
    positions: np.ndarray | None = None,
    current_pos: int | None = None,
) -> RiskCovariance:
    """Use the last 504 calendar trading positions and never count structural industry zeros as observations."""
    n = len(current.codes)
    p = len(current.factors)
    if positions is None:
        positions = np.arange(len(history))
    if current_pos is None:
        current_pos = int(positions[-1]) if len(positions) else 0
    keep = [
        (h, int(t))
        for h, t in zip(history, positions, strict=True)
        if h.valid and current_pos - 503 <= t <= current_pos
    ]
    days = len(keep)
    absent = RiskCovariance(
        current.factors,
        np.zeros((p, p)),
        np.full(n, np.nan),
        np.ones(n, bool),
        False,
        "unavailable",
        days,
        {},
        ("factor warm-up or insufficient input",),
    )
    if days < 252:
        return absent
    factors = current.factors
    vals = np.zeros((days, p))
    ind_days = {f[9:]: 0 for f in factors if f.startswith("industry:")}
    for k, (h, _) in enumerate(keep):
        idx = {f: i for i, f in enumerate(h.factors)}
        vals[k] = [h.values[idx[f]] if f in idx else 0 for f in factors]
        for label in h.industries_observed:
            if label in ind_days:
                ind_days[label] += 1
    pos = np.array([t for _, t in keep])
    F = _ew_variance(vals, pos, current_pos, 60)
    F = 0.9 * F + 0.1 * np.diag(np.diag(F)) + np.eye(p) * 1e-12
    residual = np.stack([h.residuals for h, _ in keep])
    mask = np.isfinite(residual)
    values = np.where(mask, residual, 0.0)
    q = 2.0 ** (-(current_pos - pos) / 90.0)
    weighted = q[:, None] * mask
    total = weighted.sum(axis=0)
    mean = np.divide((weighted * values).sum(axis=0), total, out=np.zeros(n), where=total > 0)
    centered = np.where(mask, residual - mean, 0.0)
    second = np.divide((weighted * centered**2).sum(axis=0), total, out=np.zeros(n), where=total > 0)
    correction = 1.0 - np.divide((weighted**2).sum(axis=0), total**2, out=np.ones(n), where=total > 0)
    raw = np.divide(second, correction, out=np.full(n, np.nan), where=(mask.sum(axis=0) >= 126) & (correction > 0))
    usable = current.universe & np.isfinite(raw)
    if not usable.any():
        return absent
    pool = float(np.median(raw[usable]))
    group = {}
    for label in set(current.industry):
        v = raw[(current.industry == label) & usable]
        group[label] = float(np.median(v)) if len(v) >= 20 else pool
    prior = ~np.isfinite(raw)
    D = np.array(
        [
            max(0.5 * raw[i] + 0.5 * group[g], 1e-8) if not prior[i] else max(group[g], 1e-8)
            for i, g in enumerate(current.industry)
        ]
    )
    involved = set(current.industry[current.benchmark > 0])
    short = [x for x in involved if ind_days.get(x, 0) < 126]
    return RiskCovariance(
        factors,
        F,
        D,
        prior,
        True,
        "provisional" if short else "valid",
        days,
        ind_days,
        tuple(f"industry {x} has fewer than 126 regression days" for x in sorted(short)),
    )


def exante_te(snapshot: RiskSnapshot, covariance: RiskCovariance, weights: np.ndarray) -> dict:
    if not covariance.valid:
        return {"quality": "unavailable", "te": None, "factor_variance": None, "specific_variance": None}
    w = np.asarray(weights, dtype=float)
    if w.shape != snapshot.benchmark.shape or not np.isfinite(w).all():
        raise ValueError("weights must be finite and align with snapshot codes")
    a = w - snapshot.benchmark
    xf = snapshot.X.T @ a
    fv = float(xf @ covariance.F @ xf)
    sv = float(np.sum(covariance.D * a * a))
    variance = max(fv + sv, 0.0)
    q = pit_quality(snapshot, w)
    quality = "valid" if q["quality"] == "valid" and covariance.quality == "valid" else "provisional"
    if q["quality"] == "unavailable":
        quality = "unavailable"
    if max(float(w[covariance.prior].sum()), float(snapshot.benchmark[covariance.prior].sum())) > 0.05:
        quality = "provisional" if quality != "unavailable" else quality
    # SW provenance is always provisional even when the statistical inputs pass.
    if snapshot.source_version == "sw1":
        quality = "provisional" if quality != "unavailable" else quality
    return {
        "quality": quality,
        "te": float(np.sqrt(252 * variance)) if quality != "unavailable" else None,
        "daily_variance": variance,
        "factor_variance": fv,
        "specific_variance": sv,
        "factor_euler": dict(zip(snapshot.factors, (xf * (covariance.F @ xf)).tolist(), strict=True)),
        "prior_weight": {
            "portfolio": float(w[covariance.prior].sum()),
            "benchmark": float(snapshot.benchmark[covariance.prior].sum()),
        },
        "missing_weight": q["missing_weight"],
        "unknown_industry_weight": q["unknown_industry_weight"],
    }


def validate_bias(frame: pd.DataFrame, periods: dict | None = None) -> dict:
    """Pair actual T-close variance with next-day gross excess only on no-execution days."""
    from alphasieve.training.mandates import PERIODS

    needed = {"no_execution", "quality", "daily_variance", "gross_excess_next"}
    if not needed.issubset(frame):
        raise ValueError(f"bias validation needs {sorted(needed)}")
    periods = periods or PERIODS
    out = {}
    for label, (lo, hi) in periods.items():
        part = frame.loc[str(lo) : str(hi)]
        denominator = int(part["no_execution"].fillna(False).sum()) if "no_execution" in part else 0
        eligible = part["no_execution"].fillna(False) & (part["daily_variance"] > 1e-12)
        if "nav" in part:
            eligible &= part["nav"] > 0
        good = part[eligible & (part["quality"] == "valid")].dropna(subset=["gross_excess_next"])
        provisional = part[eligible & (part["quality"] == "provisional")].dropna(subset=["gross_excess_next"])
        n = len(good)
        b = float((good["gross_excess_next"] / np.sqrt(good["daily_variance"])).std(ddof=1)) if n >= 2 else None
        coverage = n / denominator if denominator else 0.0
        out[label] = {
            "pairs": n,
            "eligible_days": denominator,
            "coverage": coverage,
            "bias_B": b,
            "bias_minus_one": b - 1 if b is not None else None,
            "realised_te": float(good["gross_excess_next"].std(ddof=1) * np.sqrt(252)) if n >= 2 else None,
            "exante_te": float(np.sqrt(252 * good["daily_variance"].mean())) if n else None,
            "provisional_pairs": int(len(provisional)),
            "provisional_bias_B": (
                float((provisional["gross_excess_next"] / np.sqrt(provisional["daily_variance"])).std(ddof=1))
                if len(provisional) >= 2
                else None
            ),
            "zero_risk_days": int(
                (
                    (part.get("daily_variance", pd.Series(dtype=float)) <= 1e-12) & (part.get("no_execution", False))
                ).sum()
            ),
        }
    whole = out["all"]
    sub = [out[x] for x in ("2016-2018", "2019-2020", "2021-2022")]
    sufficient = whole["pairs"] >= 252 and whole["coverage"] >= 0.8 and all(x["pairs"] >= 126 for x in sub)
    calibrated = (
        whole["bias_B"] is not None
        and 0.8 <= whole["bias_B"] <= 1.2
        and all(x["bias_B"] is not None and 0.7 <= x["bias_B"] <= 1.3 for x in sub)
    )
    status = (
        "passed" if sufficient and calibrated else ("failed_calibration" if sufficient else "insufficient_evidence")
    )
    return {"status": status, "periods": out, "strict_pit": False}
