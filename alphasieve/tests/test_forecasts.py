import json
from datetime import date, timedelta

import pandas as pd
import pytest

from alphasieve.cli.main import main
from alphasieve.errors import AlphaSieveError
from alphasieve.forecasts.service import (
    list_forecasts,
    register_forecast,
    score_forecasts,
    settle_forecast,
    show_forecast,
    verify_forecasts,
    void_forecast,
)
from alphasieve.state import connect


def spec(resolver="manual", *, p=0.8, condition=None):
    params = {"settle_date": "2020-01-02"}
    if resolver == "sina_futures_close":
        params["symbol"] = "LH2709"
    if resolver == "stock_close":
        params["code"] = "sz.000001"
    return {
        "statement": "Close exceeds 10", "thesis_id": "T-1", "resolver": resolver,
        "resolver_params": params, "condition": condition or {"op": ">", "threshold": 10},
        "p": p, "deadline": "2020-01-03", "source_of_truth": "official daily close",
    }


def test_manual_lifecycle_and_scores(settings):
    conn = connect(settings.state_db)
    first = register_forecast(conn, spec(), "tester")
    second = register_forecast(conn, spec(p=0.2, condition={"op": "between", "threshold": [8, 12]}), "tester")
    settle_forecast(conn, first["forecast_id"], "tester", value=12, source="exchange record")
    settle_forecast(conn, second["forecast_id"], "tester", value=13, source="exchange record")
    assert show_forecast(conn, first["forecast_id"])["resolution"]["outcome"] is True
    assert list_forecasts(conn, status="settled")["count"] == 2
    scores = score_forecasts(conn)
    assert scores["count"] == 2
    assert scores["brier_score"] == pytest.approx(0.04)
    assert scores["by_thesis"]["T-1"]["count"] == 2
    assert scores["calibration"][8]["observed_frequency"] == 1
    assert scores["calibration"][2]["observed_frequency"] == 0
    assert verify_forecasts(conn) == {"ok": True, "rows": 4, "errors": []}
    with pytest.raises(AlphaSieveError, match="already settled"):
        settle_forecast(conn, first["forecast_id"], "tester", value=12, source="same")
    conn.close()


def test_validation_void_and_early_settlement(settings):
    conn = connect(settings.state_db)
    for bad in (spec(p=1), spec(condition={"op": "between", "threshold": [12, 8]}),
                spec(resolver="stock_close") | {"resolver_params": {"settle_date": "2020-01-02"}}):
        with pytest.raises(AlphaSieveError) as exc:
            register_forecast(conn, bad, "tester")
        assert exc.value.code == "VALIDATION_ERROR"
    future = spec() | {"resolver_params": {"settle_date": str(date.today() + timedelta(days=2))},
                       "deadline": str(date.today() + timedelta(days=3))}
    forecast_id = register_forecast(conn, future, "tester")["forecast_id"]
    with pytest.raises(AlphaSieveError, match="before settle_date"):
        settle_forecast(conn, forecast_id, "tester", value=11, source="report")
    past_id = register_forecast(conn, spec(), "tester")["forecast_id"]
    with pytest.raises(AlphaSieveError, match="requires value and source"):
        settle_forecast(conn, past_id, "tester", value=11)
    void_forecast(conn, forecast_id, "source withdrawn", "tester")
    with pytest.raises(AlphaSieveError, match="already voided"):
        settle_forecast(conn, forecast_id, "tester", value=11, source="report")
    assert score_forecasts(conn)["count"] == 0
    conn.close()


def test_sina_resolver_uses_exact_date_and_rejects_override(settings, monkeypatch):
    from alphasieve.data.providers import sina

    calls = []

    def bars(symbol):
        calls.append(symbol)
        return pd.DataFrame({"date": ["2020-01-01", "2020-01-02"], "close": [9, 11]})

    monkeypatch.setattr(sina, "daily_bars", bars)
    conn = connect(settings.state_db)
    forecast_id = register_forecast(conn, spec("sina_futures_close"), "tester")["forecast_id"]
    with pytest.raises(AlphaSieveError, match="does not accept"):
        settle_forecast(conn, forecast_id, "tester", value=8, source="caller")
    result = settle_forecast(conn, forecast_id, "tester")
    assert calls == ["LH2709"]
    assert result["observed_value"] == 11
    assert result["outcome"] is True
    conn.close()


def test_stock_resolver_uses_existing_daily_provider(settings, monkeypatch):
    from alphasieve.data.providers import baostock

    calls = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def daily(self, code, start, end):
            calls.append((code, start, end))
            return pd.DataFrame({"date": [start], "close": [10.0]})

    monkeypatch.setattr(baostock, "BaoStockSession", Session)
    conn = connect(settings.state_db)
    forecast_id = register_forecast(conn, spec("stock_close", condition={"op": ">=", "threshold": 10}),
                                    "tester")["forecast_id"]
    assert settle_forecast(conn, forecast_id, "tester")["outcome"] is True
    assert calls == [("sz.000001", "2020-01-02", "2020-01-02")]
    conn.close()


def test_forecast_and_journal_json_cli(settings, tmp_path, capsys):
    def run(*args):
        assert main([*args, "--json"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "ok"
        assert result["command"] == " ".join(args[:2])
        return result["data"]

    forecast_file = tmp_path / "forecast.yaml"
    forecast_file.write_text("""statement: Close exceeds 10
resolver: manual
resolver_params: {settle_date: '2020-01-02'}
condition: {op: '>', threshold: 10}
p: 0.8
deadline: '2020-01-03'
source_of_truth: Exchange report
thesis_id: T-1
""", encoding="utf-8")
    first = run("forecast", "add", "--file", str(forecast_file))["forecast_id"]
    second = run("forecast", "add", "--file", str(forecast_file))["forecast_id"]
    assert run("forecast", "list", "--status", "open")["count"] == 2
    assert run("forecast", "show", first)["status"] == "open"
    assert run("forecast", "settle", first, "--value", "11", "--source", "exchange")["outcome"]
    assert run("forecast", "void", second, "--reason", "invalid source")["status"] == "voided"
    assert run("forecast", "score", "--thesis", "T-1")["count"] == 1
    assert run("forecast", "verify")["ok"]

    entry_file = tmp_path / "entry.yaml"
    entry_file.write_text(f"entry_kind: note\nreason: Review position\n"
                          f"thesis_id: T-1\nforecast_ids: [{first}]\n", encoding="utf-8")
    entry = run("journal", "add", "--file", str(entry_file))["entry_id"]
    assert run("journal", "list", "--thesis", "T-1")["count"] == 1
    assert run("journal", "show", entry)["entry_id"] == entry
    assert run("journal", "verify")["ok"]
