import os
import shutil
from dataclasses import replace

import pytest
import yaml
from fixtures.campaign import campaign_spec, start_campaign

from alphasieve.campaigns import service
from alphasieve.config import get_settings
from alphasieve.contracts import FactorSpec
from alphasieve.data.access import load_panel
from alphasieve.data.panel import build_panel
from alphasieve.data.sync import universe_codes
from alphasieve.errors import AlphaSieveError
from alphasieve.evaluation.evaluate import evaluate_spec
from alphasieve.state import connect


@pytest.fixture
def all_a(built_root, tmp_path, monkeypatch, synth_info):
    """A hot root whose ashare_all raw data is the synthetic raw set, with universe dates shifted to fit it."""
    root, _ = built_root
    hot = tmp_path / "hot"
    (hot / "data" / "raw").mkdir(parents=True)
    os.symlink(root / "hot" / "data" / "raw" / "baostock", hot / "data" / "raw" / "baostock_all")
    os.symlink(root / "hot" / "data" / "raw" / "baostock", hot / "data" / "raw" / "baostock")
    configs = tmp_path / "configs"
    shutil.copytree(root / "configs", configs)
    uni = yaml.safe_load((configs / "universes.yaml").read_text())
    uni["ashare_all"].update(history_start="2017-06-01", dev_start="2018-06-01")
    uni["ashare_all"]["rule"]["min_days_listed"] = 60
    (configs / "universes.yaml").write_text(yaml.safe_dump(uni))
    monkeypatch.setenv("ALPHASIEVE_HOT_ROOT", str(hot))
    monkeypatch.setenv("ALPHASIEVE_STORE_ROOT", str(tmp_path / "store"))
    monkeypatch.setenv("ALPHASIEVE_STORE_MOUNT", "")
    monkeypatch.setenv("ALPHASIEVE_CONFIG_DIR", str(configs))
    monkeypatch.setenv("ALPHASIEVE_ROLE", "system")
    settings = get_settings()
    conn = connect(settings.state_db)
    result = build_panel(settings, conn, universe="ashare_all", tiers=("dev",))
    return settings, result, synth_info


def test_rule_universe_panel(all_a):
    settings, result, info = all_a
    assert set(result) == {"dev"} and result["dev"]["window"]["start"] == "2018-06-01"
    assert (settings.hot_root / "data" / "panel" / "ashare_all" / "dev" / "panel.parquet").exists()
    assert not (settings.hot_root / "data" / "panel" / "ashare_all" / "holdout").exists()
    codes = universe_codes(settings, "ashare_all")
    assert info.delisted_code in codes and all(not c.startswith("sh.000") for c in codes)
    panel = load_panel(settings, "dev", universe="ashare_all")
    long = panel.long
    assert panel.meta["universe"] == "ashare_all"
    assert not long.loc[long["in_universe"], "is_st"].any()
    assert not long.loc[long["in_universe"], "is_suspended"].any()
    eligible = long[(long["days_listed"] >= 60) & ~long["is_st"] & ~long["is_suspended"]]
    share = long["in_universe"].sum() / max(len(eligible), 1)
    assert 0.6 < share < 0.85
    assert "in_csi800" not in long.columns


def test_campaign_on_rule_universe(all_a, monkeypatch):
    settings, _, _ = all_a
    spec = campaign_spec("c-all-a", universe="ashare_all")
    start_campaign(settings, spec, monkeypatch)
    conn = connect(settings.state_db)
    agent = replace(get_settings(), role="agent")
    base = {"name": "rev_all", "expression": "ts_sum(excess_ret_1d, 3)", "direction": -1, "hypothesis": "r",
            "cell": {"domain": "price", "form": "reversal", "scale": "short"}}
    wrong = evaluate_spec(agent, conn, FactorSpec(**base), campaign_id="c-all-a")
    assert wrong["gates"]["l0"]["checks"][0]["name"] == "campaign_universe"
    right = evaluate_spec(agent, conn, FactorSpec(**{**base, "name": "rev_all2", "universe": "ashare_all"}),
                          campaign_id="c-all-a")
    assert right["outcome"] != "validation_failed"
    assert right["panel_signature"] == load_panel(settings, "dev", universe="ashare_all").signature
    assert right["window"].startswith("2018-06-01")
    with pytest.raises(AlphaSieveError) as exc:
        service.create_campaign(conn, replace(settings, role="human"), campaign_spec("c-bad", universe="nasdaq"))
    assert exc.value.code == "VALIDATION_ERROR"
