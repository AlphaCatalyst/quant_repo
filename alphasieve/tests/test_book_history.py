from pathlib import Path

import pandas as pd
import pytest

from alphasieve.portfolio_book import market
from alphasieve.portfolio_book.attribution import _benchmark_sectors, build_attribution
from alphasieve.portfolio_book.cashflow import add_cashflow
from alphasieve.portfolio_book.history import build_history, load_analysis_report, save_analysis_report
from alphasieve.portfolio_book.importer import import_snapshot
from alphasieve.portfolio_book.rebalance import build_rebalance
from alphasieve.state import connect


def _import(conn, tmp_path, day, quantity, cash):
    path = tmp_path / f"{day}.csv"
    path.write_text(
        f"代码,持仓数量,市价,市值\n600000,{quantity},10,{quantity * 10}\n现金,{cash},1,{cash}\n", encoding="utf-8"
    )
    mapping = Path(__file__).parents[1] / "src/alphasieve/configs/book/mapping_generic.yaml"
    return import_snapshot(conn, path, "demo", day, "test", mapping=mapping)[0]


def test_benchmark_sectors_uses_daily_update_prices(settings, monkeypatch):
    from alphasieve.portfolio_book import attribution

    raw = settings.raw_dir
    weights_dir = raw / "csindex" / "weights" / "000300"
    weights_dir.mkdir(parents=True)
    pd.DataFrame([{"code": "sh.600000", "weight": 100.0}]).to_parquet(weights_dir / "2026-09-01.parquet")
    for root, dates in (("baostock_all", ["2026-09-24"]),
                        ("baostock", ["2026-09-24", "2026-09-30"])):
        folder = raw / root / "daily"
        folder.mkdir(parents=True)
        pd.DataFrame({"date": dates, "close": list(range(10, 10 + len(dates))) }).to_parquet(
            folder / "sh.600000.parquet")
    monkeypatch.setattr(attribution, "industry_asof", lambda *args: ({"sh.600000": "银行"}, {}))
    _, returns, meta = _benchmark_sectors(settings, "2026-09-01", "2026-09-30")
    assert returns["银行"] == pytest.approx(0.1)
    assert meta["price_source"].startswith("baostock/daily")


def test_history_cashflow_and_inferred_trade(settings, monkeypatch, tmp_path):
    days = ["2026-09-01", "2026-09-02", "2026-09-03"]
    monkeypatch.setattr(
        market,
        "history_closes",
        lambda *a: (
            {"sh.600000": pd.Series([10, 11, 12], index=days), "sh.000300": pd.Series([100, 101, 102], index=days)},
            {"source": "synthetic", "code": "sh.000300"},
        ),
    )
    with connect(settings.state_db) as conn:
        _import(conn, tmp_path, days[0], 100, 100)
        later = _import(conn, tmp_path, days[2], 150, 50)
        add_cashflow(conn, "demo", days[2], 500, "deposit", "test")
        report = build_history(conn, settings, account="demo")
    assert report["rows"][0]["nav"] == 1100
    assert report["rows"][1]["nav"] == 1200
    assert report["rows"][1]["return"] == pytest.approx(100 / 1100)
    assert report["rows"][2]["cash_flow"] == 500
    assert report["rows"][2]["return"] == pytest.approx((1850 - 500) / 1200 - 1)
    assert report["trades"] == [
        {"date": days[2], "code": "sh.600000", "quantity_delta": 50, "estimated_value": 600, "inferred": True}
    ]
    assert report["rows"][2]["snapshot_id"] == later["snapshot_id"]
    assert report["summary"]["turnover"] > 0
    save_analysis_report(report, settings, "history")
    assert load_analysis_report(settings, "history")["summary"] == report["summary"]


def test_attribution_and_rebalance(settings, monkeypatch, tmp_path):
    days = ["2026-09-01", "2026-09-02", "2026-09-03"]
    monkeypatch.setattr(
        market,
        "history_closes",
        lambda *a: (
            {"sh.600000": pd.Series([10, 11, 12], index=days), "sh.000300": pd.Series([100, 101, 102], index=days)},
            {"source": "synthetic", "code": "sh.000300"},
        ),
    )
    from alphasieve.portfolio_book import attribution, rebalance

    monkeypatch.setattr(attribution, "industry_asof", lambda *a: ({"sh.600000": "银行"}, {"source": "synthetic"}))
    monkeypatch.setattr(rebalance, "industry_asof", lambda *a: ({"sh.600000": "银行"}, {"source": "synthetic"}))
    with connect(settings.state_db) as conn:
        snapshot = _import(conn, tmp_path, days[0], 100, 100)
        report = build_attribution(conn, settings, days[0], days[-1], by="position", account="demo")
        assert report["rows"][0]["contribution"] == pytest.approx(100 / 1100 + 100 / 1200)
        advice = build_rebalance(conn, settings, snapshot["snapshot_id"])
    assert advice["rows"][0]["action"] == "trim"
    assert advice["rows"][0]["suggested_trim_value"] > 0
    assert advice["summary"]["flagged"] == 1


def test_rebalance_add_requires_explicit_target(settings, monkeypatch, tmp_path):
    from alphasieve.portfolio_book import rebalance

    monkeypatch.setattr(rebalance, "industry_asof", lambda *a: ({"sh.600000": "银行"}, {"source": "synthetic"}))
    with connect(settings.state_db) as conn:
        snapshot = _import(conn, tmp_path, "2026-09-01", 10, 900)
        baseline = build_rebalance(conn, settings, snapshot["snapshot_id"])
        assert baseline["rows"][0]["action"] == "hold"
        limit_path = settings.config_dir / "book" / "limits.yaml"
        limit_path.write_text(
            "single_name_max: 0.20\nindustry_max: 0.35\ndrift_absolute: 0.05\ntarget_weights:\n  sh.600000: 0.18\n",
            encoding="utf-8",
        )
        proposed = build_rebalance(conn, settings, snapshot["snapshot_id"])
    assert proposed["rows"][0]["action"] == "add"
    assert proposed["rows"][0]["suggested_add_value"] == pytest.approx(80)


def test_reconstruct_snapshots_from_trades(settings, monkeypatch, tmp_path):
    from alphasieve.errors import AlphaSieveError
    from alphasieve.portfolio_book import reconstruct

    days = ["2026-09-01", "2026-09-02", "2026-09-03"]
    monkeypatch.setattr(
        market,
        "history_closes",
        lambda *a: ({"sh.600000": pd.Series([10, 11, 12], index=days),
                     "sh.000300": pd.Series([100, 101, 102], index=days)}, {"source": "synthetic"}),
    )
    trades = tmp_path / "trades.csv"
    trades.write_text(
        "account,date,code,name,type,price,quantity,amount,fee,note\n"
        "demo,2026-09-01,600000,浦发银行,修改持仓,10,100,0,0,\n"
        "demo,2026-09-02,600000,浦发银行,买入,11,50,550,5,\n",
        encoding="utf-8",
    )
    with connect(settings.state_db) as conn:
        _import(conn, tmp_path, days[2], 150, 45)
        result = reconstruct.plan(conn, settings, "demo", reconstruct.parse_trades(trades))
        assert [s["as_of"] for s in result["snapshots"]] == days[:2]
        first, second = result["snapshots"]
        assert first["positions"][0]["quantity"] == 100 and first["positions"][-1]["market_value"] == 600
        assert second["positions"][0]["market_value"] == 1650 and second["positions"][-1]["market_value"] == 45
        reconstruct.write(conn, result, "test")
        report = build_history(conn, settings, account="demo")
        assert [r["nav"] for r in report["rows"]] == [1600, 1695, 1845]
        assert reconstruct.plan(conn, settings, "demo", reconstruct.parse_trades(trades))["snapshots"] == []
        _import(conn, tmp_path, "2026-09-04", 200, 45)
        with pytest.raises(AlphaSieveError):
            reconstruct.plan(conn, settings, "demo", reconstruct.parse_trades(trades))


def test_behavior_statistics(settings, monkeypatch):
    from alphasieve.portfolio_book.behavior import build_behavior

    days = pd.bdate_range("2026-08-03", periods=60).strftime("%Y-%m-%d").tolist()
    rising = pd.Series([10 + 0.1 * i for i in range(60)], index=days)
    flat = pd.Series([20.0] * 60, index=days)
    monkeypatch.setattr(market, "history_closes", lambda *a: (
        {"sh.600000": rising, "sh.600001": flat, "sh.000300": flat}, {"source": "synthetic"}))

    def trade(day, code, kind, quantity, price):
        return {"date": days[day], "code": code, "name": "", "type": kind, "price": price,
                "quantity": quantity, "amount": quantity * price, "fee": 1.0}

    report = build_behavior(settings, [
        trade(0, "sh.600000", "修改持仓", 100, 10.0),
        trade(25, "sh.600000", "卖出", 100, 12.5),
        trade(25, "sh.600001", "买入", 50, 20.0),
        trade(30, "sh.600000", "买入", 100, 13.0),
    ])
    assert report["disposition"]["counts"] == {"realized_gain": 1, "realized_loss": 0, "paper_gain": 0,
                                               "paper_loss": 0}
    assert report["repurchase_within_h"] == {"count": 1, "share": 1.0}
    assert report["chasing"]["n"] == 2 and report["chasing"]["mean_prior_excess_h"] > 0
    decision = report["switch_value_h"]["decisions"][0]
    assert decision["value"] == pytest.approx(0 - (rising.iloc[45] / 12.5 - 1))
    assert report["switch_value_h"]["verdict"] is None
