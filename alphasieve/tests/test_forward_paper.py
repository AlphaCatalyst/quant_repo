import json

import numpy as np
import pandas as pd
import pytest

from alphasieve.data.access import Panel
from alphasieve.fresh.paper import record_target, settle_day
from alphasieve.state import connect
from alphasieve.strategy.execution import initial_state, step_day


def _panel(missing=None):
    dates = pd.bdate_range("2026-10-01", periods=30)
    rows = []
    for i, day in enumerate(dates):
        for code in ("A", "B"):
            price = 10.0 + i * (0.1 if code == "A" else 0.02)
            rows.append({"date": day, "code": code, "open": price, "close": price + 0.05,
                         "tradable_buy": True, "tradable_sell": True, "amount": 1e8,
                         "ret_1d": 0.01, "circ_mv": 1e9, "in_universe": True})
    long = pd.DataFrame(rows)
    if missing:
        long.loc[(long.date == dates[missing]) & (long.code == "A"), "close"] = np.nan
    benchmark = pd.DataFrame({"date": dates, "zz500_close": 1000 + np.arange(len(dates))})
    return Panel(long, {"tier": "fresh", "signature": "synthetic", "window": {
        "start": str(dates[0].date()), "end": str(dates[-1].date())}}, benchmark)


def _book(conn, panel):
    conn.execute("INSERT INTO paper_books VALUES (?,?,?,?,?,?,?,?)",
                 ("B-1", "F-1", "observation", None, 1e7, "zz500", str(panel.dates[5].date()), "now"))


def test_step_day_manual_cost_and_blocked():
    state = initial_state(["A", "B"])
    state, day = step_day(state, prev_close=np.array([10, 10]), open_px=np.array([11, 10]),
                          close_px=np.array([12, 10]), buy_ok=np.array([True, False]),
                          sell_ok=np.array([True, True]), goal=np.array([0.5, 0.5]))
    assert day["actual_turnover"] == pytest.approx(0.25)
    assert day["rejected_trades"] == 1
    assert len(day["fills"]) == 2
    assert state["cash"] == pytest.approx(0.5 - 0.5 * (0.00025 + 0.0005))
    assert state["nav"] == pytest.approx(state["cash"] + 0.5 * 12 / 11)
    state2, no_trade = step_day(state, prev_close=np.array([12, 10]), open_px=np.array([12, 10]),
                                close_px=np.array([12, 10]), buy_ok=np.array([True, True]),
                                sell_ok=np.array([True, True]))
    assert state2["nav"] == state["nav"]
    assert no_trade["actual_turnover"] == 0
    with pytest.raises(ValueError, match="valuation"):
        step_day(state, prev_close=np.array([12, 10]), open_px=np.array([np.nan, 10]),
                 close_px=np.array([12, 10]), buy_ok=np.array([True, True]),
                 sell_ok=np.array([True, True]))
    _, halted = step_day(state, prev_close=np.array([12, 10]), open_px=np.array([np.nan, 10]),
                         close_px=np.array([np.nan, 10]), buy_ok=np.array([False, True]),
                         sell_ok=np.array([False, True]))
    assert halted["stale_codes"] == ["A"]


def test_paper_25_days_append_gap_recovery_and_chain(tmp_path):
    conn = connect(tmp_path / "state.db")
    panel = _panel(missing=8)
    _book(conn, panel)
    first_signal = str(panel.dates[4].date())
    record_target(conn, "B-1", first_signal, {"A": 0.5},
                  sealed_at=f"{first_signal}T16:00:00+08:00",
                  next_trading_date=str(panel.dates[5].date()))
    with pytest.raises(ValueError, match="CONFLICT"):
        record_target(conn, "B-1", first_signal, {"A": 0.4},
                      sealed_at=f"{first_signal}T16:00:00+08:00",
                      next_trading_date=str(panel.dates[5].date()))
    with pytest.raises(ValueError, match="missed_signal"):
        record_target(conn, "B-1", str(panel.dates[1].date()), {"A": 0.4},
                      sealed_at=f"{panel.dates[2].date()}T09:25:00+08:00",
                      next_trading_date=str(panel.dates[2].date()))
    rows = []
    for day in panel.dates[5:]:
        rows.append(settle_day(conn, "B-1", str(day.date()), panel))
    assert len(rows) == 25
    assert rows[3]["status"] == "data_gap" and rows[3]["ret"] is None
    assert rows[4]["status"] == "recovered_multi_day" and rows[4]["ret"] is None
    assert rows[-1]["nav"] > 1
    assert rows[-1]["benchmark_nav"] > 1
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
    saved = conn.execute("SELECT row_hash FROM paper_days WHERE book_id='B-1' ORDER BY date").fetchall()
    assert len({r[0] for r in saved}) == 25
    assert settle_day(conn, "B-1", str(panel.dates[-1].date()), panel)["row_hash"] == saved[-1][0]
    with pytest.raises(ValueError, match="backfill"):
        settle_day(conn, "B-1", str(panel.dates[1].date()), panel)
    checkpoint_row = conn.execute("SELECT checkpoint_json FROM paper_days ORDER BY date DESC LIMIT 1").fetchone()
    checkpoint = json.loads(checkpoint_row[0])
    assert checkpoint["state"]["nav"] == rows[-1]["nav"]
