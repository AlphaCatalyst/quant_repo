import numpy as np
import pandas as pd
import pytest
from scipy.stats import spearmanr

from alphasieve.config import get_settings
from alphasieve.errors import AlphaSieveError
from alphasieve.evaluation import metrics as M
from alphasieve.factors import ops
from alphasieve.factors.dsl import DSLError, compile_expression
from alphasieve.gates import can_transition, transition
from alphasieve.search_space import load_search_space
from alphasieve.state import connect


@pytest.fixture
def space(settings):
    return load_search_space(settings)


def issues(expr, space):
    with pytest.raises(DSLError) as exc:
        compile_expression(expr, space)
    return {i.name for i in exc.value.issues}


def test_canonical_form_dedupes_commutative_and_constants(space):
    a = compile_expression("ts_mean(close, 5) + ts_std(volume, 20)", space)
    b = compile_expression("ts_std(volume, 20) + ts_mean(close, 5)", space)
    assert a.candidate_hash == b.candidate_hash
    c = compile_expression("close * (2 + 3)", space)
    d = compile_expression("5 * close", space)
    assert c.canonical == d.canonical == "mul(5,close)"
    e = compile_expression("-ts_sum(ret_1d, 5)", space)
    assert compile_expression(e.canonical, space).candidate_hash == e.candidate_hash


@pytest.mark.parametrize("expr,expected", [
    ("ts_mean(label_5d, 5)", "unknown_terminal"),
    ("future_close", "unknown_terminal"),
    ("shift(close, 1)", "unknown_function"),
    ("ts_delay(close, -1)", "window_not_allowed"),
    ("ts_mean(close, 7)", "window_not_allowed"),
    ("ts_mean(close, w=5)", "syntax"),
    ("close.shift(1)", "syntax"),
    ("(lambda: close)()", "syntax"),
    ("close[0]", "syntax"),
    ("1 + 2", "constant_expression"),
    ("ts_mean(1, 5)", "type_error"),
    ("ts_mean(close)", "arity"),
    ("signed_power(close, 10)", "type_error"),
])
def test_l0_rejections(space, expr, expected):
    assert expected in issues(expr, space)


def test_complexity_limits(space):
    deep = "close"
    for _ in range(9):
        deep = f"abs({deep})"
    assert "complexity_depth" in issues(deep, space)
    wide = " + ".join(f"ts_mean(close, {w})" for w in (3, 5, 10, 20, 40, 60, 120, 250, 3, 5, 10))
    assert "complexity_nodes" in issues(wide, space)
    many = "open + high + low + close + volume"
    assert "complexity_terminals" in issues(many, space)


def test_cell_inference(space):
    compiled = compile_expression("ts_mean(turnover_rate, 60)", space)
    check = space.check_cell({"domain": "price", "form": "reversal", "scale": "short"}, compiled.terminals,
                             compiled.max_window)
    assert check["inferred_domains"] == ["turnover_liquidity"] and check["inferred_scale"] == "medium"
    assert len(check["warnings"]) == 2


@pytest.fixture
def frame():
    rng = np.random.default_rng(0)
    return pd.DataFrame(rng.normal(size=(60, 8)), index=pd.bdate_range("2020-01-01", periods=60),
                        columns=[f"c{i}" for i in range(8)])


def test_ts_ops_match_numpy(frame):
    x = frame.to_numpy()
    w = 5
    t = 30
    window = x[t - w + 1:t + 1]
    assert np.allclose(ops.ts_mean(frame, w).iloc[t], window.mean(axis=0))
    assert np.allclose(ops.ts_std(frame, w).iloc[t], window.std(axis=0, ddof=1))
    rank = [(np.sum(window[:, j] < window[-1, j]) + 1) / w for j in range(x.shape[1])]
    assert np.allclose(ops.ts_rank(frame, w).iloc[t], rank)
    weights = np.arange(1, w + 1)
    assert np.allclose(ops.ts_decay_linear(frame, w).iloc[t], (window * weights[:, None]).sum(0) / weights.sum())
    corr = [np.corrcoef(window[:, 0], window[:, j])[0, 1] for j in range(x.shape[1])]
    shifted = pd.DataFrame(np.repeat(frame[["c0"]].to_numpy(), 8, axis=1), index=frame.index, columns=frame.columns)
    assert np.allclose(ops.ts_corr(shifted, frame, w).iloc[t], corr)
    assert np.isnan(ops.ts_mean(frame, w).iloc[w - 2]).all()
    assert np.allclose(ops.ts_delay(frame, 3).iloc[t], x[t - 3])


def test_cs_and_group_ops(frame):
    row = frame.iloc[10].to_numpy()
    assert np.allclose(ops.cs_rank(frame).iloc[10], (row.argsort().argsort() + 1) / len(row))
    assert np.allclose(ops.cs_zscore(frame).iloc[10], (row - row.mean()) / row.std(ddof=1))
    groups = pd.Series(["a", "a", "b", "b", "b", "c", "c", "c"], index=frame.columns)
    demeaned = ops.group_demean(frame, groups).iloc[10]
    assert np.allclose(demeaned.groupby(groups).sum(), 0)
    size = frame.abs() + 1
    big = pd.concat([frame] * 4, axis=1)
    big.columns = [f"c{i}" for i in range(32)]
    big_groups = pd.Series(["a", "b", "c", "d"] * 8, index=big.columns)
    big_size = pd.concat([size] * 4, axis=1)
    big_size.columns = big.columns
    residual = ops.cs_neutralize(big, big_groups, big_size).iloc[10].to_numpy()
    dummies = pd.get_dummies(big_groups).to_numpy(dtype=float)
    design = np.column_stack([dummies, big_size.iloc[10].to_numpy()])
    assert np.allclose(design.T @ residual, 0, atol=1e-8)


def test_rank_corr_matches_scipy(frame):
    rng = np.random.default_rng(1)
    other = frame + rng.normal(scale=2, size=frame.shape)
    valid = pd.DataFrame(True, index=frame.index, columns=frame.columns)
    ours = M.rank_corr_series(frame, other, valid, min_names=5)
    for t in (0, 20, 59):
        assert ours.iloc[t] == pytest.approx(spearmanr(frame.iloc[t], other.iloc[t]).statistic)


def test_quantile_returns_hand_check():
    idx = pd.bdate_range("2020-01-01", periods=1)
    factor = pd.DataFrame([[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]], index=idx, columns=list("abcdefghij"), dtype=float)
    label = factor / 100
    valid = pd.DataFrame(True, index=idx, columns=factor.columns)
    q = M.quantile_returns(factor, label, valid)
    assert q["q1"] == pytest.approx(0.015) and q["q5"] == pytest.approx(0.095)
    assert q["long_short"] == pytest.approx(0.08)


def test_illegal_state_transition(settings):
    conn = connect(get_settings().state_db)
    conn.execute("INSERT INTO factor_specs VALUES ('F-1', 1, 'h', 'n', '{}', 'x', 'draft', 'agent', 'now')")
    assert can_transition("draft", "validating") and not can_transition("evaluated", "shadow_promoted")
    with pytest.raises(AlphaSieveError) as exc:
        transition(conn, get_settings(), "F-1", 1, "robust_passed")
    assert exc.value.code == "CONFLICT"
