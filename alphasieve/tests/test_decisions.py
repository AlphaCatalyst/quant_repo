import numpy as np
import pytest

from alphasieve.decisions.bars import Bars
from alphasieve.decisions.p3 import holm
from alphasieve.decisions.simulate import (
    Costs,
    Rule,
    Window,
    certainty_equivalent,
    matched_random,
    simulate,
    target_weights,
)
from alphasieve.decisions.synthetic import AccountSpec, draw_accounts, pool_masks


def _window(close, open_=None, can_sell=None, capital=100_000.0, weights=(0.8, 0.2)):
    close = np.asarray(close, dtype=float)[None]  # [1, H+1, K]
    open_ = close if open_ is None else np.asarray(open_, dtype=float)[None]
    ok = np.ones_like(close, dtype=bool)
    sell = ok if can_sell is None else np.asarray(can_sell, dtype=bool)[None]
    k = close.shape[2]
    return Window(open_, close, open_, ok, sell, np.full_like(close, 0.02), np.ones((1, k), dtype=bool),
                  np.array([weights]), np.array([0]), capital)


def test_no_action_holds_initial_weights():
    win = _window([[10, 10], [12, 10], [12, 5]])
    result = simulate(win, None)
    assert result["wealth"][0] == pytest.approx(0.8 * 1.2 + 0.2 * 0.5)
    assert result["cost"][0] == 0
    assert result["max_drawdown"][0] == pytest.approx(1 - (0.96 + 0.1) / (0.96 + 0.2))


def test_band_rebalance_executes_next_open_in_lots_with_costs():
    costs = Costs()
    win = _window([[10, 10], [10, 10], [11, 10], [11, 10]])
    result = simulate(win, Rule("equal", None, 0.10, 1), costs)
    assert result["triggered"][0, 0]  # 80/20 against 50/50 at the first review close
    # Day 2 open at 11/10: nav = 8000*11 + 2000*10 = 108000; target 54000 each.
    sell = round((88000 - 54000) / 1100) * 1100
    sell_fee = max(5, sell * costs.commission) + sell * (costs.stamp_sell + costs.slippage)
    cash = sell - sell_fee
    want = 54000 - 20000
    need = want * (1 + costs.commission + costs.slippage) + costs.min_commission
    buy = np.floor(want * min(1, cash / need) / 1000) * 1000
    buy_fee = max(5, buy * costs.commission) + buy * costs.slippage
    expected = (88000 - sell) + (20000 + buy) + cash - buy - buy_fee
    assert result["wealth"][0] * 100_000 == pytest.approx(expected)
    assert result["cost"][0] * 100_000 == pytest.approx(sell_fee + buy_fee)


def test_limit_down_open_blocks_the_sell():
    can_sell = [[True, True], [True, True], [False, True], [True, True]]
    win = _window([[10, 10], [10, 10], [10, 10], [10, 10]], can_sell=can_sell)
    result = simulate(win, Rule("equal", None, 0.10, 1))
    # Day-2 sell blocked; buys are limited to the zero cash on hand, so nothing trades until the day-3 decision.
    assert result["triggered"][0, :2].all()
    assert result["cost"][0] > 0
    blocked = simulate(_window([[10, 10], [10, 10], [10, 10]], can_sell=can_sell[:3]), Rule("equal", None, 0.1, 1))
    assert blocked["cost"][0] == 0


def test_cap_sends_excess_to_cash_and_redistributes():
    mask = np.array([[True, True, False], [True, True, True]])
    w = target_weights(Rule("equal", 0.30, 0.1, 5), mask, np.full((2, 3), 0.02))
    assert w[0].tolist() == pytest.approx([0.3, 0.3, 0.0])
    vol = np.array([[0.01, 0.04, np.nan], [0.01, 0.02, 0.04]])
    w = target_weights(Rule("inverse_vol", 0.45, 0.1, 5), mask, vol)
    assert w[1, 0] == pytest.approx(0.45)
    assert w[1, 1] / w[1, 2] == pytest.approx(2.0)
    assert w[1].sum() == pytest.approx(1.0)


def test_matched_random_keeps_counts():
    triggered = np.array([[True, False, False, True], [False, False, False, False], [True, True, True, True]])
    forced = matched_random(triggered, np.random.default_rng(0))
    assert forced.sum(axis=1).tolist() == [2, 0, 4]


def test_certainty_equivalent_and_holm():
    assert certainty_equivalent(np.array([1.0, 1.0]), 4) == pytest.approx(1.0)
    assert certainty_equivalent(np.array([0.5, 1.5]), 2) == pytest.approx(0.75)
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])


def test_pools_and_draws_use_point_in_time_eligibility():
    t, n = 400, 12
    rng = np.random.default_rng(0)
    close = np.cumprod(1 + rng.normal(0, 0.01, (t, n)), axis=0).astype(np.float32)
    listed = np.tile(np.arange(t)[:, None], (1, n)).astype(np.float32)
    listed[:, 0] = np.arange(t) - 300  # listed late: ineligible before day 550-ish
    values = {"close": close, "open": close, "open_raw": close, "close_raw": close,
              "amount": np.ones((t, n), np.float32) * np.arange(1, n + 1), "circ_mv": close,
              "turnover_rate": np.ones((t, n), np.float32), "pe_ttm": close, "pb_mrq": close, "days_listed": listed}
    flags = {"tradable_buy": np.ones((t, n), bool), "tradable_sell": np.ones((t, n), bool),
             "is_st": np.zeros((t, n), bool), "is_suspended": np.zeros((t, n), bool)}
    values["amount"][:, 2] = 100.0
    codes = np.array([f"sz.{300000 + i}" if i < 3 else f"sh.{600000 + i}" for i in range(n)])
    bars = Bars(np.array([f"2010-{i:04d}" for i in range(t)]), codes, values, flags)
    masks = pool_masks(bars, "all")
    assert not masks[300, 0] and masks[300, 5]
    assert not masks[300, 1]  # least liquid 20% dropped
    assert pool_masks(bars, "growth")[300, :3].sum() == 1  # only the liquid ChiNext name
    spec = AccountSpec(accounts=50, start_from="2010-0260", horizon_days=100, holdings=(2,), seed=1)
    accounts = draw_accounts(bars, spec, masks)
    for a in range(50):
        names = accounts.names[a][accounts.mask[a]]
        assert masks[accounts.start[a], names].all()
        assert accounts.weights[a].sum() == pytest.approx(1.0)
