import numpy as np
import pandas as pd
import pytest

from alphasieve.data.report_features import REPORT_FIELDS, attach_report_features


def _row(report_id, date, broker, year, eps, rating="buy", revision=None):
    return {"report_id": report_id, "code": "sh.600000", "publish_date": date, "broker": broker,
            "fiscal_year": year, "eps": eps, "rating": rating, "is_revision": revision}


def test_report_features_pit_broker_latest_and_old_columns_stable():
    dates = pd.bdate_range("2021-01-01", "2021-05-31")
    calendar = dates.strftime("%Y-%m-%d").tolist()
    panel = pd.DataFrame({"date": dates, "code": "sh.600000", "close_raw": np.arange(len(dates)) + 10.0,
                          "legacy": np.arange(len(dates), dtype=float)})
    panel = pd.concat([panel, panel.assign(code="sz.000001", legacy=-1.0)], ignore_index=True)
    original = panel.copy(deep=True)
    reports = pd.DataFrame([
        _row("a", "2021-01-04", "甲", 2021, 1.0, "buy", "up"),
        _row("a", "2021-01-04", "甲", 2022, 2.0, "buy", "up"),
        _row("b", "2021-01-05", "乙", 2021, 3.0, "hold", "down"),
        _row("c", "2021-01-06", "甲", 2021, 2.0, "strong_buy", "up"),
        _row("d", "2021-02-10", "乙", 2021, 4.0, "sell", "up"),
        _row("e", "2021-02-12", "丙", None, None, "hold"),
    ])
    out = attach_report_features(panel, reports, calendar)
    pd.testing.assert_frame_equal(out[original.columns], original)
    assert len(out) == len(original)
    a = out[out["code"] == "sh.600000"].set_index("date")
    assert np.isnan(a.loc["2021-01-04", "wr_eps_fy1"])
    assert a.loc["2021-01-05", "wr_eps_fy1"] == 1.0
    assert a.loc["2021-01-05", "wr_eps_fy2"] == 2.0
    assert a.loc["2021-01-06", "wr_eps_fy1"] == 2.0
    assert a.loc["2021-01-07", "wr_eps_fy1"] == 2.5  # latest per broker, then median
    assert a.loc["2021-01-07", "wr_coverage_90d"] == 2.0
    assert a.loc["2021-01-07", "wr_rating_mean"] == 4.0
    assert a.loc["2021-01-07", "wr_revision_balance_30d"] == pytest.approx(1 / 3)
    assert a.loc["2021-01-07", "wr_consensus_ep"] == pytest.approx(2.5 / a.loc["2021-01-06", "close_raw"])
    assert a.loc["2021-02-10", "wr_eps_fy1"] == 2.5
    assert a.loc["2021-02-11", "wr_eps_fy1"] == 3.0
    assert a.loc["2021-02-11", "wr_eps_change_30d"] == pytest.approx(3 / 2.5 - 1)
    assert a.loc["2021-02-11", "wr_rating_change_30d"] == pytest.approx(-0.5)
    assert a.loc["2021-02-15", "wr_coverage_90d"] == 3.0  # rating-only report counts as coverage
    assert a.loc["2021-02-15", "wr_eps_fy1"] == 3.0
    assert out.loc[out["code"] == "sz.000001", REPORT_FIELDS].isna().all().all()


def test_report_features_calendar_year_roll_and_stale_coverage():
    dates = pd.bdate_range("2021-12-28", "2022-07-04")
    calendar = dates.strftime("%Y-%m-%d").tolist()
    panel = pd.DataFrame({"date": dates, "code": "sh.600000", "close_raw": 10.0})
    reports = pd.DataFrame([
        _row("a", "2021-12-28", "甲", 2021, 1.0),
        _row("a", "2021-12-28", "甲", 2022, 2.0),
        _row("a", "2021-12-28", "甲", 2023, 3.0),
    ])
    out = attach_report_features(panel, reports, calendar).set_index("date")
    assert out.loc["2021-12-29", "wr_eps_fy1"] == 1.0
    assert out.loc["2022-01-03", "wr_eps_fy1"] == 2.0
    assert out.loc["2022-01-03", "wr_eps_fy2"] == 3.0
    assert out.loc["2022-01-03", "wr_eps_growth"] == pytest.approx(0.5)
    assert out.loc["2022-07-04", "wr_coverage_180d"] == 0.0
    assert np.isnan(out.loc["2022-07-04", "wr_eps_fy1"])


def test_panel_report_columns_do_not_change_existing_columns(built_root, tmp_path, monkeypatch):
    import shutil

    from alphasieve.config import get_settings
    from alphasieve.data.panel import build_long_panel

    fixture_root, _ = built_root
    hot = tmp_path / "hot"
    raw = hot / "data" / "raw"
    shutil.copytree(fixture_root / "hot" / "data" / "raw" / "baostock", raw / "baostock")
    monkeypatch.setenv("ALPHASIEVE_HOT_ROOT", str(hot))
    monkeypatch.setenv("ALPHASIEVE_CONFIG_DIR", str(fixture_root / "configs"))
    monkeypatch.setenv("ALPHASIEVE_ROLE", "system")
    settings = get_settings()
    baseline, calendar, _ = build_long_panel(settings, "2019-12-31")
    code = baseline["code"].iloc[0]
    reports_dir = raw / "westock" / "reports"
    reports_dir.mkdir(parents=True)
    pd.DataFrame([_row("r1", "2019-04-25", "甲", 2019, 2.0)]).assign(code=code).to_parquet(
        reports_dir / "parsed.parquet", index=False)
    extended, _, _ = build_long_panel(settings, "2019-12-31")
    pd.testing.assert_frame_equal(extended[baseline.columns], baseline)
    day = next(d for d in calendar if d > "2019-04-25")
    stock = extended[extended["code"] == code].set_index("date")
    assert stock.loc[day, "wr_eps_fy1"] == 2.0
    assert stock.loc[day, "wr_consensus_ep"] == pytest.approx(
        2.0 / stock["close_raw"].shift(1).loc[day])
