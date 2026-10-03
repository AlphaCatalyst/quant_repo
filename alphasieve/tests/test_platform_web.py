"""Read-only platform views use synthetic local state only."""

import json
from datetime import date

import pytest
import yaml
from fastapi.testclient import TestClient

from alphasieve.forecasts.service import register_forecast, settle_forecast
from alphasieve.journal.service import add_entry
from alphasieve.portfolio_book.checkup import save_report
from alphasieve.portfolio_book.importer import import_snapshot
from alphasieve.state import connect
from alphasieve.web.app import _load_credentials, create_app


@pytest.fixture
def platform_client(settings, tmp_path, monkeypatch):
    thesis_id = "test-thesis"
    thesis_dir = tmp_path / "theses"
    thesis_dir.mkdir()
    monkeypatch.setenv("ALPHASIEVE_THESES_DIR", str(thesis_dir))
    thesis = {
        "thesis_id": thesis_id, "title": "合成论点", "assets": [{"code": "sh.600000", "name": "测试股票"}],
        "status": "researching", "as_of": "2026-10-01", "core_variables": ["x"],
        "argument": ["x drives value"],
        "parameters": {"x": {"base": 2, "unit": "元", "low": 1, "high": 4, "evidence": ["e1"]}},
        "evidence": [{"id": "e1", "claim": "已观察", "value": 2, "source": "合成来源",
                      "grade": "A", "accessed": "2026-10-01"}],
        "valuation": {"formula": "x * 5", "output_unit": "元", "price_param": "x", "market_price": 15},
        "scenarios": {"up": {"x": 4}},
        "falsifiers": [{"id": "f1", "condition": "x < 1", "variable": "x", "action": "退出"}],
        "position_limit": 0.1, "proposed_forecasts": [{"statement": "x 增长"}], "revisions": [],
    }
    (thesis_dir / f"{thesis_id}.yaml").write_text(yaml.safe_dump(thesis, allow_unicode=True), encoding="utf-8")

    export = tmp_path / "broker.csv"
    export.write_text("代码,持仓数量,市价\n600000,10,12\n", encoding="utf-8")
    with connect(settings.state_db) as conn:
        registered = register_forecast(conn, {
            "statement": "测试价格超过 10", "thesis_id": thesis_id, "resolver": "manual",
            "resolver_params": {"settle_date": "2026-10-01"},
            "condition": {"op": ">", "threshold": 10}, "p": 0.8,
            "deadline": "2026-10-02", "source_of_truth": "合成记录",
        }, "test")
        settle_forecast(conn, registered["forecast_id"], "test", value=12,
                        source="合成记录", today=date(2026, 10, 3))
        add_entry(conn, {"entry_kind": "note", "thesis_id": thesis_id, "reason": "合成复盘"}, "test")
        snapshot, _ = import_snapshot(conn, export, "synthetic-account", "2026-10-03", "test")
    snapshot_id = snapshot["snapshot_id"]
    report = {"snapshot_id": snapshot_id, "total_value": 120.0,
              "weights": {"sh.600000": 1.0}, "industry_weights": {"银行": 1.0},
              "portfolio_beta_60d": 1.1,
              "stress_returns": {"benchmark_down_10pct": -0.11}}
    save_report(report, settings)
    client = TestClient(create_app(settings))
    return client, _load_credentials(settings), registered["forecast_id"], snapshot_id


def test_theses_list_and_detail(platform_client):
    client, auth, forecast_id, _ = platform_client
    assert client.get("/api/theses").status_code == 401
    listing = client.get("/api/theses", auth=auth).json()["theses"]
    assert len(listing) == 1
    row = listing[0]
    assert row["thesis_id"] == "test-thesis"
    assert row["title"] == "合成论点" and row["status"] == "researching"
    assert row["falsifiers_count"] == 1 and row["linked_forecasts_count"] == 1
    assert row["base_valuation"] == pytest.approx(10)
    assert row["market_price"] == 15
    detail = client.get("/api/theses/test-thesis", auth=auth).json()
    assert detail["thesis"]["thesis_id"] == "test-thesis"
    assert detail["scenarios"]["up"] == pytest.approx(20)
    assert detail["sensitivity"][0]["parameter"] == "x"
    assert detail["evidence"][0]["grade"] == "A"
    assert detail["falsifiers"][0]["id"] == "f1"
    assert detail["proposed_forecasts"][0]["statement"] == "x 增长"
    assert detail["registered_forecasts"][0]["forecast_id"] == forecast_id
    assert detail["journal_entries"][0]["payload"]["reason"] == "合成复盘"
    assert client.get("/api/theses/missing", auth=auth).status_code == 404


def test_forecasts_include_score_and_calibration(platform_client):
    client, auth, forecast_id, _ = platform_client
    assert client.get("/api/forecasts").status_code == 401
    body = client.get("/api/forecasts", auth=auth).json()
    assert body["forecasts"][0]["forecast_id"] == forecast_id
    assert body["forecasts"][0]["status"] == "settled"
    assert body["score"]["count"] == 1
    assert body["score"]["brier_score"] == pytest.approx(0.04)
    assert sum(bucket["count"] for bucket in body["score"]["calibration"]) == 1


def test_book_uses_stored_report_without_market_fetch(platform_client, monkeypatch):
    from alphasieve.portfolio_book import market

    client, auth, _, snapshot_id = platform_client
    monkeypatch.setattr(market, "recent_closes", lambda *args, **kwargs: pytest.fail("network path used"))
    monkeypatch.setattr(market, "current_industry", lambda *args, **kwargs: pytest.fail("market path used"))
    assert client.get("/api/book").status_code == 401
    snapshots = client.get("/api/book", auth=auth).json()["snapshots"]
    assert len(snapshots) == 1
    row = snapshots[0]
    assert row["snapshot_id"] == snapshot_id
    assert row["account"] == "synthetic-account" and row["as_of"] == "2026-10-03"
    assert row["positions_count"] == 1 and row["total_value"] == pytest.approx(120)
    assert row["report"]["industry_weights"] == {"银行": 1.0}
    assert row["report"]["stress_returns"]["benchmark_down_10pct"] == pytest.approx(-0.11)
    assert "/data/alphasieve" not in json.dumps(snapshots)


def test_platform_documents_are_whitelisted(platform_client):
    client, auth, _, _ = platform_client
    for name in ("26-personal-account.md", "27-broad-quant-platform.md",
                 "28-platform-implementation.md"):
        assert client.get(f"/api/docs/{name}", auth=auth).status_code == 200
    assert client.get("/api/docs/29-not-allowed.md", auth=auth).status_code == 404


def test_book_check_persists_report(settings, tmp_path, monkeypatch, capsys):
    from alphasieve.cli import commands_book
    from alphasieve.cli.main import main
    from alphasieve.portfolio_book.checkup import load_report

    export = tmp_path / "broker.csv"
    export.write_text("代码,持仓数量,市价\n600000,10,12\n", encoding="utf-8")
    with connect(settings.state_db) as conn:
        snapshot, _ = import_snapshot(conn, export, "synthetic-account", "2026-10-03", "test")
    snapshot_id = snapshot["snapshot_id"]
    report = {"snapshot_id": snapshot_id, "total_value": 120.0, "weights": {"sh.600000": 1.0}}
    monkeypatch.setattr(commands_book, "build_report", lambda *args: (report, "合成体检"))
    assert main(["book", "check", snapshot_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["report"] == report
    assert load_report(snapshot_id, settings) == report
    assert (settings.hot_root / "book" / "reports" / f"{snapshot_id}.json").is_file()
