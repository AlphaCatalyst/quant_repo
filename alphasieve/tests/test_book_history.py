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
