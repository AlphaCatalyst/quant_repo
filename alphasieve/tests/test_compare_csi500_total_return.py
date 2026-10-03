import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "compare_csi500_total_return.py"
spec = importlib.util.spec_from_file_location("compare_csi500_total_return", SCRIPT)
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


def test_raw_proxy_and_official_comparison_stays_in_dev(tmp_path):
    raw = tmp_path / "raw"
    stock_dir = raw / "baostock" / "daily"
    stock_dir.mkdir(parents=True)
    official_dir = raw / "csindex" / "total_return"
    official_dir.mkdir(parents=True)
    dates = ["2021-12-31", "2022-01-03", "2022-01-04", "2022-01-05", "2023-01-03"]
    pd.DataFrame({"calendar_date": dates, "is_trading_day": 1}).to_parquet(
        raw / "baostock" / "trade_dates.parquet", index=False)
    pd.DataFrame([
        {"snapshot_date": month, "index": "zz500", "code": code}
        for month in ("2021-12-01", "2022-01-01", "2023-01-01")
        for code in ("sh.600000", "sz.000001")
    ]).to_parquet(raw / "baostock" / "members.parquet", index=False)
    for code, close in (("sh.600000", [100, 100, 110, 121, 999]),
                        ("sz.000001", [100, 100, 90, 81, 999])):
        pd.DataFrame({"date": dates, "close": close, "volume": 100, "turn": 10}).to_parquet(
            stock_dir / f"{code}.parquet", index=False)
    pd.DataFrame({"date": dates, "close": [100, 100, 101, 103, 999]}).to_parquet(
        official_dir / "H00905.parquet", index=False)
    weight_dir = raw / "dolthub" / "index_weights"
    weight_dir.mkdir(parents=True)
    pd.DataFrame([{"trade_date": "2021-12-31", "stock_code": code, "weight": 50.0}
                  for code in ("600000.SH", "000001.SZ")]).to_parquet(
                      weight_dir / "000905.SH.parquet", index=False)

    proxy = compare.proxy_returns_from_raw(raw)
    assert proxy.index.max() == pd.Timestamp("2022-01-05")
    assert proxy.loc["2022-01-05"] == pytest.approx(0.01)
    result = compare.compare_raw(raw)
    expected_proxy = pd.Series([0.0, 0.0, 0.01],
                               index=pd.to_datetime(dates[1:4]))
    expected_official = pd.Series([0.0, 0.01, 103 / 101 - 1],
                                  index=pd.to_datetime(dates[1:4]))
    diff = expected_proxy - expected_official
    assert result["window"] == {"start": "2012-01-01", "end": "2022-12-31"}
    assert result["overall"]["days"] == 3
    assert result["overall"]["annual_gap"] == pytest.approx(diff.mean() * 252)
    assert result["overall"]["tracking_error"] == pytest.approx(diff.std(ddof=0) * np.sqrt(252))
    assert result["overall"]["correlation"] == pytest.approx(expected_proxy.corr(expected_official))
    assert result["by_year"] == {"2022": result["overall"]}
    three = compare.compare_three_raw(raw)
    assert three["common_days"] == 3
    assert three["official_weights_vs_D31_proxy"]["annual_gap"] == pytest.approx(0)


def test_comparison_needs_overlapping_dev_returns():
    proxy = pd.Series([0.2], index=pd.to_datetime(["2023-01-03"]))
    official = pd.Series([100, 110], index=pd.to_datetime(["2022-01-03", "2022-01-04"]))
    with pytest.raises(ValueError, match="no common dev"):
        compare.compare_returns(proxy, official)


def test_month_end_weights_apply_next_trading_day_and_drift(tmp_path):
    raw = tmp_path / "raw"
    stock_dir = raw / "baostock" / "daily"
    stock_dir.mkdir(parents=True)
    dates = ["2021-12-31", "2022-01-03", "2022-01-04", "2022-01-05"]
    pd.DataFrame({"calendar_date": dates, "is_trading_day": 1}).to_parquet(
        raw / "baostock" / "trade_dates.parquet", index=False)
    for code, close in (("sh.600000", [100, 110, 121, 133.1]),
                        ("sz.000001", [100, 100, 90, 81])):
        pd.DataFrame({"date": dates, "close": close, "volume": 100, "turn": 10}).to_parquet(
            stock_dir / f"{code}.parquet", index=False)
    weight_dir = raw / "dolthub" / "index_weights"
    weight_dir.mkdir(parents=True)
    pd.DataFrame([
        {"trade_date": "2021-12-31", "code": "sh.600000", "weight": 60},
        {"trade_date": "2021-12-31", "code": "sz.000001", "weight": 40},
        {"trade_date": "2022-01-04", "code": "sh.600000", "weight": 25},
        {"trade_date": "2022-01-04", "code": "sz.000001", "weight": 75},
    ]).to_parquet(weight_dir / "000905.SH.parquet", index=False)
    result = compare.official_weight_returns_from_raw(raw)
    assert result.loc["2022-01-03"] == pytest.approx(0.06)
    assert result.loc["2022-01-04"] == pytest.approx((66 / 106) * 0.1 + (40 / 106) * -0.1)
    assert result.loc["2022-01-05"] == pytest.approx(0.25 * 0.1 + 0.75 * -0.1)
