"""Read-only platform views use synthetic local state only."""

import json
from datetime import date

import pytest
import yaml
from fastapi.testclient import TestClient

from alphasieve.forecasts.service import register_forecast, settle_forecast
from alphasieve.journal.service import add_entry
from alphasieve.portfolio_book.checkup import save_report
from alphasieve.portfolio_book.history import save_analysis_report
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
              "asset_class_weights": {"stock": 1.0}, "market_cap_buckets": {"50–200亿": 1.0},
              "holdings": [{"code": "sh.600000", "asset_class": "stock", "weight": 1.0,
                            "market_cap_bucket": "50–200亿", "quote": {"pe_ratio": 6.0}}],
              "convertible_bonds": [],
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
    assert row["report"]["asset_class_weights"] == {"stock": 1.0}
    assert row["report"]["holdings"][0]["quote"]["pe_ratio"] == 6.0
    assert row["report"]["stress_returns"]["benchmark_down_10pct"] == pytest.approx(-0.11)
    assert "/data/alphasieve" not in json.dumps(snapshots)


def test_book_analysis_endpoints_only_serve_saved_matching_reports(platform_client, settings, monkeypatch):
    from alphasieve.portfolio_book import market

    client, auth, _, snapshot_id = platform_client
    monkeypatch.setattr(market, "history_closes", lambda *args: pytest.fail("market path used"))
    reports = {
        "history": {"start": "2026-10-01", "end": "2026-10-03", "rows": [], "summary": {}},
        "attribution": {"start": "2026-10-01", "end": "2026-10-03", "by": "position", "rows": []},
        "rebalance": {"snapshot_id": snapshot_id, "rows": []},
    }
    for kind, report in reports.items():
        save_analysis_report(report, settings, "attribution-position" if kind == "attribution" else kind)
        endpoint = f"/api/book/{kind}"
        assert client.get(endpoint).status_code == 401
        assert client.get(endpoint, auth=auth).json()["report"] == report
    assert client.get("/api/book/history?start=2026-10-02", auth=auth).json()["report"] is None
    assert client.get("/api/book/attribution?by=thesis", auth=auth).json()["report"] is None
    from dataclasses import replace

    open_client = TestClient(create_app(replace(settings), require_auth=False))
    for kind in reports:
        assert open_client.get(f"/api/book/{kind}").status_code == 200


def test_platform_documents_are_whitelisted(platform_client):
    client, auth, _, _ = platform_client
    for name in ("personal-account.md", "broad-quant-platform.md",
                 "platform-implementation.md", "coverage-review.md",
                 "financial-red-flags.md", "announcements.md",
                 "sw-industry-sensitivity.md", "monitoring.md", "control-plane.md",
                 "personal-decision-tasks.md"):
        assert client.get(f"/api/docs/{name}", auth=auth).status_code == 200
    assert client.get("/api/docs/not-allowed.md", auth=auth).status_code == 404


def test_alert_visibility_requires_human_auth(platform_client, settings):
    client, auth, _, _ = platform_client
    with connect(settings.state_db) as conn:
        for aid, visibility in (("public-test", "public"), ("private-test", "private")):
            conn.execute("INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?,?)", (
                aid, "test", "info", "subject", aid, "detail", "[]",
                "2026-10-04T00:00:00+00:00", visibility))
    assert client.get("/api/alerts").json()["alerts"][0]["alert_id"] == "public-test"
    assert client.get("/api/alerts/private").status_code == 401
    private = client.get("/api/alerts/private", auth=auth)
    assert private.status_code == 200
    assert [a["alert_id"] for a in private.json()["alerts"]] == ["private-test"]


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
    monkeypatch.setattr(commands_book, "build_report", lambda *args, **kwargs: (report, "合成体检"))
    assert main(["book", "check", snapshot_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["report"] == report
    assert load_report(snapshot_id, settings) == report
    assert (settings.hot_root / "book" / "reports" / f"{snapshot_id}.json").is_file()


def test_control_endpoints_public_read_only(settings, monkeypatch):
    """Public control data never needs credentials or a resource probe."""
    from datetime import UTC, datetime

    from alphasieve.control import health, jobs, llm, resources, scheduler

    settings.state_db.parent.mkdir(parents=True, exist_ok=True)
    with connect(settings.state_db):
        pass
    stamp = datetime.now(UTC).isoformat()
    snapshot = {"at": stamp, "local": {"cpu_count": 8, "loadavg": [1.5, 1, 1]},
                "ssh_hosts": {}, "ray_clusters": {}, "llm_endpoints": {}}
    monkeypatch.setattr(resources, "read_snapshot", lambda s: snapshot)
    monkeypatch.setattr(resources, "read_history", lambda s, hours: [{"at": stamp}])
    monkeypatch.setattr(resources, "snapshot", lambda s: pytest.fail("probe in GET"))
    monkeypatch.setattr(health, "collect", lambda s, c: {"overall": "ok", "checks": [{"id": "backup", "status": "ok"}]})
    monkeypatch.setattr(jobs, "list_jobs", lambda c, **kw: [
        {"job_id": "test-job", "kind": "train", "status": "running",
         "params": {"holdings": "secret"}}])
    monkeypatch.setattr(jobs, "job_summary", lambda c: {"by_status": {"running": 1}})
    monkeypatch.setattr(scheduler, "list_schedule", lambda s, c: [{"name": "daily", "enabled": True}])
    monkeypatch.setattr(llm, "state", lambda s: {"available": True, "paused_since": None})
    client = TestClient(create_app(settings, require_auth=False))
    result = client.get("/api/control/resources")
    assert result.status_code == 200
    assert result.json()["snapshot"] == snapshot
    assert result.json()["history"] == [{"at": stamp}]
    assert result.json()["stale"] is False
    assert result.json()["age_s"] < 5
    assert client.get("/api/control/health").json()["overall"] == "ok"
    assert client.get("/api/control/jobs?open=true&kind=train").json()["jobs"][0]["job_id"] == "test-job"
    assert client.get("/api/control/schedule").json()[0]["name"] == "daily"
    assert client.get("/api/control/llm").json()["available"] is True
    for path in ("resources", "health", "jobs", "schedule", "llm"):
        assert "holdings" not in json.dumps(client.get(f"/api/control/{path}").json()).lower()
    assert client.post("/api/control/jobs").status_code == 405
    assert client.get("/api/book").status_code == 200
    assert client.get("/api/alerts/private").status_code == 200


def test_control_resource_snapshot_missing_or_stale(settings, monkeypatch):
    from alphasieve.control import resources

    monkeypatch.setattr(resources, "read_history", lambda s, hours: [])
    monkeypatch.setattr(resources, "read_snapshot", lambda s: None)
    client = TestClient(create_app(settings, require_auth=False))
    assert client.get("/api/control/resources").json()["stale"] is True
    assert client.get("/api/control/resources").json()["age_s"] is None
    monkeypatch.setattr(resources, "read_snapshot", lambda s: {"at": "2020-01-01T00:00:00+00:00"})
    stale = client.get("/api/control/resources").json()
    assert stale["stale"] is True and stale["age_s"] > 300
