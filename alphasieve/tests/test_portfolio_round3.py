import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from test_training import BASE, task_dict, write_task

from alphasieve.cli.main import main
from alphasieve.data.access import load_panel
from alphasieve.errors import AlphaSieveError
from alphasieve.state import connect
from alphasieve.strategy import portfolio_lp
from alphasieve.training import mandates
from alphasieve.training import run as tr
from alphasieve.training.task import parse_task

LP = {**BASE["portfolio"], "construction": "lp", "name_cap": 0.02, "industry_dev": 0.05, "size_limit": 0.5}
TASKS = Path(__file__).parents[1] / "src" / "alphasieve" / "configs" / "training_tasks"


def _saved_task(name):
    return parse_task(yaml.safe_load((TASKS / f"{name}.yaml").read_text(encoding="utf-8")))


def _lp_inputs(settings):
    dev = load_panel(settings, "dev", role="system")
    members = dev.mask("in_universe").to_numpy()
    rng = np.random.default_rng(0)
    score = pd.DataFrame(rng.normal(size=(len(dev.dates), len(dev.codes))), index=dev.dates, columns=dev.codes)
    return dev, members, score


def _weights(dev, members, score, **portfolio):
    cfg = parse_task(task_dict(portfolio={**LP, **portfolio})).portfolio
    return portfolio_lp.build_weights_lp(score, dev, members, 10, cfg.industry_dev, cfg.name_cap, cfg.turnover_cap,
                                         cfg.size_limit, cfg=cfg, costs={"impact_k": 0.5})


def test_impact_envelope_touches_the_cost_at_nodes_and_bounds_it_between():
    kappa, qmax = np.array([0.8, 0.0]), np.array([0.02, 0.01])
    rows, rhs = portfolio_lp._impact_rows(kappa, qmax, 2)
    assert rows.shape[0] == portfolio_lp.SEGMENTS
    slopes = rows.toarray()[:, 2]
    q = np.linspace(0, 0.02, 81)
    envelope = np.max(slopes[:, None] * q[None, :] - rhs[:, None], axis=0)
    exact = 0.8 * q ** 1.5
    assert (envelope >= exact - 1e-15).all()
    np.testing.assert_allclose(envelope[::20], exact[::20], atol=1e-15)


def test_net_objective_trades_less_than_the_score_objective(panel_settings):
    dev, members, score = _lp_inputs(panel_settings)
    plain, _ = _weights(dev, members, score)
    net, info = _weights(dev, members, score, objective="net_alpha_pwl", alpha_return_scale=1e-5)
    assert info["objective"] == "net_alpha_pwl" and info["infeasible_dates"] == 0

    def turnover(w):
        return float(w.diff().abs().sum(axis=1).iloc[1:].mean() / 2)

    assert turnover(net) < turnover(plain)


def test_liquidity_cap_bounds_active_weights_and_reads_no_later_day(panel_settings):
    dev, members, score = _lp_inputs(panel_settings)
    kw = {"active_liquidity_adv_fraction": 0.1, "liquidity_design_aum": 1e9, "objective": "net_alpha_pwl"}
    first, info = _weights(dev, members, score, **kw)
    assert 0 < info["names_below_uniform_cap_share"] <= 1
    cut = first.index[len(first) // 2]
    later = dev.dates > cut
    for field in ("amount", "ret_1d"):
        w = dev.wide(field).astype(float).copy()
        w.loc[later] *= 5.0
        dev.set_wide(field, w)
    second, _ = _weights(dev, members, score, **kw)
    np.testing.assert_allclose(first.loc[:cut].to_numpy(), second.loc[:cut].to_numpy(), atol=1e-9)


def test_score_ema_is_causal_and_resets_on_missing():
    idx = pd.bdate_range("2020-01-01", periods=6)
    x = pd.DataFrame({"a": [1.0, 1.0, np.nan, 3.0, 3.0, 3.0], "b": [0.0, 2.0, 2.0, 2.0, 2.0, 2.0]}, index=idx)
    out = mandates.ema_scores(x, 5)
    assert np.isnan(out.iloc[2, 0]) and out.iloc[3, 0] == 3.0
    alpha = 1 - 2 ** (-1 / 5)
    assert out.iloc[1, 1] == pytest.approx(alpha * 2.0)
    np.testing.assert_allclose(mandates.ema_scores(x.iloc[:4], 5).to_numpy(), out.iloc[:4].to_numpy())


def test_frozen_scores_are_reused_without_training(panel_settings, tmp_path, capsys, monkeypatch):
    src = write_task(tmp_path, task_dict())
    assert main(["train", "run", "--task", src, "--processes", "1", "--threads", "1", "--json"]) == 0
    source = json.loads(capsys.readouterr().out)["data"]["trial_id"]
    task = parse_task(task_dict(task_id="t_frozen", score_source={"task_id": "t_small", "trial_id": source}))
    bundle = tr.make_bundle(task, [], "S-000000000001", panel_settings)
    assert bundle["features"]["score_source"]["scores_sha256"]

    def boom(*args, **kwargs):
        raise AssertionError("frozen mode must not train")

    monkeypatch.setattr(tr, "walk_forward", boom)
    monkeypatch.setattr(tr, "build_cross_sectional", boom)
    result, _ = tr.execute(panel_settings, bundle, 1, 1)
    assert result["model"] == {"reused_scores": True, "fits": 0}
    assert "robustness" in result["portfolio"]
    run_dir = next((panel_settings.store_root / "models" / "t_small").glob(f"*{source}*"))
    (run_dir / "scores.parquet").write_bytes(b"changed")
    with pytest.raises(AlphaSieveError, match="changed"):
        tr.execute(panel_settings, bundle, 1, 1)
    with pytest.raises(AlphaSieveError, match="index-enhancement"):
        parse_task(task_dict(mandate="C", label__kind="event_car", label__event_types=["ws_report"],
                             label__mask=["dedupe_same_period"],
                             score_source={"task_id": "t_small", "trial_id": source}))


def test_strategy_budget_blocks_new_trials(panel_settings, monkeypatch):
    task = parse_task(task_dict())
    conn = connect(panel_settings.state_db)
    monkeypatch.setitem(mandates.STRATEGY_TRIAL_BUDGET, "A", 1)
    tr.start_trial(conn, panel_settings, task, "S-00000000000a")
    with pytest.raises(AlphaSieveError, match="approved"):
        tr.start_trial(conn, panel_settings, task, "S-00000000000b")
    tr.start_trial(conn, panel_settings, task, "S-00000000000a-H", check_budget=False)


def test_later_fields_at_their_defaults_keep_the_config_hash():
    plain = parse_task(task_dict(portfolio=LP))
    explicit = parse_task(task_dict(portfolio={**LP, "objective": "score", "impact_segments": 4,
                                               "hedge_ratios": [1.0]}))
    assert plain.config_hash == explicit.config_hash
    assert parse_task(task_dict(portfolio={**LP, "score_ema_half_life": 5})).config_hash != plain.config_hash
    with pytest.raises(AlphaSieveError, match="need the LP"):
        parse_task(task_dict(portfolio={**BASE["portfolio"], "score_ema_half_life": 5}))


def test_cost_aware_defaults_preserve_existing_hashes_and_weights(panel_settings):
    p1 = _saved_task("a_csi500_portfolio_cost_v1")
    v4 = _saved_task("a_csi500_residual_v4")
    assert p1.config_hash == "488b1f8c2b00ddfa"
    assert v4.config_hash == "18fc9302845d11b2"
    explicit = p1.model_dump(mode="python")
    explicit["portfolio"].update(alpha_scale_mode="fixed", impact_design_aum=None)
    assert parse_task(explicit).config_hash == p1.config_hash
    for name, value in (("alpha_scale_mode", "trailing_10d"), ("impact_design_aum", 2e9)):
        changed = p1.model_dump(mode="python")
        changed["portfolio"][name] = value
        assert parse_task(changed).config_hash != p1.config_hash

    dev, members, score = _lp_inputs(panel_settings)
    old, old_info = _weights(dev, members, score, objective="net_alpha_pwl")
    default, default_info = _weights(dev, members, score, objective="net_alpha_pwl",
                                      alpha_scale_mode="fixed", impact_design_aum=None)
    np.testing.assert_array_equal(old.to_numpy(), default.to_numpy())
    assert old_info == default_info


def test_trailing_scale_uses_only_mature_labels_and_fixed_endpoint_window():
    dates = pd.bdate_range("2020-01-01", periods=310)
    z = np.broadcast_to(np.linspace(-1.0, 1.0, 140), (len(dates), 140)).copy()
    opening = 100.0 * np.exp(np.arange(len(dates))[:, None] * 0.0002 * z)
    buy_ok = np.ones_like(z, dtype=bool)
    decision = 275
    scale = portfolio_lp.trailing_alpha_scale
    original = scale(z, opening, buy_ok, dates, decision)
    assert original["valid_days"] == 252
    assert original["earliest_label_end"] == dates[decision - 251].date().isoformat()
    assert original["latest_label_end"] == dates[decision].date().isoformat()
    assert original["fallback_reason"] is None
    assert original["alpha_scale"] == pytest.approx(0.002, abs=1e-6)

    future = opening.copy()
    future[decision + 1:] *= np.exp(z[decision + 1:] * 3)
    assert scale(z, future, buy_ok, dates, decision) == original
    assert scale(z[:decision + 1], opening[:decision + 1], buy_ok[:decision + 1],
                 dates[:decision + 1], decision) == original
    for earlier in (decision - 1, decision - 20):
        assert scale(z, future, buy_ok, dates, earlier) == scale(z, opening, buy_ok, dates, earlier)

    endpoint = opening.copy()
    endpoint[decision] *= np.exp(z[decision] * 0.1)
    assert scale(z, endpoint, buy_ok, dates, decision)["alpha_scale"] > original["alpha_scale"]


def test_trailing_scale_minimum_valid_days_and_negative_clip():
    dates = pd.bdate_range("2020-01-01", periods=180)
    z = np.broadcast_to(np.linspace(-1.0, 1.0, 140), (len(dates), 140)).copy()
    opening = 100.0 * np.exp(np.arange(len(dates))[:, None] * 0.0002 * z)
    buy_ok = np.zeros_like(z, dtype=bool)
    # The final 125 mature score dates alone cannot meet the 126-day floor.
    buy_ok[180 - 11 - 125:180 - 11] = True
    result = portfolio_lp.trailing_alpha_scale(z, opening, buy_ok, dates, 179)
    assert result["valid_days"] == 125
    assert result["alpha_scale"] == 0.005
    buy_ok[180 - 11 - 126] = True
    result = portfolio_lp.trailing_alpha_scale(z, opening, buy_ok, dates, 179)
    assert result["valid_days"] == 126
    assert result["alpha_scale"] == pytest.approx(0.002, abs=1e-6)
    falling = 100.0 * np.exp(-np.arange(len(dates))[:, None] * 0.0002 * z)
    negative = portfolio_lp.trailing_alpha_scale(z, falling, buy_ok, dates, 179)
    assert negative["alpha_scale"] == 0.0
    assert negative["clipped_negative"] is True


@pytest.mark.parametrize("name,field,value", [
    ("a_csi500_portfolio_cost_scale_v2", "alpha_scale_mode", "trailing_10d"),
    ("a_csi500_portfolio_cost_capacity_v2", "impact_design_aum", 2e9),
])
def test_preregistered_cost_task_parses_and_builds_on_synthetic_panel(panel_settings, name, field, value):
    task = _saved_task(name)
    p1 = _saved_task("a_csi500_portfolio_cost_v1")
    assert task.portfolio.model_dump()[field] == value
    assert task.score_source == p1.score_source
    assert task.portfolio.aum == p1.portfolio.aum == 5e8
    dev, members, score = _lp_inputs(panel_settings)
    weights, info = portfolio_lp.build_weights_lp(
        score, dev, members, task.portfolio.rebalance_every, task.portfolio.industry_dev,
        task.portfolio.name_cap, task.portfolio.turnover_cap, task.portfolio.size_limit,
        cfg=task.portfolio, costs={"impact_k": 0.5})
    assert len(weights) > 0
    np.testing.assert_allclose(weights.sum(axis=1), 1.0, atol=1e-12)
    assert info["infeasible_dates"] == 0


def test_a_strategy_budget_is_15():
    assert mandates.STRATEGY_TRIAL_BUDGET["A"] == 15


def test_cost_aware_trial_positions_and_single_start(panel_settings, monkeypatch):
    c1 = _saved_task("a_csi500_portfolio_cost_scale_v2")
    c2 = _saved_task("a_csi500_portfolio_cost_capacity_v2")
    conn = connect(panel_settings.state_db)
    monkeypatch.setattr(tr, "strategy_trial_count", lambda *args: 13)
    with pytest.raises(AlphaSieveError, match="position"):
        tr.start_trial(conn, panel_settings, c2, "S-cost-aware-early")
    tr.start_trial(conn, panel_settings, c1, "S-cost-aware-c1")
    with pytest.raises(AlphaSieveError, match="already started"):
        tr.start_trial(conn, panel_settings, c1, "S-cost-aware-c1-again")
    monkeypatch.setattr(tr, "strategy_trial_count", lambda *args: 14)
    tr.start_trial(conn, panel_settings, c2, "S-cost-aware-c2")
    monkeypatch.setattr(tr, "strategy_trial_count", lambda *args: 15)
    with pytest.raises(AlphaSieveError, match="approved"):
        tr.start_trial(conn, panel_settings, c2, "S-cost-aware-extra")


def test_cost_aware_cost_digest_is_fixed_before_bundle(panel_settings, tmp_path):
    from dataclasses import replace
    import shutil
    import yaml

    assert len(tr._costs_hash(panel_settings)) == 64
    config_dir = tmp_path / "configs"
    shutil.copytree(panel_settings.config_dir, config_dir)
    isolated = replace(panel_settings, config_dir=config_dir)
    path = config_dir / "costs.yaml"
    costs = yaml.safe_load(path.read_text())
    costs["b3"]["impact_k"] = 0.99
    path.write_text(yaml.safe_dump(costs))
    with pytest.raises(AlphaSieveError, match="registered configuration"):
        tr._costs_hash(isolated)


def test_cost_aware_source_matches_the_locked_p1_bundle(panel_settings):
    run = panel_settings.store_root / "models" / "a_csi500_portfolio_cost_v1" / "p1-S-05cc830d4f41"
    run.mkdir(parents=True)
    (run / "scores.parquet").write_bytes(b"synthetic")
    frozen = {"task_id": "a_csi500_residual_v4", "trial_id": "S-1f2df27729ff",
              "scores_sha256": "synthetic-score", "manifest_sha256": "synthetic-manifest"}
    manifest = {"trial_id": "S-05cc830d4f41", "config_hash": "488b1f8c2b00ddfa",
                "evidence_tier": "dev", "bundle": {"features": {"score_source": frozen}}}
    (run / "manifest.json").write_text(json.dumps(manifest))
    tr._check_p1_source(panel_settings, frozen)
    with pytest.raises(AlphaSieveError, match="locked P1 bundle"):
        tr._check_p1_source(panel_settings, {**frozen, "scores_sha256": "changed"})


def test_cost_aware_fields_reject_invalid_scope_and_nonfinite_design_aum():
    p1 = _saved_task("a_csi500_portfolio_cost_v1").model_dump(mode="python")
    for value in (0.0, -1.0, float("nan"), float("inf")):
        data = json.loads(json.dumps(p1))
        data["portfolio"]["impact_design_aum"] = value
        with pytest.raises(AlphaSieveError):
            parse_task(data)
    data = json.loads(json.dumps(p1))
    data["score_source"] = None
    data["portfolio"]["alpha_scale_mode"] = "trailing_10d"
    with pytest.raises(AlphaSieveError):
        parse_task(data)


def test_capacity_design_doubles_lp_impact_without_changing_linear_fees(panel_settings, monkeypatch):
    dev, members, score = _lp_inputs(panel_settings)
    p1 = _saved_task("a_csi500_portfolio_cost_v1")
    c2 = _saved_task("a_csi500_portfolio_cost_capacity_v2")
    seen = []
    original = portfolio_lp._solve

    def capture(*args, **kwargs):
        cost = args[-1]
        if cost is not None and not seen:
            seen.append({key: value.copy() if isinstance(value, np.ndarray) else value
                         for key, value in cost.items()})
        return original(*args, **kwargs)

    monkeypatch.setattr(portfolio_lp, "_solve", capture)
    for cfg in (p1.portfolio, c2.portfolio):
        seen.clear()
        portfolio_lp.build_weights_lp(score, dev, members, 10, cfg.industry_dev, cfg.name_cap,
                                      cfg.turnover_cap, cfg.size_limit, cfg=cfg, costs={"impact_k": 0.5})
        assert seen
        if cfg is p1.portfolio:
            baseline = seen[0]
        else:
            designed = seen[0]
    np.testing.assert_allclose(designed["kappa"], 2 * baseline["kappa"], atol=1e-14)
    assert designed["c_buy"] == baseline["c_buy"]
    assert designed["c_sell"] == baseline["c_sell"]
    assert designed["alpha_scale"] == baseline["alpha_scale"] == 0.005


def test_frozen_score_trial_rescores_holdout_with_the_source_training(panel_settings, tmp_path, capsys):
    from dataclasses import replace

    from alphasieve.training import holdout

    src = write_task(tmp_path, task_dict())
    assert main(["train", "run", "--task", src, "--processes", "1", "--threads", "1", "--json"]) == 0
    source = json.loads(capsys.readouterr().out)["data"]["trial_id"]
    frozen = write_task(tmp_path, task_dict(task_id="t_frozen", score_source={"task_id": "t_small",
                                                                               "trial_id": source}))
    assert main(["train", "run", "--task", frozen, "--processes", "1", "--threads", "1", "--json"]) == 0
    trial = json.loads(capsys.readouterr().out)["data"]["trial_id"]

    conn = connect(panel_settings.state_db)
    human = replace(panel_settings, role="human")
    _, bundle = holdout._trial_bundle(conn, panel_settings, trial)
    result, outputs = tr.execute(panel_settings, bundle, 1, 1, tier="holdout")
    assert result["model"].get("reused_scores") is None and result["model"]["fits"] > 0
    assert result["features"]["score_source"]["rescored_on_tier"] == "holdout"
    assert result["manifest"]["config_hash"] == bundle["config_hash"]
    from alphasieve.training.score_source import source_bundle

    direct, direct_out = tr.execute(panel_settings, source_bundle(panel_settings, bundle["features"]["score_source"]),
                                    1, 1, tier="holdout")
    pd.testing.assert_frame_equal(outputs["scores"], direct_out["scores"])

    req = holdout.request_read(conn, human, trial)
    approved = holdout.approve_read(conn, human, req["request_id"], "test read", processes=1)
    assert approved["status"] == "approved"

    run_dir = next((panel_settings.store_root / "models" / "t_small").glob(f"*{source}*"))
    manifest = json.loads((run_dir / "manifest.json").read_text())
    manifest["bundle"]["task"]["portfolio"]["top_k"] = 7
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(AlphaSieveError, match="changed"):
        tr.execute(panel_settings, bundle, 1, 1, tier="holdout")
