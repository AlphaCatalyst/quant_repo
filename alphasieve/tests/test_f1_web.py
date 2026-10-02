from dataclasses import replace
import json

from fastapi.testclient import TestClient
from fixtures.campaign import campaign_spec, start_campaign

from alphasieve.contracts import FactorSpec
from alphasieve.evaluation.evaluate import evaluate_spec
from alphasieve.state import connect
from alphasieve.web.app import _load_credentials, create_app

REVERSAL = {"name": "rev_3d_excess", "expression": "ts_sum(excess_ret_1d, 3)", "direction": -1,
            "hypothesis": "short-term reversal", "cell": {"domain": "price", "form": "reversal", "scale": "short"}}


def test_web_requires_login_and_serves_read_models(panel_settings):
    start_campaign(panel_settings, campaign_spec("c-web"))
    conn = connect(panel_settings.state_db)
    result = evaluate_spec(replace(panel_settings, role="agent"), conn, FactorSpec(**REVERSAL), campaign_id="c-web")
    client = TestClient(create_app(panel_settings))
    auth = _load_credentials(panel_settings)
    assert (panel_settings.hot_root / "web.credentials").stat().st_mode & 0o077 == 0
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/overview", auth=(auth[0], "wrong")).status_code == 401
    overview = client.get("/api/overview", auth=auth).json()
    assert overview["campaigns"][0]["campaign_id"] == "c-web" and overview["ledger"]["completed_trials"] == 1
    campaign = client.get("/api/campaigns/c-web", auth=auth).json()
    assert campaign["funnel"]["counts"]["submitted"] == 1 and len(campaign["intensity"]) == 1
    factors = client.get("/api/factors", auth=auth).json()
    assert factors["factors"][0]["factor_id"] == result["factor_id"]
    detail = client.get(f"/api/factors/{result['factor_id']}", auth=auth).json()
    assert detail["trials"][0]["evidence_tier"] == "dev" and detail["state_history"]
    ledger = client.get("/api/ledger", auth=auth).json()
    assert ledger["verify"]["ok"] and ledger["trials"]
    assert client.get("/api/campaigns/missing", auth=auth).status_code == 404
    assert client.get("/api/artifacts/..%2F..%2Fetc/report", auth=auth).status_code in (400, 404)
    assert client.get("/api/artifacts/not-an-artifact/report", auth=auth).status_code == 400


def test_web_can_run_without_login(panel_settings):
    connect(panel_settings.state_db).close()
    client = TestClient(create_app(panel_settings, require_auth=False))
    assert client.get("/api/overview").status_code == 200


def test_web_status_reports_read_only_counts_and_backup_metadata(panel_settings):
    conn = connect(panel_settings.state_db)
    conn.execute("INSERT INTO strategy_holdout_requests (request_id, mandate, task_id, trial_id, config_hash,"
                 " status, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 ("req-test", "A", "task-test", "S-000000000001", "hash", "pending", "human", "2026-01-01"))
    conn.commit()
    conn.close()
    log = panel_settings.hot_root / "logs" / "daily-update.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"status": "ok", "data": {"end": "2026-01-02", "daily": {"new_rows": 3}}}) + "\n")
    backups = panel_settings.backups_dir
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / "alphasieve-20260102T030405Z.db"
    backup.write_bytes(b"synthetic backup")
    backup.with_suffix(".json").write_text(json.dumps({"ledger_rows": 7, "path": "/private/path"}))
    client = TestClient(create_app(panel_settings, require_auth=False))
    status = client.get("/api/status").json()
    assert status["running_campaigns"] == 0
    assert status["latest_trade_date"] == "2026-01-02"
    assert status["latest_backup"] == {"created_at": "2026-01-02T03:04:05+00:00",
                                       "size_bytes": len(b"synthetic backup"), "ledger_rows": 7}
    assert status["inbox"] == {"open_requests": 0, "request_campaigns": [],
                                "pending_factor_holdout": 0, "factor_holdout_campaigns": [],
                                "pending_strategy_holdout": 1, "open_reviews": 0, "review_campaigns": []}
    assert "/private/path" not in json.dumps(status)


def test_web_mandate_and_strategy_views(panel_settings, tmp_path, capsys):
    from test_training import task_dict, write_task

    from alphasieve.cli.main import main

    path = write_task(tmp_path, task_dict())
    assert main(["train", "run", "--task", path, "--processes", "1", "--threads", "1", "--json"]) == 0
    trial_id = json.loads(capsys.readouterr().out)["data"]["trial_id"]
    client = TestClient(create_app(panel_settings))
    auth = _load_credentials(panel_settings)
    assert client.get("/api/mandates").status_code == 401
    mandates = {m["mandate"]: m for m in client.get("/api/mandates", auth=auth).json()["mandates"]}
    a = mandates["A"]
    assert a["dev_trials"] == 1 and a["holdout_reads"] == {"used": 0, "budget": 1}
    assert a["trials"][0]["trial_id"] == trial_id and a["trials"][0]["outcome"].startswith("dev_")
    assert a["trials"][0]["acceptance"]["checks"]
    detail = client.get(f"/api/strategy/{trial_id}", auth=auth).json()
    assert [r["record_kind"] for r in detail["records"]] == ["started", "completed"]
    assert "checks" in detail["detail"]["acceptance"] and "bundle" not in detail["detail"]
    assert detail["detail"]["task"]["task_id"] == "t_small"
    assert client.get("/api/strategy/S-000000000000", auth=auth).status_code == 404
    assert client.get("/api/strategy/..%2Fetc", auth=auth).status_code in (400, 404)
