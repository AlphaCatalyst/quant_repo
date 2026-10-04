import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT = Path(__file__).resolve().parents[1] / "tools/sw_industry_sensitivity.py"
_SPEC = importlib.util.spec_from_file_location("sw_industry_sensitivity", _SCRIPT)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_official_at_rebalances = _MODULE._official_at_rebalances


def test_official_drift_ignores_zero_weight_names_without_prices(tmp_path):
    path = tmp_path / "weights.parquet"
    pd.DataFrame({
        "trade_date": ["2020-01-02", "2020-01-02"],
        "stock_code": ["600001.SH", "600002.SH"],
        "weight": [50.0, 50.0],
    }).to_parquet(path, index=False)
    panel = pd.DataFrame({
        "date": pd.to_datetime(["2020-01-02"] * 3 + ["2020-01-03"] * 3),
        "code": ["sh.600001", "sh.600002", "sh.600003"] * 2,
        "close": [1.0, 1.0, np.nan, 2.0, 1.0, np.nan],
    })
    dates = pd.DatetimeIndex(["2020-01-02", "2020-01-03"])
    codes = pd.Index(["sh.600001", "sh.600002", "sh.600003"])

    actual = _official_at_rebalances(path, panel, dates, codes)

    np.testing.assert_allclose(actual.loc["2020-01-02"], [0.5, 0.5, 0.0])
    np.testing.assert_allclose(actual.loc["2020-01-03"], [2 / 3, 1 / 3, 0.0])
