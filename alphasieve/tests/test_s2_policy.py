from dataclasses import replace

from fixtures.campaign import campaign_spec, start_campaign

from alphasieve.campaigns import service
from alphasieve.config import get_settings, load_config
from alphasieve.contracts import FactorSpec
from alphasieve.evaluation.evaluate import evaluate_spec
from alphasieve.gates.policy import failed_checks, gate_l2
from alphasieve.search_space import load_search_space
from alphasieve.state import connect


def l2_metrics(excess):
    return {"subwindow_ic": [0.01, 0.02, 0.01, 0.03], "neutral": {"ic_mean": 0.02}, "ic_mean": 0.03,
            "tradable": {"annual_excess_net": excess}, "marginal": {"marginal_ic": 0.002}}


def test_cost_adjusted_excess_is_recorded_not_enforced(panel_settings):
    policy = load_config(panel_settings, "gate_policy")
    assert policy["version"] == 2
    gate = gate_l2(l2_metrics(-0.03), policy, 0, "default")
    cost = next(c for c in gate["checks"] if c["name"] == "cost_adjusted_excess")
    assert cost["informational"] and not cost["passed"]
    assert gate["passed"] and failed_checks(gate) == []
    worse = l2_metrics(-0.03)
    worse["marginal"]["marginal_ic"] = -0.001
    gate = gate_l2(worse, policy, 0, "default")
    assert not gate["passed"] and [c["name"] for c in failed_checks(gate)] == ["marginal_ic"]


def test_horizon_follows_signal_domain(panel_settings, monkeypatch):
    space = load_search_space(panel_settings)
    assert space.horizon_for(["price"]) == 5 and space.horizon_for(["price", "growth"]) == 20
    fundamental = campaign_spec("c-fundamental", cells=[{"domain": "growth", "form": "level", "scale": "quarterly"}])
    start_campaign(panel_settings, fundamental, monkeypatch)
    conn = connect(panel_settings.state_db)
    assert service.get_campaign(conn, "c-fundamental")["spec"].horizon == 20
    explicit = campaign_spec("c-explicit", horizon=10)
    service.create_campaign(conn, replace(panel_settings, role="human"), explicit)
    assert service.get_campaign(conn, "c-explicit")["spec"].horizon == 10
    agent = replace(get_settings(), role="agent")
    spec = {"name": "growth_level", "expression": "cs_rank(yoy_ni)", "direction": 1, "hypothesis": "growth",
            "cell": {"domain": "growth", "form": "level", "scale": "quarterly"}}
    wrong = evaluate_spec(agent, conn, FactorSpec(**spec, horizon=5), campaign_id="c-fundamental")
    assert wrong["outcome"] == "validation_failed"
    assert wrong["gates"]["l0"]["checks"][0]["name"] == "campaign_horizon"
    right = evaluate_spec(agent, conn, FactorSpec(**spec, horizon=20), campaign_id="c-fundamental")
    assert right["outcome"] != "validation_failed"
