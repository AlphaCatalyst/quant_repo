import json

import numpy as np
import pandas as pd
import pytest
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
