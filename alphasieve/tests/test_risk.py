import numpy as np
import pandas as pd
import pytest

from alphasieve.strategy.risk import (
    SPEC_HASH,
    RiskCovariance,
    RiskSnapshot,
    _ew_variance,
    exante_te,
    fit_factor_returns,
    industry_asof,
    standardize,
    validate_bias,
)


def test_standardisation_uses_population_and_current_universe():
    x = np.array([1.0, 2.0, 3.0, 100.0])
    z, info, missing = standardize(x, np.array([1, 1, 1, 0], bool), size=True)
    assert info["mean"] == 2
    assert info["std"] == pytest.approx(np.sqrt(2 / 3))
    assert z[0] == pytest.approx(-np.sqrt(3 / 2))
    assert z[3] > 100  # held outside the fit universe, same transform
    assert not missing.any()
    z, info, _ = standardize(np.ones(120), np.ones(120, bool))
    assert not info["available"]
    assert np.all(z == 0)


def test_industry_requires_both_dates_and_next_trading_day():
    cal = pd.date_range("2022-01-03", periods=5, freq="B")
    rows = pd.DataFrame(
        [
            {
                "code": "a",
                "industry": "A",
                "effective_date": "2022-01-03",
                "known_at": "2022-01-04",
                "source_version": "x",
            },
            {
                "code": "a",
                "industry": "B",
                "effective_date": "2022-01-05",
                "known_at": "2022-01-10",
                "source_version": "x",
            },
        ]
    )
    assert industry_asof(rows, "2022-01-04", ["a", "b"], cal).tolist() == ["unknown", "unknown"]
    assert industry_asof(rows, "2022-01-05", ["a", "b"], cal).tolist() == ["A", "unknown"]
    assert industry_asof(rows, "2022-01-07", ["a", "b"], cal).tolist() == ["A", "unknown"]


def _snapshot(n=120):
    codes = tuple(str(i) for i in range(n))
    groups = np.array(["A"] * (n // 2) + ["B"] * (n - n // 2))
    z = np.linspace(-1, 1, n)
    X = np.column_stack([np.ones(n), groups == "A", groups == "B", z, z**2, z**3, z**4, z**5, z**6]).astype(float)
    b = np.full(n, 1 / n)
    return RiskSnapshot(
        pd.Timestamp("2022-01-03"),
        codes,
        (
            "market",
            "industry:A",
            "industry:B",
            "size_z",
            "beta_z",
            "momentum_z",
            "volatility_z",
            "liquidity_z",
            "value_z",
        ),
        X,
        b,
        np.ones(n, bool),
        np.ones(n),
        groups,
        {k: {"available": True} for k in ("size_z", "beta_z", "momentum_z", "volatility_z", "liquidity_z", "value_z")},
        {},
        {},
        "provisional",
        (),
        z,
        z,
        "sw1",
    )


def test_constrained_regression_and_cash_market_term():
    snap = _snapshot()
    y = 0.01 + 0.002 * (snap.X[:, 1] - snap.X[:, 2])
    result = fit_factor_returns(snap, y)
    assert result.valid
    assert result.values[0] == pytest.approx(0.01, abs=1e-5)
    assert (result.values[1] + result.values[2]) / 2 == pytest.approx(0, abs=1e-10)
    F = np.zeros((9, 9))
    F[0, 0] = 1e-4
    cov = RiskCovariance(
        snap.factors, F, np.full(120, 1e-8), np.zeros(120, bool), True, "valid", 252, {"A": 252, "B": 252}, ()
    )
    zero = exante_te(snap, cov, snap.benchmark)
    assert zero["te"] == 0
    cash = exante_te(snap, cov, np.zeros(120))
    assert cash["te"] == pytest.approx(np.sqrt(252 * 1e-4 + 252 * 1e-8 / 120))
    assert cash["quality"] == "provisional"


def test_ew_uses_actual_trading_positions_and_unbiased_denominator():
    x = np.array([[1.0], [3.0]])
    q = np.array([0.5, 1.0])
    q /= q.sum()
    expected = np.sum(q * (np.array([1.0, 3.0]) - np.dot(q, [1.0, 3.0])) ** 2) / (1 - np.sum(q * q))
    assert _ew_variance(x, np.array([0, 60]), 60, 60)[0, 0] == pytest.approx(expected)


def test_bias_coverage_and_direction():
    dates = pd.bdate_range("2016-01-01", "2022-12-30")
    n = len(dates)
    rng = np.random.default_rng(4)
    sigma = 0.01
    f = pd.DataFrame(
        {
            "no_execution": True,
            "quality": "valid",
            "daily_variance": sigma**2,
            "gross_excess_next": rng.normal(0, 1.5 * sigma, n),
        },
        index=dates,
    )
    result = validate_bias(f)
    assert result["periods"]["all"]["coverage"] == 1
    assert result["periods"]["all"]["bias_B"] > 1.2
    assert result["status"] == "failed_calibration"
    assert len(SPEC_HASH) == 64


def test_future_panel_and_industry_changes_do_not_change_close_snapshot():
    from alphasieve.data.access import Panel
    from alphasieve.strategy.risk import ExposureEngine

    dates = pd.bdate_range("2020-01-01", periods=260)
    codes = [f"s{i:03d}" for i in range(120)]
    grid = pd.MultiIndex.from_product([dates, codes], names=["date", "code"]).to_frame(index=False)
    t = np.repeat(np.arange(260), 120)
    c = np.tile(np.arange(120), 260)
    grid["close"] = 100 * np.exp(0.0002 * t + 0.01 * c / 120)
    grid["ret_1d"] = 0.001 * np.sin(t / 7 + c / 17)
    grid["circ_mv"] = 1e9 + c * 1e7 + t * 1e5
    grid["amount"] = 1e7 + c * 1e5 + t * 100
    grid["pb_mrq"] = 1 + c / 200 + t / 1e5
    grid["sw1"] = "unknown"
    meta = {
        "tier": "dev",
        "signature": "synthetic",
        "window": {"start": str(dates[0].date()), "end": str(dates[-1].date())},
    }
    hist = pd.DataFrame(
        {
            "code": codes,
            "industry": ["A"] * 60 + ["B"] * 60,
            "effective_date": ["2019-12-01"] * 120,
            "known_at": ["2019-12-02"] * 120,
            "source_version": ["synthetic"] * 120,
        }
    )
    market = 0.001 * np.cos(np.arange(260) / 9)
    mask = np.ones((260, 120), dtype=bool)
    before = ExposureEngine(Panel(grid, meta), mask, market, industry_history=hist).snapshot(255)
    changed = grid.copy()
    changed.loc[changed.date > dates[255], ["close", "ret_1d", "amount", "pb_mrq"]] *= 8
    late = pd.DataFrame(
        {
            "code": ["s000"],
            "industry": ["C"],
            "effective_date": [dates[255]],
            "known_at": [dates[256]],
            "source_version": ["synthetic"],
        }
    )
    after = ExposureEngine(Panel(changed, meta), mask, market, industry_history=pd.concat([hist, late])).snapshot(255)
    np.testing.assert_array_equal(before.X, after.X)
    np.testing.assert_array_equal(before.benchmark, after.benchmark)


def test_covariance_psd_specific_shrink_and_industry_prior():
    from alphasieve.strategy.risk import FactorReturn, estimate_covariance

    snap = _snapshot()
    rng = np.random.default_rng(3)
    history = []
    for t in range(252):
        residual = rng.normal(size=120) * np.r_[np.full(60, 0.01), np.full(60, 0.02)]
        residual[0] = np.nan
        history.append(
            FactorReturn(
                pd.Timestamp("2021-01-01") + pd.Timedelta(days=t),
                snap.factors,
                rng.normal(0, 0.001, 9),
                residual,
                True,
                ("A", "B"),
            )
        )
    cov = estimate_covariance(history, snap)
    assert cov.valid and cov.days == 252
    assert np.linalg.eigvalsh(cov.F).min() >= 0
    assert cov.prior[0]
    assert cov.D[0] == pytest.approx(np.median(cov.D[1:60]), rel=0.25)
    assert cov.D[60:].mean() > cov.D[1:60].mean()
