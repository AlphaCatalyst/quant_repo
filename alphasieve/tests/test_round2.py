import numpy as np
import pandas as pd
import pytest
from test_training import task_dict

from alphasieve.data.access import load_panel
from alphasieve.errors import AlphaSieveError
from alphasieve.training import derived, etf_industry
from alphasieve.training.etf import mapped_features
from alphasieve.training.event_features import event_frames, freeze_source
from alphasieve.training.task import parse_task

SOURCE = {"task_id": "c_src", "trial_id": "S-0123456789ab"}


@pytest.mark.parametrize("changes, message", [
    ({"mandate": "B", "label__kind": "etf_relative_return", "portfolio": {"kind": "etf_rotation"},
      "features__event_source": SOURCE}, "event-score features"),
    ({"features__event_source": SOURCE, "features__event_lag_days": 0}, "event_lag_days"),
    ({"features__etf_mapping": {"mode": "industry_map", "mapping_asof": "2026-09-30"}}, "etf_mapping"),
    ({"portfolio__hedge_ratios": [0.5, 1.0]}, "hedge_ratios"),
])
def test_round2_task_rules(changes, message):
    with pytest.raises(AlphaSieveError, match=message):
        parse_task(task_dict(**changes))


def _write_events(settings, task_id: str, events: pd.DataFrame) -> dict:
    run = settings.store_root / "models" / task_id / f"run-{SOURCE['trial_id']}"
    run.mkdir(parents=True, exist_ok=True)
    events.to_parquet(run / "event_scores.parquet", index=False)
    return freeze_source(settings, task_id, SOURCE["trial_id"])


def test_event_features_never_read_events_before_the_lag(panel_settings):
    dev = load_panel(panel_settings, "dev", role="system")
    rng = np.random.default_rng(0)
    rows = [(d, c, rng.normal()) for d in dev.dates for c in rng.choice(dev.codes, 3, replace=False)]
    events = pd.DataFrame(rows, columns=["date", "code", "score"])
    cut = dev.dates[len(dev.dates) * 2 // 3]
    full = _write_events(panel_settings, "c_full", events)
    early = _write_events(panel_settings, "c_early", events[events["date"] < cut])
    a, report = event_frames(panel_settings, dev, full, 1, [5, 20])
    b, _ = event_frames(panel_settings, dev, early, 1, [5, 20])
    assert report["future_rows"] == 0 and report["events_scored"] > 0
    for name in a:
        np.testing.assert_allclose(a[name].loc[:cut].to_numpy(), b[name].loc[:cut].to_numpy())
    assert (a["event_missing"].loc[cut:].to_numpy() != b["event_missing"].loc[cut:].to_numpy()).any()
    (panel_settings.store_root / "models" / "c_full" / f"run-{SOURCE['trial_id']}" / "event_scores.parquet") \
        .write_bytes(b"changed")
    with pytest.raises(AlphaSieveError, match="changed"):
        event_frames(panel_settings, dev, full, 1, [5, 20])


def test_derived_factors_use_only_past_rows(panel_settings):
    dev = load_panel(panel_settings, "dev", role="system")
    names = ["mom_60", "dist_high_250", "ret_skew_60", "turnover_cv_20"]
    before = derived.derived_frames(dev, names)
    cut = dev.dates[len(dev.dates) // 2]
    for field in ("close", "ret_1d", "turnover_rate"):
        w = dev.wide(field).astype(float).copy()
        w.loc[w.index > cut] *= 1.7
        dev.set_wide(field, w)
    after = derived.derived_frames(dev, names)
    for name in names:
        np.testing.assert_allclose(before[name].loc[:cut].to_numpy(), after[name].loc[:cut].to_numpy())
    with pytest.raises(AlphaSieveError, match="unknown derived"):
        derived.derived_frames(dev, ["no_such_factor"])


def _stock_long(days: int = 320, per_industry: int = 10) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    dates = pd.bdate_range("2015-01-05", periods=days)
    frames = []
    for g, ind in enumerate("ABCD"):
        for k in range(per_industry):
            code = f"sh.60{g}{k:03d}"
            close = 10 * np.cumprod(1 + rng.normal(0.0005 * g, 0.02, days))
            frames.append(pd.DataFrame({
                "date": dates, "code": code, "industry": ind, "close": close, "close_raw": close,
                "limit_up": close * 1.1, "circ_mv": rng.uniform(1e9, 5e9), "turnover_rate": rng.uniform(0.5, 3, days),
                "pe_ttm": rng.uniform(5, 50, days), "pb_mrq": rng.uniform(0.5, 5, days),
                "ws_np_surprise_q": rng.normal(size=days), "ws_np_growth_q_yoy": rng.normal(size=days),
                "mf_main_net_ratio": rng.normal(size=days), "in_universe": True}))
    return pd.concat(frames, ignore_index=True)


HOLDINGS = {
    "sh512880": [{"code": f"60{0}{k:03d}", "weight": 0.19} for k in range(5)] + [{"code": "601000", "weight": 0.05}],
    "sh512800": [{"code": f"60{2}{k:03d}", "weight": 0.1} for k in range(8)],
}


def test_etf_industry_features_are_causal_and_follow_the_mapping():
    long = _stock_long()
    dates = pd.DatetimeIndex(sorted(long["date"].unique()))
    out, info = etf_industry.build_features(long, HOLDINGS, dates)
    assert info["industry_weights"]["sh512880"] == {"A": 1.0}
    assert info["industry_weights"]["sh512800"] == {"C": 1.0}
    ind = out["industry_map"]["ind_mom_20"]
    assert list(ind.columns) == ["sh.512880", "sh.512800"]

    ranks, _, _ = etf_industry.stock_ranks(long)
    held = [f"sh.600{k:03d}" for k in range(5)] + ["sh.601000"]
    w = np.array([0.19] * 5 + [0.05])
    day = dates[-1]
    expected = float((ranks["mom_20"].loc[day, held].to_numpy() * w).sum() / w.sum())
    assert out["basket_map"]["ind_mom_20"].loc[day, "sh.512880"] == pytest.approx(expected)

    cut = dates[200]
    early, _ = etf_industry.build_features(long[long["date"] <= cut], HOLDINGS, dates[dates <= cut])
    for mode in etf_industry.MODES:
        for name, frame in early[mode].items():
            np.testing.assert_allclose(out[mode][name].loc[:cut].to_numpy(), frame.to_numpy())


def test_mapped_features_drop_low_coverage(panel_settings):
    dev = load_panel(panel_settings, "dev", role="system")
    mask = np.ones((len(dev.dates), len(dev.codes)), dtype=bool)
    full = pd.DataFrame(1.0, index=dev.dates, columns=dev.codes)
    sparse = full.where(np.broadcast_to(np.arange(len(dev.dates))[:, None] % 2 == 0, full.shape))
    kept, report = mapped_features(dev, {"ind_full": full, "ind_sparse": sparse}, mask, 0.7)
    assert list(kept) == ["ind_full"] and report["dropped_low_coverage"] == {"ind_sparse": 0.5}
