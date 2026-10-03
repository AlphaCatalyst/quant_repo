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
