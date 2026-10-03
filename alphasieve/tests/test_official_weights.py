import numpy as np
import pandas as pd
import pytest

from alphasieve.data.access import Panel
from alphasieve.errors import AlphaSieveError
from alphasieve.strategy.official_weights import daily_returns, daily_weights


def _panel():
    dates = pd.bdate_range("2022-01-28", periods=4)
    prices = [(10, 10), (20, 10), (20, 20), (20, 40)]
    rows = [{"date": date, "code": code, "close": prices[t][c], "industry": "A"}
            for t, date in enumerate(dates) for c, code in enumerate(("sh.600000", "sz.000001"))]
    return Panel(pd.DataFrame(rows), {"tier": "dev", "signature": "synthetic",
                                      "window": {"start": str(dates[0].date()), "end": str(dates[-1].date())}})


def test_official_weights_next_day_and_price_drift():
    panel = _panel()
    weights = pd.DataFrame({"trade_date": ["2022-01-28", "2022-01-28", "2022-02-01", "2022-02-01"],
                            "stock_code": ["600000.SH", "000001.SZ"] * 2,
                            "weight": [50, 50, 0, 100]})
    returns = daily_returns(panel, weights)
    np.testing.assert_allclose(returns[1:], [0.5, 1 / 3, 1.0])
    assert np.isnan(returns[0])
    np.testing.assert_allclose(daily_weights(panel, weights),
                               [[0.5, 0.5], [2 / 3, 1 / 3], [0, 1], [0, 1]])


def test_official_weights_reject_missing_constituents():
    weights = pd.DataFrame({"trade_date": ["2022-01-28", "2022-01-28"],
                            "stock_code": ["600000.SH", "600001.SH"], "weight": [50, 50]})
    with pytest.raises(AlphaSieveError, match="missing from panel"):
        daily_returns(_panel(), weights)
