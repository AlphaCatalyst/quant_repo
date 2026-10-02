import math

import pytest

from alphasieve.fresh.statistics import benjamini_hochberg, hac, verdict


def test_hac_keeps_actual_trading_day_distance_across_gaps():
    gap = hac([(0, 1.0), (1, None), (2, 2.0), (3, None), (4, 3.0)], horizon=2)
    compressed = hac([(0, 1.0), (1, 2.0), (2, 3.0)], horizon=2)
    assert gap["n"] == compressed["n"] == 3
    assert gap["lag"] == compressed["lag"] == 2
    assert gap["se"] != compressed["se"]
    assert gap["mean"] == 2.0
    with pytest.raises(ValueError, match="duplicate"):
        hac([(0, None), (0, 1.0)])


def test_hac_overlap_lag_and_negative_tail():
    result = hac([(day, -0.01 - 0.0001 * day) for day in range(120)], horizon=20)
    assert result["lag"] == 19
    assert result["ci_high"] < 0
    assert result["p_one_sided"] > 0.95


def test_bh_keeps_missing_member_and_step_up_family():
    rows = benjamini_hochberg({"a": 0.02, "b": 0.04, "missing": None}, q=0.10)
    assert rows["a"]["passed"]
    assert rows["b"]["passed"]
    assert rows["missing"]["p"] == 1.0
    assert not rows["missing"]["passed"]
    assert rows["a"]["threshold"] == pytest.approx(0.1 / 3)


@pytest.mark.parametrize("kind,required", [("factor", 60), ("strategy", 120)])
def test_verdict_minimum_days_coverage_and_endpoint(kind, required):
    series = [(i, 0.01 + i * 0.00001) for i in range(required)]
    extras = {"marginal_ic": 0.01} if kind == "factor" else {"excess_drawdown": -0.01, "tracking_error": 0.05}
    base = dict(kind=kind, mode="validation", horizon=1, bh_pass=True, **extras)
    observing = verdict(**base, series=series, eligible_days=required, endpoint_reached=False)
    assert observing["verdict"] is None
    supported = verdict(**base, series=series, eligible_days=required, endpoint_reached=True)
    assert supported["verdict"] == "fresh_supported"
    assert supported["promotion_eligible"]
    short = verdict(**base, series=series[:-1], eligible_days=required, endpoint_reached=True)
    assert short["verdict"] == "fresh_inconclusive"
    low_coverage = verdict(**base, series=series, eligible_days=math.ceil(required / .94), endpoint_reached=True)
    assert low_coverage["verdict"] == "fresh_inconclusive"
    shadow = verdict(**{**base, "mode": "diagnostic_shadow"}, series=series,
                     eligible_days=required, endpoint_reached=True)
    assert shadow["verdict"] is None
    assert shadow["status"] == "shadow_complete"
    assert not shadow["promotion_eligible"]


def test_verdict_three_states_and_hard_risk_failure():
    base = dict(kind="strategy", mode="validation", horizon=1, eligible_days=120,
                endpoint_reached=True, bh_pass=True, excess_drawdown=-0.01, tracking_error=0.05)
    assert verdict(**base, series=[(i, 0.001) for i in range(120)])["verdict"] == "fresh_supported"
    negative = verdict(**base, series=[(i, -0.001 - i * 0.000001) for i in range(120)])
    assert negative["verdict"] == "fresh_failed"
    assert "significant_negative" in negative["reasons"]
    inconclusive = verdict(**{**base, "bh_pass": False}, series=[(i, 0.001) for i in range(120)])
    assert inconclusive["verdict"] == "fresh_inconclusive"
    risk = verdict(**{**base, "tracking_error": 0.07}, series=[(i, 0.001) for i in range(120)])
    assert risk["verdict"] == "fresh_failed"
