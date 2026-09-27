import json

import numpy as np
import pandas as pd
import pytest
import yaml
from fixtures.campaign import campaign_spec, start_campaign

from alphasieve.cli.main import main
from alphasieve.config import get_settings, load_config
from alphasieve.contracts import FactorSpec
from alphasieve.data.access import load_panel
from alphasieve.errors import AlphaSieveError
from alphasieve.evaluation.calibration import null_simulation, planted_detection
from alphasieve.evaluation.core import EvalInputs, l1_metrics
from alphasieve.evaluation.evaluate import evaluate_spec
from alphasieve.factors.dsl import compile_expression, evaluate
from alphasieve.ledger import ledger_stats, verify_ledger
from alphasieve.search_space import load_search_space
from alphasieve.state import connect

REVERSAL = {"name": "rev_3d_excess", "expression": "ts_sum(excess_ret_1d, 3)", "direction": -1,
            "hypothesis": "short-term reversal in excess returns",
            "cell": {"domain": "price", "form": "reversal", "scale": "short"}}


def run_cli(capsys, *argv):
    code = main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def write_spec(tmp_path, spec: dict, name="spec.yaml") -> str:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    return str(path)


@pytest.fixture
def dev(panel_settings):
    return load_panel(panel_settings, "dev")


def test_label_shuffle_kills_ic(dev, panel_settings):
    compiled = compile_expression("ts_sum(ret_1d, 5)", load_search_space(panel_settings))
    factor = -evaluate(compiled, dev)
    inputs = EvalInputs(dev, 5)
    real = l1_metrics(factor, inputs, {})["ic_mean"]
    rng = np.random.default_rng(0)
    values = inputs.label.to_numpy().copy()
    for row in values:
        rng.shuffle(row)
    shuffled = pd.DataFrame(values, index=inputs.label.index, columns=inputs.label.columns)
    inputs.label = shuffled
    inputs.valid = inputs.universe & shuffled.notna()
    fake = l1_metrics(factor, inputs, {})["ic_mean"]
    assert real > 0.03
    assert abs(fake) < 0.006


@pytest.mark.parametrize("expr", [
    "ts_rank(close, 20)", "ts_decay_linear(ret_1d, 10)", "cs_rank(ts_std(ret_1d, 20))",
    "cs_neutralize(ts_mean(turnover_rate, 5))", "group_rank(ts_corr(close, volume, 10))", "amihud_20d",
    "excess_ret_1d", "where(ret_1d > 0, volume, neg(volume))",
])
def test_truncation_invariance(dev, panel_settings, expr):
    compiled = compile_expression(expr, load_search_space(panel_settings))
    full = evaluate(compiled, dev)
    cut = dev.dates[400]
    truncated = evaluate(compiled, dev.truncated(cut))
    pd.testing.assert_frame_equal(full.loc[:cut], truncated.loc[:cut], check_freq=False)


def test_metrics_ignore_data_outside_window(dev, panel_settings):
    compiled = compile_expression("ts_sum(ret_1d, 5)", load_search_space(panel_settings))
    factor = -evaluate(compiled, dev)
    inputs = EvalInputs(dev, 5)
    base = l1_metrics(factor, inputs, {})
    start, _ = dev.window
    outside = inputs.label.index < start
    poisoned = inputs.label.copy()
    poisoned.loc[outside] = 1e6
    inputs.label = poisoned
    inputs.valid = inputs.universe & poisoned.notna()
    again = l1_metrics(factor, inputs, {})
    assert again["ic_mean"] == pytest.approx(base["ic_mean"])
    assert again["quantiles"] == pytest.approx(base["quantiles"])


def test_evaluate_records_trials_and_reaches_l2(panel_settings):
    conn = connect(panel_settings.state_db)
    spec = FactorSpec(**REVERSAL)
    first = evaluate_spec(panel_settings, conn, spec)
    assert first["gates"]["l1"]["passed"], first["gates"]["l1"]
    assert "l2" in first["gates"]
    assert first["outcome"] in ("robust_passed", "robust_failed")
    second = evaluate_spec(panel_settings, conn, spec)
    assert second["artifact_id"] == first["artifact_id"]
    assert second["factor_id"] == first["factor_id"] and second["version"] == first["version"]
    stats = ledger_stats(conn)
    assert stats["completed_trials"] == 2 and stats["distinct_candidates"] == 1
    assert verify_ledger(conn)["ok"]
    state = conn.execute("SELECT state FROM factor_specs WHERE factor_id = ?", (first["factor_id"],)).fetchone()
    assert state["state"] == first["outcome"]
    events = [json.loads(r["payload_json"])["to"] for r in conn.execute(
        "SELECT payload_json FROM events WHERE event_type = 'factor.state_changed' ORDER BY seq")]
    assert events == ["validating", "validated", "evaluating", "evaluated", "robust_evaluating", first["outcome"]]


def test_direction_must_match_hypothesis(panel_settings):
    conn = connect(panel_settings.state_db)
    wrong = FactorSpec(**{**REVERSAL, "name": "rev_wrong_sign", "direction": 1})
    result = evaluate_spec(panel_settings, conn, wrong)
    assert result["outcome"] == "evaluation_failed"
    failed = {c["name"] for c in result["gates"]["l1"]["checks"] if not c["passed"]}
    assert "ic_mean" in failed


def test_invalid_expression_is_recorded(panel_settings):
    conn = connect(panel_settings.state_db)
    bad = FactorSpec(**{**REVERSAL, "name": "peek", "expression": "ts_delay(close, -1)"})
    result = evaluate_spec(panel_settings, conn, bad)
    assert result["outcome"] == "validation_failed"
    assert ledger_stats(conn)["failure_reasons"] == {"l0.window_not_allowed": 1}


def test_agent_cannot_evaluate_on_holdout(panel_settings, monkeypatch):
    monkeypatch.setenv("ALPHASIEVE_ROLE", "agent")
    conn = connect(panel_settings.state_db)
    with pytest.raises(AlphaSieveError) as exc:
        evaluate_spec(get_settings(), conn, FactorSpec(**REVERSAL), tier="holdout")
    assert exc.value.code == "PERMISSION_DENIED"


def test_cli_workflow(panel_settings, capsys, tmp_path, monkeypatch):
    code, out = run_cli(capsys, "library", "seed")
    assert code == 0 and out["data"]["count"] == len(load_config(panel_settings, "seeds")["seeds"])
    monkeypatch.setenv("ALPHASIEVE_ROLE", "agent")
    spec_path = write_spec(tmp_path, REVERSAL)
    code, out = run_cli(capsys, "factor", "validate", spec_path)
    assert code == 0 and out["data"]["canonical"] == "ts_sum(excess_ret_1d,3)"
    code, out = run_cli(capsys, "factor", "eval", spec_path)
    assert code == 4 and "campaign" in out["error"]["message"]
    start_campaign(panel_settings, campaign_spec("cli-workflow"), monkeypatch)
    code, out = run_cli(capsys, "factor", "eval", spec_path)
    assert code in (0, 3)
    assert out["data"]["outcome"] in ("evaluation_failed", "robust_passed", "robust_failed")
    assert out["artifacts"] and out["data"]["library"]
    factor_id = out["data"]["factor_id"]
    code, out = run_cli(capsys, "factor", "show", factor_id)
    assert code == 0 and out["data"]["trial_count"] == 1
    code, out = run_cli(capsys, "library", "corr", factor_id)
    assert code == 0 and out["data"]["max_corr_with"] is not None
    code, out = run_cli(capsys, "library", "seed")
    assert code == 4
    code, out = run_cli(capsys, "ledger", "stats")
    assert out["data"]["completed_trials"] == 1
    bad_path = write_spec(tmp_path, {**REVERSAL, "expression": "ts_mean(label_5d, 5)"}, "bad.yaml")
    code, out = run_cli(capsys, "factor", "validate", bad_path)
    assert code == 2 and out["error"]["details"]["issues"][0]["name"] == "unknown_terminal"


def test_planted_signal_detection(dev, panel_settings):
    policy = load_config(panel_settings, "gate_policy")
    result = planted_detection(dev, policy, target_ics=(0.0, 0.05), trials=3)
    assert result["0.05"]["detection_rate"] == 1.0
    assert result["0.05"]["realised_ic_mean"] == pytest.approx(0.05, abs=0.01)
    assert result["0.0"]["detection_rate"] == 0.0


def test_null_simulation_pass_rate_is_low(dev, panel_settings):
    policy = load_config(panel_settings, "gate_policy")
    result = null_simulation(dev, load_search_space(panel_settings), policy, {}, {}, n=40, seed=3)
    assert result["n"] == 40
    assert result["l1_pass_rate"] <= 0.15


def test_concurrent_registration_allocates_distinct_ids(panel_settings):
    import threading

    from alphasieve.factors.registry import register

    ids, errors = [], []

    def reg(i):
        try:
            local = connect(panel_settings.state_db)
            spec = FactorSpec(**{**REVERSAL, "name": f"concurrent_{i}", "expression": f"ts_sum(ret_1d, {i + 3})"})
            ids.append(register(local, spec, f"expr{i}", f"hash{i}", "system")[0])
            local.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reg, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(set(ids)) == 12
