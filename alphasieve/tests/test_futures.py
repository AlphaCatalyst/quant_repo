from unittest import mock

import numpy as np
import pandas as pd
import pytest

from alphasieve.data import futures as fu

DATES = pd.bdate_range("2019-01-02", "2019-12-31")


def _spot(seed=0) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(5000 * np.cumprod(1 + rng.normal(0, 0.01, len(DATES))), index=DATES)


def _write_market(settings, spot: pd.Series, discount=0.01, ic0_lag=False, first_contract="1902") -> dict:
    """Contracts that trade at ``spot * (1 - discount * dte / 20)`` and converge on their settlement day."""
    cal = fu._Calendar(DATES)
    root = fu.futures_root(settings) / "IC"
    root.mkdir(parents=True, exist_ok=True)
    expiry = {}
    for month in pd.period_range(f"20{first_contract[:2]}-{first_contract[2:]}", "2020-03", freq="M"):
        code = f"IC{month.year % 100:02d}{month.month:02d}"
        settle = cal.settlement(month)
        expiry[code] = settle
        live = DATES[(DATES >= settle - pd.Timedelta(days=120)) & (DATES <= settle)]
        if live.empty:
            continue
        dte = np.array([cal.pos(settle) - cal.pos(d) for d in live])
        px = spot.loc[live].to_numpy() * (1 - discount * dte / 20)
        pd.DataFrame({"date": live.strftime("%Y-%m-%d"), "open": px, "high": px, "low": px, "close": px,
                      "volume": 1.0, "oi": 1.0}).to_parquet(root / f"{code}.parquet", index=False)
    ser = pd.Series(expiry).sort_values()
    rows = []
    for d in DATES:
        live = ser[ser > d] if not ic0_lag else ser[ser >= d]
        code = live.index[0]
        rows.append(pd.read_parquet(root / f"{code}.parquet").set_index("date").loc[d.strftime("%Y-%m-%d")])
    ic0 = pd.DataFrame(rows).reset_index(names="date")
    ic0.to_parquet(root / "IC0.parquet", index=False)
    return expiry


def test_exact_leg_holds_front_contract_without_roll_jumps(settings):
    spot = _spot()
    expiry = _write_market(settings, spot)
    leg = fu.hedge_leg(settings, "IC", spot)
    exact = leg[leg["source"] == "contract"]
    assert len(exact) > 200
    spot_ret = spot.pct_change()
    # with a linear discount the only gap to the index is the daily convergence, never a roll jump
    gap = (leg["fut_ret"] - spot_ret).loc[exact.index[1:]]
    assert gap.abs().max() < 0.002
    settles = [e for e in expiry.values() if exact.index[0] <= e <= DATES[-1]]
    assert int(exact["roll"].sum()) == len(settles)
    assert (exact["days_to_expiry"] >= 1).all()
    assert (exact["basis"] < 0).all()


def test_continuous_splice_rolls_are_inferred(settings):
    spot = _spot(1)
    _write_market(settings, spot, ic0_lag=True)
    exact = fu.hedge_leg(settings, "IC", spot)
    with mock.patch.object(fu, "load_contracts", lambda *a: (pd.DataFrame(), pd.DataFrame())):
        inferred = fu.hedge_leg(settings, "IC", spot)
    assert set(inferred["source"]) == {"continuous"}
    both = exact.index[(exact["source"] == "contract")][1:]
    assert (inferred.loc[both, "roll"] == exact.loc[both, "roll"]).all()
    assert (inferred.loc[both, "fut_ret"] - exact.loc[both, "fut_ret"]).abs().mean() < 5e-4


def test_leg_reads_nothing_past_the_index_dates(settings):
    spot = _spot(2)
    _write_market(settings, spot)
    cut = spot.loc[:"2019-06-30"]
    full = fu.hedge_leg(settings, "IC", spot)
    part = fu.hedge_leg(settings, "IC", cut)
    assert part.index.max() <= pd.Timestamp("2019-06-30")
    common = part.index[part["source"] == "contract"]
    pd.testing.assert_series_equal(part.loc[common, "fut_ret"], full.loc[common, "fut_ret"])
    pd.testing.assert_series_equal(part.loc[common, "days_to_expiry"], full.loc[common, "days_to_expiry"])


def test_fragmentary_early_contracts_fall_back_to_the_splice(settings):
    spot = _spot(3)
    _write_market(settings, spot)
    root = fu.futures_root(settings) / "IC"
    frag = pd.read_parquet(root / "IC1904.parquet")
    frag.iloc[::3].to_parquet(root / "IC1904.parquet", index=False)
    leg = fu.hedge_leg(settings, "IC", spot)
    first_exact = leg.index[leg["source"] == "contract"][0]
    assert first_exact >= pd.Timestamp("2019-04-19")
    assert leg.loc[:first_exact, "source"].iloc[:-1].eq("continuous").all()


@pytest.mark.parametrize("covered_days", [0, 400])
def test_futures_hedged_judges_only_covered_days(covered_days):
    from types import SimpleNamespace

    from alphasieve.training.mandates import futures_hedged

    dates = pd.bdate_range("2020-01-01", periods=500)
    codes = ["a", "b"]
    rng = np.random.default_rng(0)
    bench = pd.Series(rng.normal(0, 0.01, len(dates)), index=dates)
    daily = pd.DataFrame({"ret": bench + 0.0002, "bench": bench})
    weights = pd.DataFrame(0.5, index=dates, columns=codes)
    panel = SimpleNamespace(dates=dates, codes=codes)
    leg = pd.DataFrame({"fut_ret": np.nan, "roll": False, "source": "none"}, index=dates)
    if covered_days:
        tail = dates[-covered_days:]
        leg.loc[tail, "fut_ret"] = bench.loc[tail] + 0.0004
        leg.loc[tail, "source"] = "contract"
    cfg = SimpleNamespace(margin=0.15, cash_buffer=0.25, hedge_ratios=[1.0, 0.5])
    out = futures_hedged(panel, {"_daily": daily, "weights": weights}, np.ones((len(dates), 2)), cfg,
                         bench.to_numpy(), leg)
    if covered_days:
        assert out["basis_included"] and out["segments"]["contract_exact"]["days"] == covered_days
        assert out["segments"]["contract_exact"]["annual_return"] < out["segments"]["no_futures_proxy"]["annual_return"]
        assert out["acceptance"]["checks"]
    else:
        assert not out["basis_included"] and not out["acceptance"]["passed"]
        assert "blocked" in out["acceptance"]
