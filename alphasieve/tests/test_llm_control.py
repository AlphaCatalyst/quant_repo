"""Codex load balancer probe and outage state transitions."""

import json
import shutil
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from fixtures.campaign import campaign_spec

from alphasieve.agents.executors import FakeExecutor
from alphasieve.campaigns import service
from alphasieve.config import PACKAGE_CONFIG_DIR, get_settings
from alphasieve.control import llm
from alphasieve.state import connect
from alphasieve.thesis.agent_run import run_draft, show_run

SAMPLE = Path(__file__).resolve().parents[1] / "theses" / "hog-cycle-muyuan.yaml"


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        status = 200

        def do_GET(self):
            self.send_response(type(self).status)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    shutil.copytree(PACKAGE_CONFIG_DIR, tmp_path / "configs")
    config = tmp_path / "configs" / "control"
    (config / "targets.yaml").write_text(yaml.safe_dump({
        "remote_root": "/tmp", "llm_endpoints": [{"name": "codex-lb",
        "url": f"http://127.0.0.1:{server.server_port}/health"}]}), encoding="utf-8")
    monkeypatch.setenv("ALPHASIEVE_HOT_ROOT", str(tmp_path / "hot"))
    monkeypatch.setenv("ALPHASIEVE_CONFIG_DIR", str(config.parent))
    settings = replace(get_settings(), store_root=tmp_path / "store", store_mount=None)
    yield settings, Handler
    server.shutdown()
    thread.join()
    server.server_close()


def test_pause_after_two_failures_alert_once_and_recover(endpoint, monkeypatch):
    settings, handler = endpoint
    alerts = []
    monkeypatch.setattr("alphasieve.control.health.record_system_alert",
                        lambda *_args, **kwargs: alerts.append(kwargs) or False)
    conn = connect(settings.state_db)
    handler.status = 503
    first = llm.check_and_resume(settings, conn)
    assert first["state"]["consecutive_failures"] == 1
    assert first["state"]["paused_since"] is None
    second = llm.check_and_resume(settings, conn)
    assert second["state"]["paused_since"]
    assert second["state"]["consecutive_failures"] == 2
    llm.check_and_resume(settings, conn)
    assert len(alerts) == 1
    assert alerts[0]["rule"] == "llm-unavailable"
    assert alerts[0]["severity"] == "warning"
    assert json.loads((settings.hot_root / "control" / "llm.json").read_text()) == llm.state(settings)
    handler.status = 200
    recovered = llm.check_and_resume(settings, conn)
    assert recovered["state"]["available"] is True
    assert recovered["state"]["paused_since"] is None
    llm.check_and_resume(settings, conn)
    assert len(alerts) == 2
    assert alerts[1]["severity"] == "info"
    assert alerts[0]["dedupe_key"] != alerts[1]["dedupe_key"]
    conn.close()


def test_require_available_fails_without_starting_work(endpoint):
    settings, handler = endpoint
    handler.status = 503
    with pytest.raises(llm.LlmUnavailable) as exc:
        llm.require_available(settings)
    assert exc.value.code == "LLM_UNAVAILABLE"
    with pytest.raises(llm.LlmUnavailable):
        llm.require_available(settings)
    assert llm.state(settings)["paused_since"]
    handler.status = 200
    with pytest.raises(llm.LlmUnavailable):
        llm.require_available(settings)
    conn = connect(settings.state_db)
    llm.check_and_resume(settings, conn)
    llm.require_available(settings)
    conn.close()


def test_campaign_resumes_once_with_concurrency_limit(endpoint):
    settings, handler = endpoint
    conn = connect(settings.state_db)
    for name in ("first", "second"):
        service.create_campaign(conn, settings, campaign_spec(name))
        service.set_status(conn, settings, name, "running")
        service.set_status(conn, settings, name, "paused", "llm_unavailable")
        service.update_stats(conn, name, pause_reason="llm_unavailable")
    conn.commit()
    handler.status = 503
    llm.check_and_resume(settings, conn)
    llm.check_and_resume(settings, conn)
    started = []
    handler.status = 200
    result = llm.check_and_resume(settings, conn, runner=started.append, max_concurrency=1)
    assert result["campaigns"] == ["first"]
    assert started == ["first"]
    assert service.get_campaign(conn, "first")["status"] == "running"
    assert service.get_campaign(conn, "second")["status"] == "paused"
    llm.check_and_resume(settings, conn, runner=started.append, max_concurrency=1)
    assert started == ["first"]
    conn.close()


def test_recovery_resumes_interrupted_thesis_once(endpoint, monkeypatch):
    settings, handler = endpoint
    handler.status = 503
    run = run_draft(settings, "hog cycle", "codex", run_id="thesis-llm-control")
    assert run["status"] == "interrupted"

    calls = []

    def script(ctx, _env, _run_cli, _result):
        calls.append(ctx.workspace)
        (ctx.workspace / "drafts" / "proposal.yaml").write_text(SAMPLE.read_text(encoding="utf-8"))
        return "drafted"

    monkeypatch.setattr("alphasieve.thesis.agent_run.make_executor",
                        lambda _settings, _harness, _fake_script=None: FakeExecutor(settings, script))
    conn = connect(settings.state_db)
    handler.status = 200
    first = llm.check_and_resume(settings, conn, runner=lambda _id: None)
    second = llm.check_and_resume(settings, conn, runner=lambda _id: None)
    assert first["thesis_runs"] == ["thesis-llm-control"]
    assert second["thesis_runs"] == []
    assert calls == [Path(run["workspace"])]
    assert show_run(settings, run["run_id"])["status"] == "completed"
    conn.close()


def test_claude_campaign_is_not_gated_by_codex_endpoint(endpoint):
    settings, handler = endpoint
    conn = connect(settings.state_db)
    service.create_campaign(conn, settings, campaign_spec("claude-only", agents=[{"harness": "claude", "model": "x"}]))
    service.set_status(conn, settings, "claude-only", "running")
    conn.execute("INSERT INTO turns (turn_id, campaign_id, turn_index, harness, model, status, started_at)"
                 " VALUES ('claude-only-t001', 'claude-only', 1, 'claude', 'x', 'interrupted', '2026-01-01')")
    service.set_status(conn, settings, "claude-only", "paused", "llm_unavailable")
    handler.status = 503
    started = []
    resumed = llm.check_and_resume(settings, conn, runner=started.append)
    assert resumed["campaigns"] == ["claude-only"]
    assert started == ["claude-only"]
    conn.close()
