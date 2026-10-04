import json
from pathlib import Path

import pandas as pd
import pytest

from alphasieve.cli.main import main
from alphasieve.portfolio_book import market
from alphasieve.state import connect

FIXTURE = Path(__file__).parent / "fixtures" / "book" / "chinese.csv"


def call(args, capsys):
    code = main([*args, "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_import_dedupe_list_show_and_role(settings, monkeypatch, capsys):
    args = ["book", "import", "--file", str(FIXTURE), "--account", "test", "--as-of", "2026-10-03"]
    code, first = call(args, capsys)
    assert code == 0 and first["data"]["created"]
    snapshot = first["data"]["snapshot"]
    assert [item["code"] for item in snapshot["positions"]] == ["sh.600000", "sz.000001", "CASH"]
    code, second = call(args, capsys)
    assert code == 0 and not second["data"]["created"]
    assert second["data"]["snapshot"]["snapshot_id"] == snapshot["snapshot_id"]
    code, listing = call(["book", "list", "--account", "test"], capsys)
    assert code == 0 and len(listing["data"]["snapshots"]) == 1
    code, shown = call(["book", "show", snapshot["snapshot_id"]], capsys)
    assert code == 0 and shown["data"]["snapshot"]["content_hash"] == snapshot["content_hash"]
    with connect(settings.state_db) as conn:
        assert conn.execute("SELECT count(*) FROM holdings_snapshots").fetchone()[0] == 1
    monkeypatch.setenv("ALPHASIEVE_ROLE", "agent")
    for command in (args, ["book", "list"], ["book", "show", snapshot["snapshot_id"]],
                    ["book", "check", snapshot["snapshot_id"]]):
        code, denied = call(command, capsys)
        assert code == 4 and denied["error"]["code"] == "PERMISSION_DENIED"


def test_checkup_synthetic_prices(settings, monkeypatch, capsys):
    days = pd.bdate_range("2026-06-01", periods=75)
    benchmark = pd.Series([100 * (1.001 if i % 2 else 0.999) ** i for i in range(len(days))],
                          index=days.strftime("%Y-%m-%d"))
    prices = {"sh.000300": benchmark, "sh.600000": benchmark * 2, "sz.000001": benchmark * 3}
    monkeypatch.setattr(market, "recent_closes", lambda *args, **kwargs: prices)
    monkeypatch.setattr(market, "current_quotes", lambda *args, **kwargs: {})
    monkeypatch.setattr(market, "current_industry", lambda *args: (
        {"sh.600000": "银行", "sz.000001": "银行"},
        {"label": "current snapshot, not PIT", "source": "synthetic", "as_of": "2026-10-03"}))
    _, imported = call(["book", "import", "--file", str(FIXTURE), "--account", "test",
                        "--as-of", "2026-10-03"], capsys)
    snapshot_id = imported["data"]["snapshot"]["snapshot_id"]
    code, checked = call(["book", "check", snapshot_id], capsys)
    assert code == 0
    report = checked["data"]["report"]
    assert report["total_value"] == 3500
    assert report["top_n_concentration"] == pytest.approx(3000 / 3500)
    assert report["hhi"] == pytest.approx((1000**2 + 2000**2 + 500**2) / 3500**2)
    assert report["industry_weights"]["银行"] == pytest.approx(3000 / 3500)
    assert report["portfolio_beta_60d"] == pytest.approx(3000 / 3500)
    assert report["pairwise_correlation"]["max_pair"]["correlation"] == pytest.approx(1)
    assert report["stress_returns"]["benchmark_down_10pct"] == pytest.approx(-0.1 * 3000 / 3500)
    assert report["stress_returns"]["industry_down_20pct"]["银行"] == pytest.approx(-0.2 * 3000 / 3500)
    assert report["stress_returns"]["largest_position_down_30pct"] == pytest.approx(-0.3 * 2000 / 3500)
    assert "持仓体检" in checked["data"]["markdown"]


def test_checkup_flags_announcements_and_saved_sections(settings, monkeypatch, capsys):
    from alphasieve import announcements, redflag
    from alphasieve.portfolio_book.checkup import load_report

    days = pd.bdate_range("2026-06-01", periods=75).strftime("%Y-%m-%d")
    series = pd.Series(range(100, 175), index=days, dtype=float)
    monkeypatch.setattr(market, "recent_closes", lambda *args, **kwargs: {
        "sh.000300": series, "sh.600000": series * 2, "sh.510300": series,
        "sz.127045": series})
    monkeypatch.setattr(market, "current_quotes", lambda settings, codes: {
        code: {"bond_stock_code": "002714"} if code == "sz.127045" else {"pb_ratio": 0.8,
        "circulating_market_cap": 1e10} for code in codes})
    monkeypatch.setattr(market, "current_industry", lambda *args: ({}, {"label": "test"}))
    monkeypatch.setattr(redflag, "flags_for", lambda settings, codes, asof: [{
        "code": "sh.600000", "level": "amber", "extra": "future field",
        "rules": {"cash_profit": {"level": "amber", "evidence": {"period": "20250630"}}}}])
    wanted = []
    def fake_recent(settings, codes, since):
        wanted.extend(codes)
        assert since == "2026-09-03"
        return [{"code": "127045", "importance": "high", "event_type": "convertible_redemption",
                 "title": "强赎公告", "published_date": "2026-09-30", "pdf_url": "https://example.test/1"},
                {"code": "002714", "importance": "medium", "event_type": "convertible_revision",
                 "title": "下修公告", "published_date": "2026-10-01", "pdf_url": "https://example.test/2"}]
    monkeypatch.setattr(announcements, "recent_for", fake_recent)
    monkeypatch.setattr(announcements, "list_local", lambda *args, **kwargs: [
        {"code": "127045", "importance": "low", "event_type": "convertible_other",
         "title": "回售公告", "published_date": "2026-10-02", "pdf_url": "https://example.test/3",
         "announcement_id": "3"}])
    fixture = FIXTURE.with_name("mixed.csv")
    _, imported = call(["book", "import", "--file", str(fixture), "--account", "mixed",
                        "--as-of", "2026-10-03"], capsys)
    snapshot_id = imported["data"]["snapshot"]["snapshot_id"]
    code, checked = call(["book", "check", snapshot_id], capsys)
    assert code == 0
    report = checked["data"]["report"]
    assert report["red_flags"]["flagged_weight"] == pytest.approx(1000 / 7000)
    assert report["red_flags"]["holdings"][0]["rules"][0]["evidence"]["period"] == "20250630"
    assert set(wanted) == {"600000", "127045", "002714"}
    assert len(report["announcements"]["convertible_bond_items"]) == 3
    assert report["style_exposure"]["model"] == "book_simplified_not_rm1"
    assert load_report(snapshot_id, settings)["announcements"] == report["announcements"]


def test_mixed_book_quote_bonds_and_price_cache(settings, monkeypatch, capsys):
    from alphasieve.data.providers import westock

    fixture = FIXTURE.with_name("mixed.csv")
    quotes = {
        "sh.600000": {"code": "sh600000", "price": 10, "circulating_market_cap": 12e9,
                      "total_market_cap": 25e9, "pe_ratio": 5, "pb_ratio": 0.7,
                      "low_52week": 8, "high_52week": 12},
        "sh.510300": {"code": "sh510300", "price": 4.5},
        "sz.127045": {"code": "sz127045", "price": 160, "bond_equity_value": 100,
                      "bond_pure_premium": 15, "bond_double_low": 220, "bond_ytm": -2,
                      "bond_rating": "A+", "bond_undue_size": 20000, "bond_undue_term": 0.8,
                      "bond_stock_code": "002714", "bond_redeem_price_trigger": 50,
                      "bond_buyback_price_trigger": 25},
        "sz.002714": {"code": "sz002714", "price": 45},
    }
    quote_calls = []

    def fake_quote(codes):
        quote_calls.append(codes)
        return {code: quotes[code] for code in codes if code in quotes}

    kline_calls = []
    days = pd.bdate_range("2026-06-01", periods=75).strftime("%Y-%m-%d")

    def fake_kline(codes, start, end):
        kline_calls.extend(codes)
        return pd.DataFrame({"date": days, "last": [100 + n for n in range(len(days))]})

    def fake_baostock(code, start, end, benchmark=False):
        assert code in {"sh.600000", "sh.000300"}
        return pd.DataFrame({"date": days, "close": [100 + n for n in range(len(days))]})

    monkeypatch.setattr(westock, "quote", fake_quote)
    monkeypatch.setattr(westock, "kline", fake_kline)
    monkeypatch.setattr(market, "_fetch_closes", fake_baostock)
    monkeypatch.setattr(market, "current_industry", lambda *args: (
        {"sh.600000": "银行"}, {"label": "current snapshot, not PIT", "source": "synthetic", "as_of": "2026-10-03"}))
    _, imported = call(["book", "import", "--file", str(fixture), "--account", "mixed",
                        "--as-of", "2026-10-03"], capsys)
    snapshot_id = imported["data"]["snapshot"]["snapshot_id"]
    code, checked = call(["book", "check", snapshot_id], capsys)
    assert code == 0
    report = checked["data"]["report"]
    assert report["asset_class_weights"] == pytest.approx({"stock": 1000 / 7000, "etf": 4500 / 7000,
                                                              "convertible_bond": 1180 / 7000, "cash": 320 / 7000})
    assert report["market_cap_buckets"]["50–200亿"] == pytest.approx(1000 / 7000)
    assert report["holdings"][1]["quote"]["position_52week"] == pytest.approx(0.5)
    bond = report["convertible_bonds"][0]
    assert bond["conversion_premium_pct"] == pytest.approx(60)
    assert bond["remaining_size_yi"] == pytest.approx(2)
    assert bond["redeem_distance_pct"] == pytest.approx(-10)
    assert bond["flags"] == ["high_conversion_premium", "small_remaining_size", "short_maturity", "low_rating"]
    assert set(kline_calls) == {"sh510300", "sz127045"}
    assert report["beta_60d"]["sh.510300"] is not None
    assert report["beta_60d"]["sz.127045"] is not None
    assert len(quote_calls) == 2
    market.current_quotes(settings, ["sh.600000", "sh.510300", "sz.127045", "sz.002714"])
    assert len(quote_calls) == 2


def test_quote_wrapper_batch_retry(monkeypatch):
    from alphasieve.data.providers import westock

    calls = []

    def fake_call(args):
        calls.append(args)
        symbol = "sz127045" if len(calls) == 2 else "sh600000"
        return {"data": [{"symbol": symbol, "data": {"code": symbol, "price": 100}}]}

    monkeypatch.setattr(westock, "_call", fake_call)
    result = westock.quote(["sh.600000", "sz.127045"])
    assert set(result) == {"sh.600000", "sz.127045"}
    assert len(calls) == 2


def test_current_industry_prefers_latest_sw_l1_with_csrc_fallback(settings):
    raw = settings.raw_dir
    sw_dir = raw / "westock" / "sw_industry"
    sw_dir.mkdir(parents=True)
    (raw / "baostock").mkdir(parents=True)
    pd.DataFrame([{"code": "sh.600000", "level": 1, "sector_name": "旧分类"}]).to_parquet(
        sw_dir / "2026-10-01.parquet")
    pd.DataFrame([{"code": "sh.600000", "level": 1, "sector_name": "银行"},
                  {"code": "sh.600000", "level": 2, "sector_name": "银行细分"}]).to_parquet(
        sw_dir / "2026-10-03.parquet")
    pd.DataFrame([{"code": "sh.600000", "industry": "金融"},
                  {"code": "sz.000001", "industry": "银行业"}]).to_parquet(raw / "baostock" / "industry.parquet")
    industries, meta = market.current_industry(settings, ["sh.600000", "sz.000001"])
    assert industries == {"sh.600000": "银行", "sz.000001": "银行业"}
    assert meta["as_of"] == "2026-10-03"
    assert meta["label"] == "current snapshot, not PIT"


def test_cash_option_and_local_market_cache(settings, monkeypatch, tmp_path):
    export = tmp_path / "broker.csv"
    export.write_text("代码,持仓数量,市价\n600000,10,12\n", encoding="utf-8")
    from alphasieve.portfolio_book.importer import import_snapshot

    with connect(settings.state_db) as conn:
        snapshot, created = import_snapshot(conn, export, "cash-account", "2026-10-03", "test", cash=80)
    assert created and snapshot["positions"][-1]["market_value"] == 80
    calls = []

    def fake_fetch(code, start, end, benchmark=False):
        calls.append((code, benchmark))
        return pd.DataFrame({"date": ["2026-10-01", "2026-10-02"], "close": [10, 11]})

    monkeypatch.setattr(market, "_fetch_closes", fake_fetch)
    market.recent_closes(settings, ["sh.600000"], "sh.000300", end="2026-10-03")
    market.recent_closes(settings, ["sh.600000"], "sh.000300", end="2026-10-03")
    assert len(calls) == 2
    assert all((settings.hot_root / "book" / "market" / f"{code}-2026-10-03.json").exists()
               for code in ("sh.600000", "sh.000300"))
