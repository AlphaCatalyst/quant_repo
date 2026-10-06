"""Campaign accounting and startup behavior across model outages."""

from dataclasses import replace

from fixtures.campaign import campaign_spec, start_campaign

from alphasieve.agents import orchestrator
from alphasieve.agents.executors import TurnResult
from alphasieve.campaigns import service, stats
from alphasieve.control.llm import LlmUnavailable
from alphasieve.state import connect


class FailedCodex:
    def run(self, ctx):
        return TurnResult(status="failed", error="HTTP 503; retry: HTTP 503")


def test_turn_outage_pauses_without_budget_charge(panel_settings, monkeypatch):
    start_campaign(panel_settings, campaign_spec("llm-turn"))
    settings = replace(panel_settings, role="system")
    monkeypatch.setattr(orchestrator, "require_available", lambda _: None)
    monkeypatch.setattr(orchestrator, "probe", lambda _: {"reachable": True})
    out = orchestrator.run_campaign(settings, "llm-turn", executor_for=lambda _: FailedCodex())
    assert out["turns"][0]["status"] == "interrupted"
    assert out["outcome"] == {"paused": "llm_unavailable"}
    conn = connect(settings.state_db)
    assert service.get_campaign(conn, "llm-turn")["stats"]["pause_reason"] == "llm_unavailable"
    assert stats.budget_status(conn, "llm-turn")["turns"]["used"] == 0
    assert stats.budget_status(conn, "llm-turn")["failed_turns_streak"]["current"] == 0


def test_preflight_failure_starts_no_turn(panel_settings, monkeypatch):
    start_campaign(panel_settings, campaign_spec("llm-start", agents=[{"harness": "codex", "model": "fake"}]))
    settings = replace(panel_settings, role="system")
    calls = []

    def unavailable(_):
        calls.append(1)
        raise LlmUnavailable()

    monkeypatch.setattr(orchestrator, "require_available", unavailable)
    monkeypatch.setattr(orchestrator, "state", lambda _: {"paused_since": "now" if len(calls) >= 2 else None})
    out = orchestrator.run_campaign(settings, "llm-start", executor_for=lambda _: FailedCodex())
    assert len(calls) == 2
    assert out["outcome"] == {"paused": "llm_unavailable"}
    conn = connect(settings.state_db)
    assert conn.execute("SELECT COUNT(*) FROM turns WHERE campaign_id = 'llm-start'").fetchone()[0] == 0
