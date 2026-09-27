import numpy as np
import pandas as pd
import pytest

from alphasieve.data.access import load_panel
from alphasieve.errors import AlphaSieveError
from alphasieve.factors.dsl import compile_expression, evaluate
from alphasieve.search_space import load_search_space
from alphasieve.state import connect
from alphasieve.strategy import execution, model, portfolio
from alphasieve.strategy.run import backtest


@pytest.fixture
def dev(panel_settings):
    return load_panel(panel_settings, "dev")


def factors(panel, settings):
    space = load_search_space(settings)
    exprs = {"rev": "neg(ts_sum(excess_ret_1d, 3))", "vol": "neg(ts_std(ret_1d, 20))", "mom": "ts_sum(ret_1d, 20)"}
    return {k: evaluate(compile_expression(e, space), panel) for k, e in exprs.items()}


def test_walk_forward_has_no_label_leakage(dev, panel_settings):
    feats = factors(dev, panel_settings)
    scores, info = model.walk_forward_scores(feats, dev, 5, "ridge", "monthly", train_years=5, warmup_years=1)
    assert info["fits"] > 0 and np.isfinite(info["ic"]["ic_mean"])
    retrains = model.retrain_dates(dev.dates, dev.window_mask().to_numpy(), "monthly", 1)
    k = len(retrains) // 2
    start, stop = retrains[k], retrains[k + 1]
    cut = dev.dates[start - 6]
    label = dev.wide("label_5d").copy()
    label.loc[label.index >= cut] = np.random.default_rng(0).normal(size=label.loc[label.index >= cut].shape)
    dev.set_wide("label_5d", label)
    again, _ = model.walk_forward_scores(feats, dev, 5, "ridge", "monthly", train_years=5, warmup_years=1)
    pd.testing.assert_frame_equal(scores.iloc[start:stop], again.iloc[start:stop])


def test_model_refuses_holdout(panel_settings):
    holdout = load_panel(panel_settings, "holdout", role="system")
    with pytest.raises(AlphaSieveError):
        model.walk_forward_scores({"x": holdout.wide("close")}, holdout, 5)


def test_lgbm_scores(dev, panel_settings):
    pytest.importorskip("lightgbm")
    _, info = model.walk_forward_scores(factors(dev, panel_settings), dev, 5, "lgbm", "yearly", warmup_years=1,
                                        n_jobs=2)
    assert info["fits"] >= 1 and sum(info["feature_importance"].values()) == pytest.approx(1.0)


def test_portfolio_constraints_and_execution(dev, panel_settings):
    scores, _ = model.walk_forward_scores(factors(dev, panel_settings), dev, 5, "ridge", "monthly", warmup_years=1)
    weights = portfolio.build_weights(scores, dev, rebalance_every=5, industry_dev=0.05, name_cap=0.05,
                                      turnover_cap=0.3)
    assert not weights.empty
    np.testing.assert_allclose(weights.sum(axis=1), 1.0, atol=1e-9)
    diag = portfolio.weight_diagnostics(weights, dev)
    assert diag["max_industry_deviation"] <= 0.05 + 1e-6
    assert diag["one_way_turnover_max"] <= 0.3 + 1e-6
    names = (weights > 0).sum(axis=1)
    assert (weights.max(axis=1)[names >= 20] <= 0.05 + 1e-9).all()
    sim = execution.simulate(weights, dev, {"commission": 0.00025, "stamp_duty_sell": 0.0005, "slippage": 0.0005})
    assert sim["days"] > 100 and sim["annual_cost"] > 0 and np.isfinite(sim["information_ratio"])
    blocked = dev.mask("tradable_buy").copy()
    first_exec = dev.dates.get_loc(weights.index[0]) + 1
    blocked.iloc[first_exec] = False
    dev.set_wide("tradable_buy", blocked.astype(float))
    none_bought = execution.simulate(weights.iloc[:1], dev)
    assert none_bought["invested_mean"] == pytest.approx(0.0, abs=1e-12)


def test_backtest_end_to_end(panel_settings):
    conn = connect(panel_settings.state_db)
    out = backtest(panel_settings, conn, horizon=5, seed_library=True, industry_dev=0.05, name_cap=0.05,
                   warmup_years=1)
    assert out["model"]["fits"] > 0 and out["portfolio"]["rebalances"] > 0
    assert out["execution"]["days"] > 0 and out["artifact_id"] and out["series"]["nav"]
    assert (panel_settings.store_root / "models" / out["run_id"] / "scores.parquet").exists()
