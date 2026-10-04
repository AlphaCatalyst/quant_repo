"""Offline monitoring rules and append-only behavior."""
import json

import pytest
from fastapi.testclient import TestClient

from alphasieve.monitor import acknowledge, list_alerts, run
from alphasieve.state import connect
from alphasieve.web.app import create_app


def test_monitor_rules_visibility_and_idempotence(settings, tmp_path, monkeypatch):
    import alphasieve.monitor.service as svc

    thesis_dir = tmp_path / "theses"
    thesis_dir.mkdir()
    (thesis_dir / "one.yaml").write_text("""thesis_id: example-thesis
title: 示例论点
assets: [{code: 'sh.600001', name: 甲}]
status: holding
as_of: 2026-10-01
core_variables: []
argument: []
parameters: {}
evidence: []
valuation: {formula: '1', output_unit: 元}
scenarios: {}
falsifiers:
  - {id: manual, condition: 成本上升, variable: cost, action: 重估}
  - {id: market, condition: '收盘价 > 16', variable: 'sina_futures_close:LH2707', action: 重估}
position_limit: 0.2
""", encoding="utf-8")
    monkeypatch.setenv("ALPHASIEVE_THESES_DIR", str(thesis_dir))
    monkeypatch.setattr(svc, "_market_value", lambda *_: (17.0, "2026-10-04", "sina:LH2707"))
    monkeypatch.setattr(svc, "list_forecasts", lambda *_args, **_kwargs: {"forecasts": [
        {"forecast_id": fid, "spec": {"statement": fid,
          "resolver_params": {"settle_date": settle}}}
        for fid, settle in (("late", "2026-10-03"), ("soon", "2026-10-10"))]})
    monkeypatch.setattr(svc, "recent_for", lambda _s, codes, _since: [
        {"code": code, "announcement_id": f"a{code}", "published_at": "2026-10-04",
         "title": "监管措施", "importance": "high", "event_type": "regulatory_penalty"} for code in codes])
    monkeypatch.setattr(svc, "flags_for", lambda _s, codes, _asof: [
        {"code": code, "level": "red", "period": "2026-06-30",
         "rules": {"cash": {"level": "red"}}} for code in codes])
    with connect(settings.state_db) as conn:
        conn.execute("INSERT INTO holdings_snapshots VALUES (?,?,?,?,?,?,?,?)", (
            "snapshot-1", "personal", "2026-10-04", "test",
            json.dumps([{"code": "sh.600001", "market_value": 30},
                        {"code": "sh.600002", "market_value": 70}]), "hash", "human", "2026-10-04"))
        first = run(settings, conn, "2026-10-04")
        second = run(settings, conn, "2026-10-04")
        assert first["inserted"] > 0 and second["inserted"] == 0
        public = list_alerts(conn, visibility="public")
        private = list_alerts(conn, visibility="private")
        assert any(a["kind"] == "manual_check" for a in public)
        assert any(a["kind"] == "thesis_falsifier" and a["severity"] == "critical" for a in public)
        assert len([a for a in public if a["kind"] == "forecast"]) == 2
        assert any(a["kind"] == "position_cap" for a in private)
        assert any(a["subject"] == "600002" for a in private)
        assert all(a["subject"] != "600002" for a in public)
        ack = acknowledge(conn, private[0]["alert_id"], "human", "已查看")
        assert ack and all(a["alert_id"] != private[0]["alert_id"] for a in list_alerts(conn, open_only=True))
        with pytest.raises(Exception, match="append-only"):
            conn.execute("UPDATE alerts SET title='changed'")
        with pytest.raises(Exception, match="append-only"):
            conn.execute("DELETE FROM alert_acks")
    open_client = TestClient(create_app(settings, require_auth=False))
    assert open_client.get("/api/alerts").status_code == 200
    assert all(a["visibility"] == "public" for a in open_client.get("/api/alerts").json()["alerts"])
    assert open_client.get("/api/alerts/private").status_code == 403
