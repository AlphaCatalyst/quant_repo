import json
import stat

import numpy as np
import pandas as pd
import pytest

from alphasieve.cli.main import main
from alphasieve.data.access import load_panel
from alphasieve.data.panel import align_financials_pit, limit_ratios
from alphasieve.errors import AlphaSieveError


def run_cli(capsys, *argv):
    code = main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.fixture
def dev(panel_settings):
    return load_panel(panel_settings, "dev", role="system")


@pytest.fixture
def holdout(panel_settings):
    return load_panel(panel_settings, "holdout", role="system")


def row(panel, code, day):
    df = panel.long
    return df[(df["code"] == code) & (df["date"] == pd.Timestamp(day))].iloc[0]


def test_dev_panel_respects_window(dev):
    assert dev.long["date"].max() <= pd.Timestamp("2020-06-30")
    assert dev.meta["window"] == {"start": "2018-01-01", "end": "2020-06-30", "warmup_start": "2011-01-01"}


def test_holdout_directory_is_private(panel_settings, holdout):
    mode = stat.S_IMODE(panel_settings.panel_dir("holdout").stat().st_mode)
    assert mode == 0o700
    assert holdout.long["date"].max() <= pd.Timestamp("2021-12-31")


def test_agent_cannot_read_holdout(panel_settings):
    with pytest.raises(AlphaSieveError) as exc:
        load_panel(panel_settings, "holdout", role="agent")
    assert exc.value.code == "PERMISSION_DENIED"
    with pytest.raises(AlphaSieveError):
        load_panel(panel_settings, "holdout", role="human")


def test_limit_up_open_blocks_buying(dev, synth_info):
    code, day = synth_info.limit_up_events[0]
    r = row(dev, code, day)
    assert r["is_limit_up_open"] and not r["tradable_buy"]
    prev_day = dev.dates[dev.dates.get_loc(pd.Timestamp(day)) - 1]
    assert np.isnan(row(dev, code, prev_day)["label_5d"])


def test_suspension(dev, synth_info):
    start, end = synth_info.suspended_range
    df = dev.long[(dev.long["code"] == synth_info.suspended_code)
                  & (dev.long["date"] >= start) & (dev.long["date"] <= end)]
    assert df["is_suspended"].all() and not df["tradable_buy"].any() and not df["in_universe"].any()


def test_st_limit_ratio(dev, synth_info):
    r = row(dev, synth_info.st_code, synth_info.st_range[0])
    assert r["is_st"]
    assert r["limit_up"] == pytest.approx(np.floor(r["preclose"] * 1.05 * 100 + 0.5) / 100)


def test_limit_ratio_rules():
    codes = pd.Series(["sh.600000", "sz.300001", "sz.300001", "sh.688001", "bj.830001", "sz.000001"])
    dates = pd.Series(["2019-01-02", "2019-01-02", "2021-01-04", "2021-01-04", "2021-01-04", "2021-01-04"])
    st = pd.Series([False, False, False, False, False, True])
    assert list(limit_ratios(codes, dates, st)) == [0.10, 0.10, 0.20, 0.20, 0.30, 0.05]


def test_split_is_adjusted(dev, synth_info):
    df = dev.long[dev.long["code"] == synth_info.split_code].set_index("date")
    day = pd.Timestamp(synth_info.split_date)
    prev = df.index[df.index.get_loc(day) - 1]
    assert df.loc[day, "close_raw"] < 0.6 * df.loc[prev, "close_raw"]
    assert abs(df.loc[day, "ret_1d"]) < 0.1


def test_ipo_days_listed_and_universe(dev, synth_info):
    df = dev.long[dev.long["code"] == synth_info.ipo_code].sort_values("date")
    assert df["date"].min() == pd.Timestamp(synth_info.ipo_date)
    assert df.iloc[0]["days_listed"] == 0
    assert not df.head(60)["in_universe"].any()


def test_delisted_has_no_rows_after_delist(holdout, synth_info):
    df = holdout.long[holdout.long["code"] == synth_info.delisted_code]
    assert df["date"].max() < pd.Timestamp(synth_info.delist_date)


def test_membership_is_point_in_time(dev, synth_root):
    root, _ = synth_root
    members = pd.read_parquet(root / "hot" / "data" / "raw" / "baostock" / "members.parquet")
    snap = members[(members["snapshot_date"] == "2019-07-01") & (members["index"] == "hs300")]["code"]
    july = dev.long[(dev.long["date"] >= "2019-07-01") & (dev.long["date"] <= "2019-07-31")]
    in_hs = set(july.loc[july["in_hs300"], "code"])
    assert in_hs == set(snap)
    june = dev.long[(dev.long["date"] >= "2019-06-01") & (dev.long["date"] <= "2019-06-28")]
    assert set(june.loc[june["in_hs300"], "code"]) != in_hs


def test_label_definition(dev):
    open_w = dev.wide("open")
    label = dev.wide("label_5d")
    buy = dev.mask("tradable_buy")
    t = 300
    code = dev.codes[0]
    expected = open_w[code].iloc[t + 6] / open_w[code].iloc[t + 1] - 1
    if buy[code].iloc[t + 1]:
        assert label[code].iloc[t] == pytest.approx(expected)


def test_embargo(dev):
    label = dev.wide("label_5d")
    window_dates = dev.dates[dev.dates <= pd.Timestamp("2020-06-30")]
    assert label.loc[window_dates[-6:]].isna().all().all()
    assert label.loc[window_dates[-30:-10]].notna().any().any()
    assert dev.wide("label_20d").loc[window_dates[-21:]].isna().all().all()


def test_financials_point_in_time(dev, synth_root):
    root, _ = synth_root
    code = dev.codes[3]
    fin = pd.read_parquet(root / "hot" / "data" / "raw" / "baostock" / "financials" / "profit" / f"{code}.parquet")
    record = fin[(fin["pubDate"] > "2019-01-10") & (fin["pubDate"] < "2020-01-01")].iloc[0]
    pub = pd.Timestamp(record["pubDate"])
    series = dev.long[dev.long["code"] == code].set_index("date")["roe_avg"]
    on_or_before = series[series.index <= pub]
    after = series[series.index > pub]
    assert on_or_before.iloc[-1] != pytest.approx(record["roeAvg"])
    assert after.iloc[0] == pytest.approx(record["roeAvg"])


def test_align_financials_ignores_late_old_statements():
    fin = pd.DataFrame({
        "code": ["a", "a", "a"],
        "pubDate": ["2020-04-20", "2020-04-25", "2020-04-28"],
        "statDate": ["2020-03-31", "2019-12-31", "2020-03-31"],
        "roeAvg": [0.1, 0.2, 0.3],
    })
    cal = ["2020-04-20", "2020-04-21", "2020-04-27", "2020-04-28", "2020-04-29"]
    out = align_financials_pit(fin, cal, {"roeAvg": "roe_avg"})
    assert out["roe_avg"].tolist() == [0.1]
    assert out["effective_date"].tolist() == ["2020-04-21"]


def test_quality_report_written(panel_settings, dev):
    report = json.loads((panel_settings.quality_dir / "dev.json").read_text())
    assert set(report["checks"]) >= {"universe_size_min", "close_coverage_min", "missing_member_months"}


def test_data_status_and_describe_cli(panel_settings, capsys, monkeypatch):
    code, out = run_cli(capsys, "data", "status")
    assert code == 0 and set(out["data"]["tiers"]) == {"dev", "holdout"}
    monkeypatch.setenv("ALPHASIEVE_ROLE", "agent")
    code, out = run_cli(capsys, "data", "status")
    assert set(out["data"]["tiers"]) == {"dev"}
    code, out = run_cli(capsys, "data", "describe", "turnover_rate")
    assert code == 0 and 0 < out["data"]["coverage"] <= 1
    code, out = run_cli(capsys, "data", "sample", "--date", "2019-03-01", "--fields", "close,label_5d")
    assert code == 2


def test_baostock_query_deadline():
    import time

    import pytest

    from alphasieve.data.providers.baostock import _deadline

    start = time.monotonic()
    with pytest.raises(TimeoutError), _deadline(1):
        while True:
            pass
    assert time.monotonic() - start < 3
