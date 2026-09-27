import json
import shutil
from dataclasses import replace

import pytest
import yaml
from fixtures.campaign import campaign_spec, start_campaign

from alphasieve.agents import orchestrator
from alphasieve.agents.executors import FakeExecutor
from alphasieve.campaigns import lifecycle, memory, service
from alphasieve.config import get_settings
from alphasieve.contracts import FactorSpec
from alphasieve.errors import AlphaSieveError
from alphasieve.evaluation.evaluate import evaluate_spec
from alphasieve.gates.l3 import benjamini_hochberg, deflated_sharpe, expected_max_sharpe
from alphasieve.ledger import verify_ledger
from alphasieve.state import connect

REVERSAL = {"name": "rev_3d_excess", "expression": "ts_sum(excess_ret_1d, 3)", "direction": -1,
            "hypothesis": "short-term reversal", "cell": {"domain": "price", "form": "reversal", "scale": "short"}}
NOISE = {"name": "vol_level", "expression": "cs_rank(volume)", "direction": 1, "hypothesis": "noise",
         "cell": {"domain": "volume", "form": "level", "scale": "short"}}
MOMENTUM = {"name": "mom_20d", "expression": "ts_sum(ret_1d, 20)", "direction": 1, "hypothesis": "momentum",
            "cell": {"domain": "price", "form": "change_momentum", "scale": "medium"}}
TURNOVER = {"name": "turnover_20d", "expression": "ts_mean(turnover_rate, 20)", "direction": -1, "hypothesis": "t",
            "cell": {"domain": "turnover_liquidity", "form": "level", "scale": "medium"}}
VOLATILITY = {"name": "vol_20d", "expression": "ts_std(ret_1d, 20)", "direction": -1, "hypothesis": "v",
              "cell": {"domain": "price", "form": "volatility_stability", "scale": "medium"}}


@pytest.fixture
def lenient(panel_settings, tmp_path, monkeypatch):
    configs = tmp_path / "configs"
    shutil.copytree(panel_settings.config_dir, configs)
    policy = yaml.safe_load((configs / "gate_policy.yaml").read_text())
    policy["l2"].update({"min_neutral_ratio": -10.0, "min_cost_adjusted_excess": -1.0, "min_marginal_ic": -1.0,
                         "min_subwindows_same_sign": 1})
    (configs / "gate_policy.yaml").write_text(yaml.safe_dump(policy))
    monkeypatch.setenv("ALPHASIEVE_CONFIG_DIR", str(configs))
    return get_settings()


def as_role(settings, role):
    return replace(settings, role=role)


def spec_script(turn_specs: list[list[dict]]):
    def script(ctx, env, run_cli, result):
        for i, spec in enumerate(turn_specs[(ctx.turn_index - 1) % len(turn_specs)]):
            path = ctx.workspace / "candidates" / f"t{ctx.turn_index}_{i}.yaml"
            path.write_text(yaml.safe_dump(spec))
            run_cli("factor", "eval", str(path))
        return "SUMMARY: evaluated\nINSIGHTS:\n- reversal in excess returns is strong\n- raw volume level is noise"
    return script


def fake_executors(settings, script):
    ex = FakeExecutor(settings, script)
    return lambda harness: ex


def test_l3_statistics():
    assert expected_max_sharpe(1, 0.01) == 0.0
    assert 0 < expected_max_sharpe(10, 0.01) < expected_max_sharpe(100, 0.01) < expected_max_sharpe(100, 0.04)
    assert deflated_sharpe(0.3, 0.0, 200) > 0.99
    assert deflated_sharpe(0.1, 0.3, 200) < 0.01
    bh = benjamini_hochberg({"a": 0.001, "b": 0.02, "c": 0.04, "d": 0.5}, 0.10)
    assert [bh[k]["passed"] for k in "abcd"] == [True, True, True, False]


def test_campaign_validation_and_transitions(panel_settings):
    conn = connect(panel_settings.state_db)
    human = as_role(panel_settings, "human")
    with pytest.raises(AlphaSieveError) as exc:
        service.create_campaign(conn, human, campaign_spec("bad-domains", domains=["price", "astrology"]))
    assert exc.value.code == "VALIDATION_ERROR"
    service.create_campaign(conn, human, campaign_spec("c-one"))
    with pytest.raises(AlphaSieveError):
        service.create_campaign(conn, human, campaign_spec("c-one"))
    with pytest.raises(AlphaSieveError) as exc:
        service.set_status(conn, human, "c-one", "concluded")
    assert exc.value.code == "CONFLICT"
    assert service.set_status(conn, human, "c-one", "running")["started_at"]


def test_agent_evaluation_constraints(panel_settings, monkeypatch):
    start_campaign(panel_settings, campaign_spec("c-rules", domains=["price"], budgets={"trials": 2}), monkeypatch)
    conn = connect(panel_settings.state_db)
    agent = replace(get_settings(), role="agent")
    no_campaign = replace(agent, campaign=None)
    with pytest.raises(AlphaSieveError) as exc:
        evaluate_spec(no_campaign, conn, FactorSpec(**REVERSAL))
    assert exc.value.code == "PERMISSION_DENIED"
    outside = evaluate_spec(agent, conn, FactorSpec(**NOISE), campaign_id="c-rules")
    assert outside["outcome"] == "validation_failed"
    assert outside["gates"]["l0"]["checks"][0]["name"] == "campaign_domain"
    evaluate_spec(agent, conn, FactorSpec(**REVERSAL), campaign_id="c-rules")
    with pytest.raises(AlphaSieveError) as exc:
        evaluate_spec(agent, conn, FactorSpec(**MOMENTUM), campaign_id="c-rules")
    assert exc.value.code == "BUDGET_EXHAUSTED"
    service.set_status(conn, as_role(agent, "human"), "c-rules", "paused")
    with pytest.raises(AlphaSieveError) as exc:
        evaluate_spec(agent, conn, FactorSpec(**MOMENTUM), campaign_id="c-rules")
    assert exc.value.code == "CONFLICT"


def test_turn_allowance(panel_settings, monkeypatch):
    start_campaign(panel_settings, campaign_spec("c-ceiling"), monkeypatch)
    conn = connect(panel_settings.state_db)
    other = replace(get_settings(), role="agent", turn="c-ceiling-t001", turn_allowance=5)
    evaluate_spec(other, conn, FactorSpec(**VOLATILITY), campaign_id="c-ceiling")
    agent = replace(get_settings(), role="agent", turn="c-ceiling-t002", turn_allowance=1)
    evaluate_spec(agent, conn, FactorSpec(**REVERSAL), campaign_id="c-ceiling")
    with pytest.raises(AlphaSieveError) as exc:
        evaluate_spec(agent, conn, FactorSpec(**MOMENTUM), campaign_id="c-ceiling")
    assert exc.value.code == "BUDGET_EXHAUSTED"


def test_directive_leak_check(panel_settings):
    start_campaign(panel_settings, campaign_spec("c-directive"))
    conn = connect(panel_settings.state_db)
    human = as_role(panel_settings, "human")
    assert service.add_directive(conn, human, "c-directive", "prioritize", "try growth x quality")["status"]
    for text in ("the holdout liked reversal", "前瞻结果不错"):
        with pytest.raises(AlphaSieveError) as exc:
            service.add_directive(conn, human, "c-directive", "hint", text)
        assert exc.value.code == "VALIDATION_ERROR"


def test_orchestrator_runs_until_budget_then_concludes(panel_settings):
    spec = campaign_spec("c-orch", budgets={"trials": 5, "turns": 10})
    start_campaign(panel_settings, spec)
    system = as_role(panel_settings, "system")
    out = orchestrator.run_campaign(system, "c-orch",
                                    executor_for=fake_executors(system, spec_script([[REVERSAL, NOISE], [MOMENTUM],
                                                                                     [TURNOVER, VOLATILITY]])))
    assert [t["status"] for t in out["turns"]] == ["completed", "completed", "completed"]
    assert out["outcome"]["concluded"] == "trial_budget_exhausted"
    conn = connect(panel_settings.state_db)
    campaign = service.get_campaign(conn, "c-orch")
    assert campaign["status"] in ("awaiting_holdout_approval", "concluded")
    assert campaign["memory_frozen_at"]
    assert not memory.add_insight(conn, "c-orch", "late insight")
    assert len(service.completed_trials(conn, "c-orch")) == 5
    assert memory.insights(conn, "c-orch")
    ws = system.workspaces_dir / "c-orch"
    assert (ws / "program.md").exists() and (ws / ".git").exists()
    assert (system.reports_dir / "c-orch" / "status.md").exists()
    assert verify_ledger(conn)["ok"]


def test_red_team_role_spoofing_is_detected(panel_settings):
    start_campaign(panel_settings, campaign_spec("c-redteam"))
    system = as_role(panel_settings, "system")

    def script(ctx, env, run_cli, result):
        denied = run_cli("holdout", "list")
        assert denied["error"]["code"] == "PERMISSION_DENIED"
        run_cli("holdout", "list", extra_env={"ALPHASIEVE_ROLE": "human"})
        return "SUMMARY: tried\nINSIGHTS:\n- nothing"

    out = orchestrator.run_campaign(system, "c-redteam", executor_for=fake_executors(system, script))
    assert out["turns"][0]["status"] == "integrity_violation"
    conn = connect(panel_settings.state_db)
    assert service.get_campaign(conn, "c-redteam")["status"] == "paused"
    assert memory.insights(conn, "c-redteam") == []


def test_red_team_direct_access_is_detected(panel_settings):
    start_campaign(panel_settings, campaign_spec("c-redteam2"))
    system = as_role(panel_settings, "system")

    def script(ctx, env, run_cli, result):
        result.commands.append("sqlite3 /data/alphasieve/state/alphasieve.db 'select * from trials'")
        result.reads.append("/data/alphasieve/data/panel/holdout/panel.parquet")
        return "SUMMARY: x"

    out = orchestrator.run_campaign(system, "c-redteam2", executor_for=fake_executors(system, script))
    turn = out["turns"][0]
    assert turn["status"] == "integrity_violation"
    assert "direct database access" in turn["error"] and "holdout or fresh panel path" in turn["error"]


def test_failed_turns_pause_campaign(panel_settings):
    start_campaign(panel_settings, campaign_spec("c-fail"))
    system = as_role(panel_settings, "system")

    def script(ctx, env, run_cli, result):
        raise RuntimeError("harness crashed")

    out = orchestrator.run_campaign(system, "c-fail", executor_for=fake_executors(system, script))
    assert [t["status"] for t in out["turns"]] == ["failed"] * 3
    assert out["outcome"] == {"paused": "consecutive failed turns"}
    conn = connect(panel_settings.state_db)
    service.set_status(conn, as_role(panel_settings, "human"), "c-fail", "running", "outage over")
    again = orchestrator.run_campaign(system, "c-fail", executor_for=fake_executors(system, script))
    assert [t["status"] for t in again["turns"]] == ["failed"] * 3


def test_holdout_chain(lenient, monkeypatch, capsys):
    spec = campaign_spec("c-holdout", budgets={"trials": 3, "turns": 5, "holdout_reads": 1})
    start_campaign(lenient, spec)
    system = as_role(lenient, "system")
    out = orchestrator.run_campaign(system, "c-holdout",
                                    executor_for=fake_executors(system, spec_script([[REVERSAL, NOISE, MOMENTUM]])))
    assert out["outcome"]["concluded"] == "trial_budget_exhausted"
    members = out["outcome"]["members"]
    assert [m["factor_id"] for m in members] and all(m["dsr"] > 0.95 for m in members)
    request_id = out["outcome"]["holdout_request"]["request_id"]
    conn = connect(lenient.state_db)
    assert service.get_campaign(conn, "c-holdout")["status"] == "awaiting_holdout_approval"

    from alphasieve.cli.main import main
    monkeypatch.setenv("ALPHASIEVE_ROLE", "agent")
    monkeypatch.setenv("ALPHASIEVE_CAMPAIGN", "c-holdout")
    assert main(["holdout", "approve", request_id, "--reason", "self", "--json"]) == 4
    capsys.readouterr()
    assert main(["campaign", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["data"]
    assert status["campaign"]["status"] == "concluding" and "holdout_requests" not in status
    assert "holdout_passed" not in status["funnel"]["counts"]

    with pytest.raises(AlphaSieveError) as exc:
        lifecycle.approve_holdout(conn, system, request_id, "system may not approve")
    assert exc.value.code == "PERMISSION_DENIED"
    human = as_role(lenient, "human")
    result = lifecycle.approve_holdout(conn, human, request_id, "looks reasonable")
    outcomes = {r["name"]: r["outcome"] for r in result["results"]}
    assert outcomes["rev_3d_excess"] == "holdout_passed"
    holdout_trials = conn.execute("SELECT created_by, evidence_tier FROM trials WHERE evidence_tier = 'holdout'"
                                  " AND record_kind = 'completed'").fetchall()
    assert holdout_trials and all(r["created_by"] == "system" for r in holdout_trials)
    with pytest.raises(AlphaSieveError):
        lifecycle.approve_holdout(conn, human, request_id, "again")
    with pytest.raises(AlphaSieveError) as exc:
        lifecycle.create_holdout_request(conn, human, "c-holdout", "S-other")
    assert exc.value.code == "BUDGET_EXHAUSTED"

    packet = result["review_packets"][0]
    factor_id = packet["factor"].split("@")[0]
    capsys.readouterr()
    assert main(["factor", "show", factor_id, "--json"]) == 0
    shown = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["data"]
    assert shown["state"] == "batch_concluded"
    assert all(t["metrics"].get("evidence_tier", "dev") == "dev" for t in shown["dev_trials"])

    with pytest.raises(AlphaSieveError):
        lifecycle.decide_review(conn, system, packet["packet_id"], "approved_for_shadow", "no")
    decided = lifecycle.decide_review(conn, human, packet["packet_id"], "approved_for_shadow", "clean mechanism")
    assert decided["decision_id"]
    state = conn.execute("SELECT state FROM factor_specs WHERE factor_id = ?", (factor_id,)).fetchone()["state"]
    assert state == "approved_for_shadow"
    with pytest.raises(AlphaSieveError):
        lifecycle.decide_review(conn, human, packet["packet_id"], "rejected", "changed mind")
    assert verify_ledger(conn)["ok"]


def test_agent_duplicate_candidate_is_rejected(panel_settings, monkeypatch):
    start_campaign(panel_settings, campaign_spec("c-dup"), monkeypatch)
    conn = connect(panel_settings.state_db)
    agent = replace(get_settings(), role="agent")
    evaluate_spec(agent, conn, FactorSpec(**REVERSAL), campaign_id="c-dup")
    renamed = FactorSpec(**{**REVERSAL, "name": "same_idea_new_name", "expression": "ts_sum(excess_ret_1d,3)"})
    with pytest.raises(AlphaSieveError) as exc:
        evaluate_spec(agent, conn, renamed, campaign_id="c-dup")
    assert exc.value.code == "CONFLICT"
    assert len(service.completed_trials(conn, "c-dup")) == 1


def test_codex_sandbox_covers_agent_write_paths(panel_settings, tmp_path):
    import json as _json
    from pathlib import Path

    from alphasieve.agents.executors import CodexExecutor, TurnContext
    from alphasieve.evaluation.slots import evaluation_slot

    ctx = TurnContext("c", "c-t001", 1, tmp_path, "p", "m", None, 60, tmp_path / "t.jsonl", 1)
    cmd = CodexExecutor(panel_settings).command(ctx)
    roots = [Path(p) for p in _json.loads(next(a for a in cmd if a.startswith("sandbox_workspace_write.writable_roots"))
                                         .split("=", 1)[1])]
    within = lambda p: any(Path(p).resolve().is_relative_to(r.resolve()) for r in roots)  # noqa: E731
    assert within(panel_settings.state_db) and within(panel_settings.artifacts_dir / "x")
    assert within(panel_settings.cache_dir / "library")
    with evaluation_slot(panel_settings):
        locks = list((panel_settings.state_db.parent / "locks").glob("eval-slot-*.lock"))
    assert locks and all(within(p) for p in locks)


def test_unavailable_agent_is_disabled_and_skipped(panel_settings):
    spec = campaign_spec("c-quota", budgets={"trials": 50, "turns": 4},
                         agents=[{"harness": "codex", "model": "a"}, {"harness": "claude", "model": "b"}])
    start_campaign(panel_settings, spec)
    system = as_role(panel_settings, "system")
    calls = []

    class Broken:
        def run(self, ctx):
            calls.append("claude")
            from alphasieve.agents.executors import TurnResult
            return TurnResult(status="failed", error='API Error: 401 {"type":"UserBudgetExhausted"}')

    class Working:
        def run(self, ctx):
            calls.append("codex")
            from alphasieve.agents.executors import TurnResult
            return TurnResult(status="completed", summary="SUMMARY: nothing")

    out = orchestrator.run_campaign(system, "c-quota",
                                    executor_for=lambda h: Broken() if h == "claude" else Working())
    assert calls == ["codex", "claude", "codex", "codex"]
    conn = connect(panel_settings.state_db)
    assert "claude" in service.get_campaign(conn, "c-quota")["stats"]["disabled_agents"]
    assert out["outcome"]["concluded"] == "turn_budget_exhausted"


def test_parallel_lanes_split_cells_and_allowances(panel_settings):
    cells = [{"domain": "price", "form": "reversal", "scale": "short"},
             {"domain": "price", "form": "change_momentum", "scale": "medium"},
             {"domain": "turnover_liquidity", "form": "level", "scale": "medium"}]
    spec = campaign_spec("c-lanes", cells=cells, lanes=3, budgets={"trials": 6, "turns": 6})
    start_campaign(panel_settings, spec)
    system = as_role(panel_settings, "system")
    seen = []
    lane_specs = {0: [REVERSAL, NOISE, VOLATILITY], 1: [MOMENTUM], 2: [TURNOVER]}

    def script(ctx, env, run_cli, result):
        lane = int(ctx.workspace.name.split("-")[-1])
        seen.append((lane, ctx.trial_allowance, (ctx.workspace / "brief.md").read_text()))
        for i, s in enumerate(lane_specs[lane]):
            path = ctx.workspace / "candidates" / f"l{lane}_{ctx.turn_index}_{i}.yaml"
            path.write_text(yaml.safe_dump({**s, "name": f"{s['name']}_t{ctx.turn_index}"}))
            run_cli("factor", "eval", str(path))
        return "SUMMARY: lane"

    out = orchestrator.run_campaign(system, "c-lanes", max_turns=3,
                                    executor_for=fake_executors(system, script))
    assert sorted(t["lane"] for t in out["turns"]) == [0, 1, 2]
    assert all(allow == 2 for _, allow, _ in seen)
    brief0 = next(b for lane, _, b in seen if lane == 0)
    assert "price/reversal/short" in brief0 and "turnover_liquidity/level/medium" not in brief0.split("Focus")[1][:200]
    by_lane = {t["lane"]: t["trials"] for t in out["turns"]}
    assert by_lane == {0: 2, 1: 1, 2: 1}
    conn = connect(panel_settings.state_db)
    turn_ids = {r[0] for r in conn.execute("SELECT DISTINCT turn_id FROM trials WHERE campaign_id = 'c-lanes'")}
    assert turn_ids == {t["turn_id"] for t in out["turns"]}
    assert verify_ledger(conn)["ok"]


def test_template_expansion_records_default_and_neighbours(panel_settings, monkeypatch):
    from alphasieve.evaluation.expand import FactorTemplate, evaluate_template, expand

    tmpl = FactorTemplate(name="rev_excess", expression="ts_sum(excess_ret_1d, {w})", grid={"w": [3, 5, 10]},
                          hypothesis="reversal", cell={"domain": "price", "form": "reversal", "scale": "short"},
                          direction=-1)
    assert [v["expression"] for v in expand(tmpl)] == ["ts_sum(excess_ret_1d, 3)", "ts_sum(excess_ret_1d, 5)",
                                                       "ts_sum(excess_ret_1d, 10)"]
    with pytest.raises(AlphaSieveError):
        expand(tmpl.model_copy(update={"grid": {"w": list(range(3, 40))}}))
    start_campaign(panel_settings, campaign_spec("c-expand"), monkeypatch)
    conn = connect(panel_settings.state_db)
    agent = replace(get_settings(), role="agent")
    out = evaluate_template(agent, conn, tmpl, "c-expand")
    assert out["variants"] == 3 and len(out["results"]) == 3
    default_id = out["default_factor"]
    specs = [json.loads(r["spec_json"]) for r in conn.execute(
        "SELECT spec_json FROM factor_specs WHERE name LIKE 'rev_excess_%' ORDER BY created_at")]
    assert specs[0]["params_source"] == "default"
    assert all(s["params_source"] == "neighborhood" and s["neighborhood_of"] == default_id for s in specs[1:])
    assert len(service.completed_trials(conn, "c-expand")) == 3


def test_conclude_without_holdout_budget(lenient):
    spec = campaign_spec("c-nobudget", budgets={"trials": 2, "turns": 5, "holdout_reads": 0})
    start_campaign(lenient, spec)
    system = as_role(lenient, "system")
    out = orchestrator.run_campaign(system, "c-nobudget",
                                    executor_for=fake_executors(system, spec_script([[REVERSAL, MOMENTUM]])))
    assert out["outcome"]["concluded"] == "trial_budget_exhausted"
    assert out["outcome"]["holdout_request"] is None
    conn = connect(lenient.state_db)
    assert service.get_campaign(conn, "c-nobudget")["status"] == "concluded"
