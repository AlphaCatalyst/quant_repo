"""Rebuild an index total return from published month-end constituent weights."""

import numpy as np
import pandas as pd

from alphasieve.data.access import Panel
from alphasieve.errors import validation_error


def daily_returns(panel: Panel, snapshots: pd.DataFrame) -> np.ndarray:
    """Weights at a snapshot close earn returns from the next trading day; holdings drift until replaced."""
    required = ("trade_date", "stock_code", "weight")
    if not set(required).issubset(snapshots.columns):
        raise validation_error("official index weights need trade_date, stock_code and weight")
    frame = snapshots[list(required)].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["weight"] = pd.to_numeric(frame["weight"], errors="coerce")
    if frame["trade_date"].isna().any() or frame["weight"].isna().any() or (frame["weight"] < 0).any():
        raise validation_error("invalid official index weight date or value")
    code = frame["stock_code"].astype(str).str.lower()
    frame["code"] = np.where(code.str.endswith(".sh"), "sh." + code.str[:6],
                             np.where(code.str.endswith(".sz"), "sz." + code.str[:6], code))
    if frame.duplicated(["trade_date", "code"]).any():
        raise validation_error("duplicate official index weight constituents")
    grouped = {d: g.set_index("code")["weight"] for d, g in frame.groupby("trade_date")}
    dates = panel.dates
    close = panel.wide("close").to_numpy(dtype=float)
    code_index = pd.Index(panel.codes)
    releases = sorted(grouped)
    out = np.full(len(dates), np.nan)
    if not releases:
        raise validation_error("official index weights are empty")
    current = None
    release_pos = 0
    shares = None
    for t in range(1, len(dates)):
        while release_pos < len(releases) and releases[release_pos] <= dates[t - 1]:
            current = grouped[releases[release_pos]]
            release_pos += 1
            shares = None
        if current is None:
            continue
        if shares is None:
            aligned = current.reindex(code_index).fillna(0.0).to_numpy(dtype=float)
            if current.sum() <= 0:
                raise validation_error("official index weights sum to zero")
            covered = aligned.sum() / current.sum()
            if covered < 0.99:
                raise validation_error("official index weight constituents missing from panel")
            valid = np.isfinite(close[t - 1]) & (close[t - 1] > 0)
            if aligned[~valid].sum() / aligned.sum() > 0.01:
                raise validation_error("official index weight prices missing from panel")
            aligned = np.where(valid, aligned, 0.0)
            shares = aligned / aligned.sum() / np.where(valid, close[t - 1], 1.0)
        held = shares > 0
        if not (np.isfinite(close[t - 1, held]).all() and np.isfinite(close[t, held]).all()
                and (close[t - 1, held] > 0).all() and (close[t, held] > 0).all()):
            raise validation_error("official index weight reconstruction has missing constituent prices")
        prev = float(np.dot(shares, np.nan_to_num(close[t - 1], nan=0.0)))
        now = float(np.dot(shares, np.nan_to_num(close[t], nan=0.0)))
        if prev <= 0 or now <= 0:
            raise validation_error("official index weight reconstruction has missing prices")
        out[t] = now / prev - 1
    return out


def daily_weights(panel: Panel, snapshots: pd.DataFrame) -> np.ndarray:
    """Close-of-day reference weights, including price drift between published snapshots."""
    dates = panel.dates
    close = panel.wide("close").to_numpy(dtype=float)
    frame = snapshots.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame["code"] = frame["stock_code"].str.lower().map(
        lambda code: code[-2:] + "." + code[:6] if code.endswith((".sh", ".sz")) else code)
    groups = {date: group.set_index("code")["weight"] for date, group in frame.groupby("trade_date")}
    out = np.zeros(close.shape, dtype=float)
    for t, date in enumerate(dates):
        if date in groups:
            row = groups[date].reindex(panel.codes).fillna(0).to_numpy(dtype=float)
            if row.sum() <= 0 or row.sum() / groups[date].sum() < 0.99:
                raise validation_error("official index weight constituents missing from panel")
            out[t] = row / row.sum()
        elif t and out[t - 1].sum() > 0:
            held = out[t - 1] > 0
            if not (np.isfinite(close[t, held]).all() and np.isfinite(close[t - 1, held]).all()
                    and (close[t, held] > 0).all() and (close[t - 1, held] > 0).all()):
                raise validation_error("official index weight drift has missing constituent prices")
            ratio = np.divide(close[t], close[t - 1], out=np.ones(close.shape[1]),
                              where=np.isfinite(close[t]) & np.isfinite(close[t - 1]) & (close[t - 1] > 0))
            row = out[t - 1] * ratio
            out[t] = row / row.sum()
        elif not t:
            prior = [date0 for date0 in groups if date0 <= date]
            if prior:
                row = groups[max(prior)].reindex(panel.codes).fillna(0).to_numpy(dtype=float)
                out[t] = row / row.sum()
    return out
