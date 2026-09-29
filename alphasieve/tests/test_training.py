import json
from dataclasses import replace

import numpy as np
import pytest
import yaml

from alphasieve.cli.main import main
from alphasieve.data.access import load_panel
from alphasieve.errors import AlphaSieveError
from alphasieve.ledger import verify_ledger
from alphasieve.state import connect
from alphasieve.training import holdout, samples
from alphasieve.training import run as tr
from alphasieve.training.engine import retrain_points, walk_forward
from alphasieve.training.task import parse_task

BASE = {
    "task_id": "t_small", "mandate": "A", "universe_train": "csi800", "universe_predict": "csi500",
    "label": {"kind": "regression_residual", "horizons": [5], "formula_version": "v1",
              "neutralize": ["industry", "log_circ_mv"]},
    "sample": {"frequency": "daily", "min_names_per_date": 20, "min_train_rows": 500, "purge_days": 6,
               "embargo_days": 6},
    "features": {"factor_refs": [], "panel_fields": ["turnover_rate", "pb_mrq", "ret_1d", "circ_mv"],
                 "min_feature_coverage": 0.5},
    "split": {"train_years": 1, "warmup_years": 0.5, "retrain": "monthly", "select_every": "never"},
    "models": [{"family": "ridge", "grid": {"alpha": [10.0]}}],
    "output": {"score_field": "score"},
    "portfolio": {"kind": "index_enhancement", "benchmark": "zz500", "name_cap": 0.05, "industry_dev": 0.1,
                  "size_limit": 1.0, "turnover_cap": 0.5, "aum": 1e8},
    "platform": {"processes": 2},
}


def task_dict(**changes):
    data = json.loads(json.dumps(BASE))
    for key, value in changes.items():
        section, _, field = key.partition("__")
        if field:
            data[section][field] = value
        else:
            data[section] = value
    return data


@pytest.mark.parametrize("changes, message", [
    ({"sample__purge_days": 3}, "purge_days"),
    ({"label__kind": "event_car"}, "mandate A needs"),
    ({"description": "tune on the holdout window"}, "holdout"),
    ({"models": [{"family": "lgbm", "grid": {"num_leaves": [7, 15, 31, 63], "min_child_samples": [1, 2, 3, 4]}}]},
     "max_configs"),
    ({"search": {"seeds": [0, 1, 2, 3]}}, "seeds"),
])
def test_task_rules(changes, message):
    with pytest.raises(AlphaSieveError, match=message):
        parse_task(task_dict(**changes))


def test_task_candidates_and_hash():
    task = parse_task(task_dict(models=[{"family": "ridge", "grid": {"alpha": [1.0, 10.0]}},
                                        {"family": "lgbm", "grid": {"num_leaves": [15]}}]))
    assert task.candidate_count() == 3
    assert [c["family"] for c in task.candidates()] == ["ridge", "ridge", "lgbm"]
    assert task.config_hash != parse_task(task_dict()).config_hash
    d = task_dict(mandate="D", label={**BASE["label"], "kind": "residual_plus_basis"})
    d["portfolio"] = {**BASE["portfolio"], "kind": "futures_hedged", "basis_head": "enabled"}
    with pytest.raises(AlphaSieveError, match="futures"):
        parse_task(d)


def test_residualize_removes_industry_and_size():
    rng = np.random.default_rng(0)
    n = 300
    ind = rng.integers(0, 5, n)
    size = rng.normal(size=(1, n))
    y = (ind * 0.3 + 2.0 * size[0] + rng.normal(size=n))[None, :]
    res = samples.residualize(y, np.ones((1, n), bool), ind, [size], min_names=50)[0]
    assert abs(np.corrcoef(res, size[0])[0, 1]) < 1e-8
    for g in range(5):
        assert abs(res[ind == g].mean()) < 1e-8


def test_training_scores_do_not_see_future_labels(panel_settings):
    dev = load_panel(panel_settings, "dev", role="system")
    task = parse_task(task_dict())
    frames = {f: dev.wide(f) for f in task.features.panel_fields}
    beta = samples.rolling_beta(dev, samples.benchmark_returns(dev, "zz500"))
    window = dev.window_mask().to_numpy()

    def run(panel):
        table = samples.build_cross_sectional(panel, frames, task, beta)
        return table, walk_forward(table, panel.dates, window, task, processes=1, threads=1)

    table, first = run(dev)
    assert first["fits"] > 0
    points = retrain_points(dev.dates, window, "monthly", 0.5)
    k = len(points) // 2
    start, stop = points[k], points[k + 1]
    label = dev.wide("label_5d").copy()
    cut = dev.dates[start - task.sample.purge_days]
    label.loc[label.index >= cut] = np.random.default_rng(1).normal(size=label.loc[label.index >= cut].shape)
    dev.set_wide("label_5d", label)
    table2, second = run(dev)
    rows = (table.date_pos >= start) & (table.date_pos < stop) & table.predict
    np.testing.assert_allclose(first["score"][rows], second["score"][rows])


def write_task(tmp_path, data):
    path = tmp_path / f"{data['task_id']}.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(path)


def test_train_run_records_a_strategy_trial(panel_settings, tmp_path, capsys):
    data = task_dict(models=[{"family": "ridge", "grid": {"alpha": [1.0, 10.0]}}],
                     split={**BASE["split"], "select_every": "yearly", "inner_folds": 2})
    path = write_task(tmp_path, data)
    assert main(["train", "run", "--task", path, "--processes", "1", "--threads", "1", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)["data"]
    assert out["trial_id"].startswith("S-") and out["artifact_id"]
    assert out["portfolio"]["execution"]["benchmark"] == "cap_weighted_universe"
    assert out["portfolio"]["execution_vs_price_index"]["benchmark"] == "zz500"
    assert set(out["portfolio"]["acceptance"]["checks"]) >= {"annual_excess", "information_ratio"}
    conn = connect(panel_settings.state_db)
    rows = conn.execute("SELECT record_kind, layer, scope, outcome FROM trials WHERE trial_id = ? ORDER BY seq",
                        (out["trial_id"],)).fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [("started", "strategy", "A"), ("completed", "strategy", "A")]
    assert verify_ledger(conn)["ok"]
    assert (panel_settings.store_root / "models" / "t_small" / out["trial_id"] / "scores.parquet").exists()


def test_strategy_holdout_is_human_only_and_budgeted(panel_settings, tmp_path, capsys):
    path = write_task(tmp_path, task_dict())
    assert main(["train", "run", "--task", path, "--processes", "1", "--threads", "1", "--json"]) == 0
    trial_id = json.loads(capsys.readouterr().out)["data"]["trial_id"]
    conn = connect(panel_settings.state_db)
    human = replace(panel_settings, role="human")
    with pytest.raises(AlphaSieveError, match="human-only"):
        holdout.request_read(conn, replace(panel_settings, role="system"), trial_id)
    req = holdout.request_read(conn, human, trial_id)
    with pytest.raises(AlphaSieveError, match="human-only"):
        holdout.approve_read(conn, replace(panel_settings, role="agent"), req["request_id"], "no")
    result = holdout.approve_read(conn, human, req["request_id"], "test read", processes=1)
    assert result["status"] == "approved"
    row = conn.execute("SELECT evidence_tier, layer FROM trials WHERE trial_id = ? AND record_kind = 'completed'",
                       (result["holdout_trial"],)).fetchone()
    assert tuple(row) == ("holdout", "strategy")
    again = holdout.request_read(conn, human, trial_id)
    with pytest.raises(AlphaSieveError, match="already used"):
        holdout.approve_read(conn, human, again["request_id"], "second read")
    assert verify_ledger(conn)["ok"]


def test_bundle_freezes_features(panel_settings):
    task = parse_task(task_dict())
    bundle = tr.make_bundle(task, [{"name": "f", "canonical": "rank(ret_1d)", "candidate_hash": "x",
                                    "direction": 1}], "S-1")
    assert bundle["config_hash"] == task.config_hash and len(bundle["feature_version"]) == 12
    assert parse_task(bundle["task"]).config_hash == task.config_hash


def test_void_only_removes_duplicated_results(panel_settings, tmp_path, capsys):
    from alphasieve.ledger.ledger import void_duplicate_result

    path = write_task(tmp_path, task_dict())
    assert main(["train", "run", "--task", path, "--processes", "1", "--threads", "1", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)["data"]
    conn = connect(panel_settings.state_db)
    seq = conn.execute("SELECT seq FROM trials WHERE trial_id = ? AND record_kind = 'completed'",
                       (out["trial_id"],)).fetchone()[0]
    with pytest.raises(ValueError, match="only result"):
        void_duplicate_result(conn, out["trial_id"], seq, "try to erase", "human")
    task = parse_task(yaml.safe_load(open(path)))
    with pytest.raises(AlphaSieveError, match="already has a result"):
        tr.complete_trial(conn, panel_settings, task, out["trial_id"], {"manifest": {"window": ["a", "b"]}})
    assert verify_ledger(conn)["ok"]


def test_event_detection_counts_each_period_once():
    import pandas as pd

    from alphasieve.data.access import Panel
    from alphasieve.training.events import detect_events

    dates = pd.bdate_range("2020-01-01", periods=8)
    long = pd.DataFrame({"date": list(dates) * 2, "code": ["sh.600000"] * 8 + ["sz.000001"] * 8})
    long["ws_stat_date"] = (["2019-09-30"] * 3 + ["2019-12-31"] * 3 + [None, "2019-12-31"]
                            + [None] * 4 + ["2019-12-31"] * 4)
    long["fc_age"] = [np.nan] * 8 + [np.nan, 0, 1, 2, np.nan, np.nan, 0, 1]
    meta = {"tier": "dev", "signature": "x", "window": {"start": "2020-01-01", "end": "2020-12-31"}}
    ev = detect_events(Panel(long, meta), ["ws_report", "forecast"])
    got = sorted(zip(ev["t"], ev["c"], ev["type"], strict=True))
    assert got == [(0, 0, "ws_report"), (1, 1, "forecast"), (3, 0, "ws_report"), (4, 1, "ws_report"),
                   (6, 1, "forecast")]


def test_etf_relative_labels_sum_to_zero():
    import pandas as pd

    from alphasieve.data.access import Panel
    from alphasieve.training.etf import relative_labels

    dates = pd.bdate_range("2021-01-01", periods=3)
    long = pd.DataFrame({"date": list(dates) * 3, "code": ["a"] * 3 + ["b"] * 3 + ["c"] * 3,
                         "label_5d": [0.1, 0.2, np.nan, 0.0, 0.1, 0.3, -0.1, np.nan, 0.3]})
    panel = Panel(long, {"tier": "dev", "signature": "x", "window": {"start": "2021-01-01", "end": "2021-12-31"}})
    rel = relative_labels(panel, np.ones((3, 3), bool), [5])[5]
    np.testing.assert_allclose(np.nansum(rel, axis=1), 0.0, atol=1e-12)
    assert np.isnan(rel[1, 2]) and rel[0, 0] == pytest.approx(0.1)
