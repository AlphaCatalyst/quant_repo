"""Mandate C's walk-forward event scores as sparse features of the A task (docs/20 §2).

The source is a frozen C run: ``event_scores.parquet`` holds one out-of-sample score per (event date, code), where
the event date is the first panel day that shows the new report and the score uses nothing later. A may read an
event from ``event date + lag`` trading days on. Raw scores are not comparable across C refits, so each score is
turned into its percentile among the C scores of the 250 trading days before the start of its month.
"""

import numpy as np
import pandas as pd

from alphasieve.config import Settings
from alphasieve.data.access import Panel
from alphasieve.errors import validation_error
from alphasieve.util import file_sha256

MAX_AGE = 60
REFERENCE_DAYS = 250
MIN_REFERENCE = 200


def source_path(settings: Settings, task_id: str, trial_id: str):
    runs = sorted((settings.store_root / "models" / task_id).glob(f"*{trial_id}*"))
    if len(runs) != 1 or not (runs[0] / "event_scores.parquet").exists():
        raise validation_error(f"expected one {task_id} run of trial {trial_id} with event_scores.parquet",
                               found=[str(r) for r in runs])
    return runs[0] / "event_scores.parquet"


def freeze_source(settings: Settings, task_id: str, trial_id: str) -> dict:
    path = source_path(settings, task_id, trial_id)
    return {"task_id": task_id, "trial_id": trial_id, "sha256": file_sha256(path)}


def _percentiles(events: pd.DataFrame) -> np.ndarray:
    """Percentile of each score among the scores dated in the 250 trading days before its month began."""
    days = pd.DatetimeIndex(sorted(events["date"].unique()))
    out = np.full(len(events), np.nan)
    month = events["date"].dt.to_period("M")
    for m, idx in events.groupby(month).indices.items():
        start = days.searchsorted(m.start_time)
        lo = days[max(0, start - REFERENCE_DAYS)] if start > 0 else None
        if lo is None:
            continue
        ref = np.sort(events["score"][(events["date"] >= lo) & (events["date"] < m.start_time)].dropna().to_numpy())
        if len(ref) < MIN_REFERENCE:
            continue
        out[idx] = np.searchsorted(ref, events["score"].to_numpy()[idx], side="right") / len(ref)
    return out


def event_frames(settings: Settings, panel: Panel, source: dict, lag: int,
                 half_lives: list[int]) -> tuple[dict[str, pd.DataFrame], dict]:
    path = source_path(settings, source["task_id"], source["trial_id"])
    if file_sha256(path) != source["sha256"]:
        raise validation_error("the frozen event-score artifact changed since the bundle was made", path=str(path))
    raw = pd.read_parquet(path).reset_index()
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw.sort_values("date").reset_index(drop=True)
    raw["pct"] = _percentiles(raw)
    ev = raw.dropna(subset=["pct"])
    t = panel.dates.get_indexer(ev["date"])
    c = pd.Index(panel.codes).get_indexer(ev["code"])
    ok = (t >= 0) & (c >= 0)
    shape = (len(panel.dates), len(panel.codes))
    event_t = np.full(shape, np.nan)
    event_s = np.full(shape, np.nan)
    # an event is written on the day it becomes readable, ``lag`` days after its date; later events overwrite
    for ti, ci, s in sorted(zip(t[ok], c[ok], 2 * ev["pct"].to_numpy()[ok] - 1, strict=True)):
        if ti + lag < shape[0]:
            event_t[ti + lag, ci] = ti
            event_s[ti + lag, ci] = s
    held_t = pd.DataFrame(event_t).ffill().to_numpy()
    held_s = pd.DataFrame(event_s).ffill().to_numpy()
    age = np.arange(shape[0])[:, None] - held_t
    fresh = np.isfinite(age) & (age <= MAX_AGE)
    frames = {"event_age": np.where(fresh, age, MAX_AGE + 1).astype(float),
              "event_missing": (~fresh).astype(float),
              "event_score_last": np.where(fresh, held_s, 0.0)}
    for hl in half_lives:
        frames[f"event_decay_{hl}"] = np.where(fresh, held_s * np.exp(-np.log(2) * np.nan_to_num(age) / hl), 0.0)
    window = panel.window_mask().to_numpy()
    report = {"source": source, "events": int(len(raw)), "events_scored": int(ok.sum()),
              "events_without_reference": int(raw["pct"].isna().sum()), "lag_days": lag,
              "fresh_share_in_window": float(fresh[window].mean()),
              "first_event": str(ev["date"].min().date()) if len(ev) else None,
              "future_rows": int((age[np.isfinite(age)] < lag).sum())}
    if report["future_rows"]:
        raise validation_error("event features would read events before they are available", **report)
    return {k: pd.DataFrame(v, index=panel.dates, columns=panel.codes) for k, v in frames.items()}, report


def event_feature_names(half_lives: list[int]) -> tuple[str, ...]:
    return ("event_age", "event_missing", "event_score_last", *(f"event_decay_{hl}" for hl in half_lives))
