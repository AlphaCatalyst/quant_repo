"""S-1 equivalence: the vectorised evaluation kernel must reproduce the v1 pandas implementation
(tests/reference/*_v1.py, frozen copies) to within 1e-6 on every metric."""

import math

import numpy as np
import pandas as pd
import pytest
from reference import marginal_v1, metrics_v1, tradable_v1

from alphasieve.config import load_config
from alphasieve.data.access import load_panel
from alphasieve.evaluation import core, fastops, marginal, metrics, tradable
from alphasieve.factors import library as lib
from alphasieve.factors.dsl import compile_expression, evaluate
from alphasieve.search_space import load_search_space
from alphasieve.state import connect

TOL = 1e-6


def assert_close(new, old, path="root"):
    if isinstance(old, dict):
        assert set(new) == set(old), f"{path}: keys differ {set(new) ^ set(old)}"
        for k in old:
            assert_close(new[k], old[k], f"{path}.{k}")
    elif isinstance(old, (list, tuple)):
        assert len(new) == len(old), path
        for i, (a, b) in enumerate(zip(new, old, strict=True)):
            assert_close(a, b, f"{path}[{i}]")
    elif isinstance(old, pd.Series):
        np.testing.assert_allclose(new.to_numpy(float), old.to_numpy(float), atol=TOL, rtol=TOL, equal_nan=True,
                                   err_msg=path)
    elif isinstance(old, float) and (math.isnan(old) or (isinstance(new, float) and math.isnan(new))):
        assert isinstance(new, float) and math.isnan(new) and math.isnan(old), f"{path}: {new} vs {old}"
    elif isinstance(old, (int, float)) and not isinstance(old, bool):
        assert new == pytest.approx(old, abs=TOL, rel=TOL), f"{path}: {new} vs {old}"
    else:
        assert new == old, f"{path}: {new!r} vs {old!r}"


def random_frames(seed=0, t=160, n=90):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=t)
    cols = [f"c{i:03d}" for i in range(n)]
    a = rng.normal(size=(t, n))
    a[:, :15] = np.round(a[:, :15], 1)
    a[rng.random((t, n)) < 0.2] = np.nan
    b = 0.3 * np.nan_to_num(a) + rng.normal(size=(t, n))
    b[rng.random((t, n)) < 0.1] = np.nan
    valid = rng.random((t, n)) < 0.85
    valid[5] = False
    valid[6, :10] = True
    valid[6, 10:] = False
    return (pd.DataFrame(a, idx, cols), pd.DataFrame(b, idx, cols), pd.DataFrame(valid, idx, cols))


def test_row_rank_matches_pandas():
    a, _, _ = random_frames()
    arr = a.to_numpy().copy()
    arr[3] = np.nan
    arr[4, :] = 1.0
    np.testing.assert_array_equal(fastops.row_rank(arr), pd.DataFrame(arr).rank(axis=1).to_numpy())
    np.testing.assert_allclose(fastops.row_rank(arr, pct=True), pd.DataFrame(arr).rank(axis=1, pct=True).to_numpy(),
                               equal_nan=True)


def test_cross_sectional_metrics_match_v1():
    a, b, valid = random_frames()
    assert_close(metrics.rank_corr_series(a, b, valid), metrics_v1.rank_corr_series(a, b, valid))
    assert_close(metrics.quantile_returns(a, b, valid), metrics_v1.quantile_returns(a, b, valid))
    assert_close(metrics.coverage(a, valid), metrics_v1.coverage(a, valid))
    assert_close(metrics.turnover_proxy(a, valid), metrics_v1.turnover_proxy(a, valid))
    library = {"x": b, "y": -a + 0.1 * b.fillna(0)}
    assert_close(metrics.library_correlations(a, library, valid), metrics_v1.library_correlations(a, library, valid))
    groups = pd.Series([f"g{i % 4}" for i in range(a.shape[1])], index=a.columns)
    size = b.abs() + 1
    assert_close(metrics.neutralized_ic(a, b, valid, groups, size),
                 metrics_v1.neutralized_ic(a, b, valid, groups, size))


@pytest.fixture
def dev(panel_settings):
    return load_panel(panel_settings, "dev")


def _inputs(dev, panel_settings, expression, direction=-1):
    compiled = compile_expression(expression, load_search_space(panel_settings))
    factor = evaluate(compiled, dev) * direction
    return factor, core.EvalInputs(dev, 5)


def test_marginal_and_tradable_match_v1(dev, panel_settings):
    factor, inp = _inputs(dev, panel_settings, "ts_sum(excess_ret_1d, 3)")
    other, _ = _inputs(dev, panel_settings, "ts_mean(turnover_rate, 20)")
    baseline = {"turnover": other, "mom": _inputs(dev, panel_settings, "ts_sum(ret_1d, 20)", 1)[0]}
    new = marginal.marginal_contribution(factor, baseline, inp.label, inp.valid, inp.window, 5)
    old = marginal_v1.marginal_contribution(factor, baseline, inp.label, inp.valid, inp.window, 5)
    assert_close(new, old)
    second, _ = _inputs(dev, panel_settings, "ts_std(ret_1d, 10)")
    for cand in (factor, second):
        cached = marginal.marginal_contribution(cand, baseline, inp.label, inp.valid, inp.window, 5, cache_key="k")
        assert_close(cached, marginal_v1.marginal_contribution(cand, baseline, inp.label, inp.valid, inp.window, 5))
    empty = marginal.marginal_contribution(factor, {}, inp.label, inp.valid, inp.window, 5)
    assert_close(empty, marginal_v1.marginal_contribution(factor, {}, inp.label, inp.valid, inp.window, 5))
    b2 = load_config(panel_settings, "costs")["b2"]
    args = (factor, inp.universe, dev.wide("open"), dev.mask("tradable_buy"), dev.mask("tradable_sell"), inp.window,
            b2["rebalance_every"], b2["top_fraction"], b2["one_way_cost"])
    assert_close(tradable.long_only_excess(*args), tradable_v1.long_only_excess(*args))


def test_full_l1_l2_metrics_match_v1(dev, panel_settings, monkeypatch):
    conn = connect(panel_settings.state_db)
    lib.seed_library(panel_settings, conn, dev)
    library = lib.library_frames(panel_settings, conn, dev)
    costs = load_config(panel_settings, "costs")
    factor, inp = _inputs(dev, panel_settings, "ts_sum(excess_ret_1d, 3)")

    def run():
        m = core.l1_metrics(factor, core.EvalInputs(dev, 5), library)
        m["l2"] = core.l2_metrics(factor, core.EvalInputs(dev, 5), library, costs, 4, m["_ic_series"])
        return core.public_metrics(m)

    new = run()
    monkeypatch.setattr(core, "M", metrics_v1)
    monkeypatch.setattr(core, "marginal_contribution",
                        lambda *args, cache_key=None, **kw: marginal_v1.marginal_contribution(*args, **kw))
    monkeypatch.setattr(core, "long_only_excess", tradable_v1.long_only_excess)
    old = run()
    assert_close(new, old)


def test_fast_wide_matches_unstack(dev):
    for field in ("close", "in_universe", "tradable_buy", "roe_avg"):
        if not dev.has(field):
            continue
        series = dev._indexed[field]
        if series.dtype == bool or str(series.dtype) == "boolean":
            series = series.astype(float)
        ref = series.unstack("code").reindex(index=dev.dates, columns=dev.codes)
        pd.testing.assert_frame_equal(dev.wide(field), ref.astype(float), check_names=False)


def test_float32_wide_tables_stay_close(panel_settings, monkeypatch):
    import alphasieve.data.access as access

    def metrics_with(dtype):
        monkeypatch.setattr(access, "WIDE_DTYPE", dtype)
        access._read.cache_clear()
        panel = load_panel(panel_settings, "dev")
        factor, inp = _inputs(panel, panel_settings, "ts_sum(excess_ret_1d, 3)")
        m = core.l1_metrics(factor, inp, {})
        return panel.wide("close"), m

    close64, m64 = metrics_with(np.float64)
    close32, m32 = metrics_with(np.float32)
    assert close32.dtypes.iloc[0] == np.float32 and close64.dtypes.iloc[0] == np.float64
    assert close32.to_numpy().nbytes == close64.to_numpy().nbytes // 2
    assert m32["ic_mean"] == pytest.approx(m64["ic_mean"], abs=1e-4)
    assert m32["icir"] == pytest.approx(m64["icir"], abs=1e-3)
