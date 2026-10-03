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


def test_comparison_needs_overlapping_dev_returns():
    proxy = pd.Series([0.2], index=pd.to_datetime(["2023-01-03"]))
    official = pd.Series([100, 110], index=pd.to_datetime(["2022-01-03", "2022-01-04"]))
    with pytest.raises(ValueError, match="no common dev"):
        compare.compare_returns(proxy, official)
