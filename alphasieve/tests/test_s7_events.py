import os

import numpy as np
import pandas as pd
import pytest

from alphasieve.data import events


def test_forecast_is_point_in_time_and_expires():
    calendar = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2020-01-01", periods=120)]
    fc = pd.DataFrame({"code": ["sh.600000"], "profitForcastExpPubDate": [calendar[10]], "profitForcastType": ["预增"],
                       "profitForcastChgPctUp": [60.0], "profitForcastChgPctDwn": [40.0]})
    aligned = events.align_events(events.forecast_rows(fc), calendar, ["fc_chg_mid", "fc_positive"])
    assert str(aligned["date"].iloc[0].date()) == calendar[11]
    panel = pd.DataFrame({"date": pd.to_datetime(calendar), "code": "sh.600000"})
    out = events.attach_events(panel, aligned, calendar, ["fc_chg_mid", "fc_positive"], age_field="fc_age")
    out = out.set_index("date")
    assert np.isnan(out.loc[calendar[10], "fc_chg_mid"])
    assert out.loc[calendar[11], "fc_chg_mid"] == 50.0 and out.loc[calendar[11], "fc_positive"] == 1.0
    assert out.loc[calendar[11], "fc_age"] == 0 and out.loc[calendar[11 + 60], "fc_age"] == 60
    assert np.isnan(out.loc[calendar[11 + 61], "fc_chg_mid"])


def test_intraday_features():
    rng = np.random.default_rng(0)
    rows = []
    for day in ("2021-03-01", "2021-03-02"):
        price = 10.0
        for i in range(48):
            o = price
            price = price * (1 + rng.normal(0, 0.002))
            rows.append({"date": day, "time": f"{i:03d}", "open": o, "high": max(o, price), "low": min(o, price),
                         "close": price, "volume": 1000 + (5000 if i >= 42 else 0), "amount": 0.0})
    rows = rows[:-20]
    bars = pd.DataFrame(rows)
    bars["code"] = "sz.000001"
    feats = events.intraday_features(bars)
    assert list(feats["date"]) == ["2021-03-01"]
    first = feats.iloc[0]
    assert first["tail30_vol_share"] == pytest.approx(6 * 6000 / (42 * 1000 + 6 * 6000))
    assert 0 < first["rv_5m"] < 0.05 and 0 <= first["updown_vol_share"] <= 1


def test_panel_attaches_events(built_root, tmp_path, monkeypatch):
    import shutil

    from alphasieve.config import get_settings
    from alphasieve.data.panel import build_long_panel

    root, _ = built_root
    hot = tmp_path / "hot"
    raw = hot / "data" / "raw" / "baostock"
    shutil.copytree(root / "hot" / "data" / "raw" / "baostock", raw, symlinks=True)
    code = sorted(p.stem for p in (raw / "daily").glob("*.parquet"))[0]
    (raw / "events" / "forecast").mkdir(parents=True)
    pd.DataFrame({"code": [code], "profitForcastExpPubDate": ["2019-04-10"], "profitForcastExpStatDate": ["2019-03-31"],
                  "profitForcastType": ["略减"], "profitForcastAbstract": [""], "profitForcastChgPctUp": [-10.0],
                  "profitForcastChgPctDwn": [-30.0]}).to_parquet(raw / "events" / "forecast" / f"{code}.parquet")
    monkeypatch.setenv("ALPHASIEVE_HOT_ROOT", str(hot))
    monkeypatch.setenv("ALPHASIEVE_CONFIG_DIR", str(root / "configs"))
    monkeypatch.setenv("ALPHASIEVE_ROLE", "system")
    panel, _, info = build_long_panel(get_settings(), "2019-12-31")
    assert info["has_events"]
    row = panel[(panel["code"] == code) & (panel["date"] == "2019-04-11")].iloc[0]
    assert row["fc_chg_mid"] == -20.0 and row["fc_positive"] == -1.0
    assert panel.loc[panel["code"] != code, "fc_chg_mid"].isna().all()
    assert os.path.exists(raw / "events")


def test_programmatic_search_has_its_own_accounting(panel_settings):
    from dataclasses import replace

    from fixtures.campaign import campaign_spec, start_campaign

    from alphasieve.campaigns import service
    from alphasieve.search.generator import Generator, run_search
    from alphasieve.state import connect

    gen = Generator(["close", "volume"], [5, 10, 250], seed=1)
    assert all(e for e in (gen.expr() for _ in range(20)))
    spec = campaign_spec("c-prog", domains=["price", "volume"], agents=[{"harness": "program", "model": "random"}],
                         budgets={"trials": 6, "turns": 1})
    start_campaign(panel_settings, spec)
    system = replace(panel_settings, role="system")
    out = run_search(system, "c-prog", trials=6, method="evolve", population=4, seed=3)
    assert sum(out["outcomes"].values()) == 6
    conn = connect(panel_settings.state_db)
    assert len(service.completed_trials(conn, "c-prog")) == 6
    assert service.get_campaign(conn, "c-prog")["status"] in ("concluded", "awaiting_holdout_approval")
    names = [r[0] for r in conn.execute("SELECT name FROM factor_specs WHERE name LIKE 'prog_c_prog_%'")]
    assert len(names) == 6
    llm = campaign_spec("c-not-prog")
    start_campaign(panel_settings, llm)
    from alphasieve.errors import AlphaSieveError
    with pytest.raises(AlphaSieveError):
        run_search(system, "c-not-prog", trials=1)
