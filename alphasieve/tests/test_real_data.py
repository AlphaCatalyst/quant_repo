"""Checks against the real panel under /data/alphasieve (T3 data validation, T5 golden comparison).

Run with ALPHASIEVE_RUN_REALDATA=1 (and ALPHASIEVE_RUN_NETWORK=1 for the cross-source check).
"""

import os
import re
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import spearmanr

from alphasieve.config import DEFAULT_HOT_ROOT, PACKAGE_CONFIG_DIR, Settings
from alphasieve.data.access import load_panel
from alphasieve.errors import AlphaSieveError
from alphasieve.evaluation.core import EvalInputs, l1_metrics
from alphasieve.factors.dsl import compile_expression, evaluate
from alphasieve.search_space import load_search_space

pytestmark = pytest.mark.realdata


@pytest.fixture(scope="module")
def real_settings() -> Settings:
    return Settings(hot_root=Path(os.environ.get("ALPHASIEVE_REAL_HOT_ROOT", DEFAULT_HOT_ROOT)),
                    store_root=Path("/tmp/alphasieve-realdata-tests"), store_mount=None,
                    config_dir=PACKAGE_CONFIG_DIR, role="system", user="test")


@pytest.fixture(scope="module")
def dev(real_settings):
    return load_panel(real_settings, "dev", role="system"), real_settings


def day(panel, date):
    return panel.long[panel.long["date"] == pd.Timestamp(date)]


def test_2015_crash_mass_suspension(dev):
    panel, _ = dev
    rows = day(panel, "2015-07-08")
    members = rows[rows["in_csi800"]]
    assert len(members) > 700
    assert members["is_suspended"].mean() > 0.2


def test_2020_02_03_mass_limit_down(dev):
    panel, _ = dev
    rows = day(panel, "2020-02-03")
    active = rows[rows["in_csi800"] & ~rows["is_suspended"]]
    assert active["is_limit_down_open"].mean() > 0.3


def test_no_trading_on_national_day(dev):
    panel, _ = dev
    assert not panel.long["date"].between("2015-10-01", "2015-10-07").any()


def test_ping_an_always_in_hs300(dev):
    panel, _ = dev
    rows = panel.long[panel.long["code"] == "sh.601318"].set_index("date")
    for year in range(2013, 2023):
        first = rows[rows.index.year == year].iloc[0]
        assert first["in_hs300"], year


def test_hs300_rebalances_in_january_and_july(dev, real_settings):
    members = pd.read_parquet(real_settings.hot_root / "data/raw/baostock/members.parquet")
    hs = members[members["index"] == "hs300"].groupby("snapshot_date")["code"].apply(set).sort_index()
    changes = {d: len(hs.iloc[i] - hs.iloc[i - 1]) for i, d in enumerate(hs.index) if i > 0}
    total = sum(changes.values())
    semiannual = sum(v for d, v in changes.items() if d[5:7] in ("01", "07", "06", "12"))
    assert total > 100
    assert semiannual / total > 0.7


def test_derived_limit_prices_match_limit_closes(dev):
    panel, _ = dev
    df = panel.long
    main = ~df["code"].str.startswith(("sz.300", "sz.301", "sh.688")) & ~df["is_st"] & (df["days_listed"] >= 5)
    limit_closes = df[main & ~df["is_suspended"] & (df["close_raw"] / df["preclose"] - 1 > 0.0995)]
    assert len(limit_closes) > 1000
    matched = (limit_closes["close_raw"] - limit_closes["limit_up"]).abs() <= 0.011
    assert matched.mean() > 0.95


def test_main_board_returns_within_limits(dev):
    panel, _ = dev
    df = panel.long
    main = ~df["code"].str.startswith(("sz.300", "sz.301", "sh.688")) & (df["days_listed"] >= 5) & ~df["is_suspended"]
    raw_ret = df.loc[main, "close_raw"] / df.loc[main, "preclose"] - 1
    assert (raw_ret.abs() > 0.105).mean() < 0.001


def test_dividend_adjustment_is_continuous(dev):
    panel, _ = dev
    rows = panel.long[panel.long["code"] == "sh.600519"].set_index("date")
    raw_jump = rows["preclose"] / rows["close_raw"].shift(1) - 1
    ex_dates = raw_jump[raw_jump < -0.005].index
    assert len(ex_dates) >= 5
    assert (rows.loc[ex_dates, "ret_1d"] > -0.08).all()


@pytest.mark.network
def test_cross_source_returns_and_volume(dev):
    panel, _ = dev
    codes = panel.long[panel.long["in_csi800"] & (panel.long["date"] == "2020-01-02")]["code"].sample(
        10, random_state=0)
    checked = 0
    for code in codes:
        out = subprocess.run(["westock-data", "kline", code.replace(".", ""), "--start", "2020-01-02", "--end",
                              "2020-01-23"], capture_output=True, text=True, timeout=60).stdout
        rows = [line.split("|")[1:-1] for line in out.splitlines() if re.match(r"\|\s*20\d\d-", line)]
        if not rows:
            continue
        ws = pd.DataFrame([[c.strip() for c in r] for r in rows],
                          columns=["date", "open", "last", "high", "low", "volume", "amount", "exchange"])
        ws = ws.assign(date=pd.to_datetime(ws["date"]), last=ws["last"].astype(float),
                       volume=ws["volume"].astype(float)).set_index("date").sort_index()
        ours = panel.long[panel.long["code"] == code].set_index("date").loc[ws.index]
        # westock's default forward adjustment subtracts cash dividends and scales for share splits, so its daily
        # price changes equal a constant multiple of the unadjusted changes between corporate actions.
        ws_diff = ws["last"].diff().dropna()
        our_diff = ours["close_raw"].diff().loc[ws_diff.index]
        moving = our_diff.abs() > 0.05
        scale = float((ws_diff[moving] / our_diff[moving]).median()) if moving.sum() >= 3 else 1.0
        assert ((ws_diff - scale * our_diff).abs() <= 0.011 + 0.01 * scale).mean() >= 0.9, code
        active = ours["volume"] > 0
        assert np.allclose(ws.loc[active, "volume"] * 100, ours.loc[active, "volume"], rtol=0.01), code
        checked += 1
    assert checked >= 5


GOLDEN = {
    "reversal_5d": ("ts_sum(ret_1d, 5)", lambda g: g["ret_1d"].rolling(5, min_periods=5).sum()),
    "reversal_20d": ("ts_sum(ret_1d, 20)", lambda g: g["ret_1d"].rolling(20, min_periods=20).sum()),
    "vol_20d": ("ts_std(ret_1d, 20)", lambda g: g["ret_1d"].rolling(20, min_periods=20).std()),
    "turnover_20d": ("ts_mean(turnover_rate, 20)", lambda g: g["turnover_rate"].rolling(20, min_periods=20).mean()),
    "size": ("log(circ_mv)", lambda g: np.log(g["circ_mv"].where(g["circ_mv"] > 0))),
    "book_to_price": ("1 / pb_mrq", lambda g: (1 / g["pb_mrq"].where(g["pb_mrq"] != 0))),
    "max_ret_20d": ("ts_max(ret_1d, 20)", lambda g: g["ret_1d"].rolling(20, min_periods=20).max()),
    "volume_ratio": ("ts_mean(volume, 5) / ts_mean(volume, 20)",
                     lambda g: g["volume"].rolling(5, min_periods=5).mean()
                     / g["volume"].rolling(20, min_periods=20).mean().replace(0, np.nan)),
    "pv_corr_20d": ("ts_corr(close, volume, 20)",
                    lambda g: g["close"].rolling(20, min_periods=20).corr(g["volume"])),
    "range_20d": ("ts_mean((high - low) / close, 20)",
                  lambda g: ((g["high"] - g["low"]) / g["close"]).rolling(20, min_periods=20).mean()),
}


def _reference_ic(panel, series: pd.Series) -> float:
    start, end = panel.window
    df = panel.long[["date", "code", "in_universe", "label_5d"]].copy()
    df["f"] = series.replace([np.inf, -np.inf], np.nan).to_numpy()
    df = df[(df["date"] >= start) & (df["date"] <= end) & df["in_universe"]].dropna(subset=["f", "label_5d"])
    ics = []
    for _, g in df.groupby("date"):
        if len(g) >= 20:
            ics.append(spearmanr(g["f"], g["label_5d"]).statistic)
    return float(np.nanmean(ics))


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_golden_factor_ic(dev, name):
    panel, settings = dev
    expr, reference = GOLDEN[name]
    compiled = compile_expression(expr, load_search_space(settings))
    ours = l1_metrics(evaluate(compiled, panel), EvalInputs(panel, 5), {})["ic_mean"]
    long = panel.long.sort_values(["code", "date"])
    ref_values = long.groupby("code", group_keys=False).apply(reference)
    ref_series = ref_values.reindex(panel.long.index)
    ref_ic = _reference_ic(panel, ref_series)
    assert ours == pytest.approx(ref_ic, abs=1e-6), (name, ours, ref_ic)


def test_holdout_is_private_on_disk(real_settings):
    import stat

    mode = stat.S_IMODE(real_settings.panel_dir("holdout").stat().st_mode)
    assert mode == 0o700
    with pytest.raises(AlphaSieveError):
        load_panel(real_settings, "holdout", role="agent")


def test_financials_become_visible_after_announcement(dev, real_settings):
    panel, _ = dev
    fin_dir = real_settings.hot_root / "data/raw/baostock/financials/profit"
    files = sorted(fin_dir.glob("*.parquet"))[:30]
    if not files:
        pytest.skip("financial data not downloaded yet")
    checked = 0
    for path in files:
        code = path.stem
        fin = pd.read_parquet(path).dropna(subset=["roeAvg"])
        fin = fin.sort_values(["statDate", "pubDate"]).drop_duplicates("statDate", keep="first")
        series = panel.long[panel.long["code"] == code].set_index("date")
        if series.empty or "roe_avg" not in series or series["roe_avg"].isna().all():
            continue
        for _, rec in fin[(fin["pubDate"] > "2013-01-01") & (fin["pubDate"] < "2022-06-30")].iterrows():
            pub = pd.Timestamp(rec["pubDate"])
            after = series[series.index > pub]
            if after.empty or after["profit_stat_date"].iloc[0] != rec["statDate"]:
                continue
            assert after["roe_avg"].iloc[0] == pytest.approx(rec["roeAvg"])
            before = series[series.index <= pub]
            if not before.empty:
                assert before["profit_stat_date"].iloc[-1] != rec["statDate"]
            checked += 1
    assert checked >= 20
