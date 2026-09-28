import json
import sys

import numpy as np
import pandas as pd
import pytest

from alphasieve.data import fundamentals
from alphasieve.data.panel import align_financials_pit
from alphasieve.data.providers import westock

PERIODS = ["2018-12-31", "2019-03-31", "2019-06-30", "2019-09-30", "2019-12-31"]
PUB = ["2019-03-28", "2019-04-25", "2019-08-20", "2019-10-28", "2020-03-30"]


def _statements(code="sh.600000"):
    n = len(PERIODS)
    lrb = pd.DataFrame({"code": code, "EndDate": PERIODS, "InfoPublDate": PUB,
                        "NPParentCompanyOwnersTTM": [100.0, 104, 108, 112, 120],
                        "NPParentCompanyOwners_Q": [20.0, 30, 25, 30, 35],
                        "TotalOperatingRevenueTTM": [1000.0, 1010, 1020, 1030, 1100],
                        "TotalOperatingRevenue_Q": [250.0] * n, "RAndD_Q": [10.0] * n,
                        "GrossProfitTTM": [300.0] * n, "OperatingProfitTTM": [150.0] * n})
    zcfz = pd.DataFrame({"code": code, "EndDate": PERIODS, "InfoPublDate": PUB[:-1] + ["2020-04-02"],
                         "TotalLiability": [600.0] * n, "TotalShareholderEquity": [400.0, 400, 400, 400, 500],
                         "SEWithoutMI": [380.0] * n, "InterestBearDebt": [190.0] * n, "CashEquivalents": [100.0] * n})
    xjll = pd.DataFrame({"code": code, "EndDate": PERIODS, "InfoPublDate": PUB,
                         "NetOperateCashFlowTTM": [80.0] * n})
    return lrb, zcfz, xjll


def test_statement_rows_derive_ratios_and_yoy():
    rows = fundamentals.statement_rows(*_statements()).set_index("statDate")
    last = rows.loc["2019-12-31"]
    assert last["pubDate"] == "2020-04-02"          # the latest of the three announcements
    assert last["ws_roe_ttm"] == pytest.approx(120 / 380)
    assert last["ws_roa_ttm"] == pytest.approx(120 / 1100)
    assert last["ws_accruals_ttm"] == pytest.approx((120 - 80) / 1100)
    assert last["ws_debt_to_assets"] == pytest.approx(600 / 1100)
    assert last["ws_goodwill_to_equity"] == 0.0     # missing goodwill means none
    assert last["ws_asset_growth_yoy"] == pytest.approx(1100 / 1000 - 1)
    assert last["ws_rev_growth_yoy"] == pytest.approx(1100 / 1000 - 1)
    assert last["ws_np_growth_q_yoy"] == pytest.approx((35 - 20) / 20)
    assert last["ws_np_surprise_q"] == pytest.approx(15 / 1100)
    assert np.isnan(rows.loc["2019-03-31", "ws_rev_growth_yoy"])   # no 2018Q1 statement


def test_statement_rows_become_usable_after_announcement():
    rows = fundamentals.statement_rows(*_statements())
    calendar = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2019-01-01", "2020-06-30")]
    aligned = align_financials_pit(rows, calendar, {f: f for f in fundamentals.STATEMENT_FIELDS})
    by_stat = aligned.set_index("stat_date")["effective_date"]
    assert by_stat["2019-12-31"] == "2020-04-03"
    assert by_stat["2019-03-31"] == "2019-04-26"


def test_fund_flow_ratios_use_traded_amount():
    panel = pd.DataFrame({"date": pd.to_datetime(["2021-01-04", "2021-01-05"]), "code": ["sz.000001"] * 2,
                          "amount": [1e8, 0.0]})
    flow = pd.DataFrame({"code": ["sz.000001"] * 2, "date": ["2021-01-04", "2021-01-05"],
                         **{src: [2e6, 1e6] for src in fundamentals.FLOW_RATIOS.values()}})
    out = fundamentals.attach_fund_flow(panel, flow)
    assert out.loc[0, "mf_main_net_ratio"] == pytest.approx(0.02)
    assert np.isnan(out.loc[1, "mf_main_net_ratio"])   # no trading, no ratio
    assert "MainNetFlow" not in out.columns


FAKE_CLI = """
import json, sys
args = sys.argv[1:]
codes = args[2].split(",") if args[0] == "fund" else args[1].split(",")
kept = codes[:1] if len(codes) > 1 else codes     # batch mode drops everything but the first code
rows = [{"symbol": c, "code": c, "date": "2021-01-04", "EndDate": "2021-01-04", "SecuCode": c,
         "MainNetFlow": "1.5", "JumboNetFlow": "1", "BlockNetFlow": "0.5", "MidNetFlow": "-1",
         "SmallNetFlow": "-0.5", "MainInFlow": "3", "MainOutFlow": "1.5", "RetailInFlow": "2",
         "RetailOutFlow": "3.5"} for c in kept if c != "sz999999"]
print(json.dumps(rows))
"""


def test_provider_retries_codes_dropped_by_batch_mode(tmp_path, monkeypatch):
    script = tmp_path / "fake_westock"
    script.write_text(f"#!{sys.executable}\n{FAKE_CLI}")
    script.chmod(0o755)
    monkeypatch.setattr(westock, "CLI", str(script))
    df = westock.fund_flow(["sh.600000", "sz.000001", "sz.999999"], "2021-01-01", "2021-01-31")
    assert sorted(df["code"]) == ["sh.600000", "sz.000001"]
    assert df["MainNetFlow"].dtype == float and df["MainNetFlow"].iloc[0] == 1.5


def test_provider_rejects_non_json(tmp_path, monkeypatch):
    script = tmp_path / "broken"
    script.write_text(f"#!{sys.executable}\nprint('service error')")
    script.chmod(0o755)
    monkeypatch.setattr(westock, "CLI", str(script))
    with pytest.raises(westock.WestockError):
        westock.fund_flow(["sh.600000"], "2021-01-01", "2021-01-31")


def test_fund_flow_sync_chunks_by_year_and_appends(settings, monkeypatch):
    from alphasieve.data import sync
    from alphasieve.state import connect

    calls = []

    def fake_flow(codes, start, end):
        calls.append((tuple(codes), start, end))
        days = pd.bdate_range(start, end).strftime("%Y-%m-%d")[:2]
        return pd.DataFrame([{"code": c, "date": d, "MainNetFlow": 1.0} for c in codes for d in days])

    monkeypatch.setattr(westock, "fund_flow", fake_flow)
    monkeypatch.setattr(sync, "universe_codes", lambda s, u=None: ["sh.600000", "sz.000001"])
    conn = connect(settings.state_db)
    out = sync.sync_fund_flow(settings, conn, "2021-06-30", workers=1)
    assert [c[1:] for c in calls] == [("2020-01-01", "2020-12-31"), ("2021-01-01", "2021-06-30")]
    assert out["files"] == 2 and out["new_rows"] == 8 and not out["errors"]
    calls.clear()
    sync.sync_fund_flow(settings, conn, "2021-07-31", workers=1)
    assert [c[1:] for c in calls] == [("2021-01-05", "2021-07-31")]   # resumes after the last stored day
    stored = pd.read_parquet(sync.westock_root(settings) / "fund_flow" / "sh.600000.parquet")
    assert stored["date"].is_unique and len(stored) == 6


def test_panel_attaches_westock_fields(built_root, tmp_path, monkeypatch):
    import shutil

    from alphasieve.config import get_settings
    from alphasieve.data.panel import build_long_panel

    root, _ = built_root
    hot = tmp_path / "hot"
    raw = hot / "data" / "raw"
    shutil.copytree(root / "hot" / "data" / "raw" / "baostock", raw / "baostock", symlinks=True)
    code = sorted(p.stem for p in (raw / "baostock" / "daily").glob("*.parquet"))[0]
    ws = raw / "westock"
    for kind, df in zip(("lrb", "zcfz", "xjll"), _statements(code), strict=True):
        (ws / "financials" / kind).mkdir(parents=True)
        df.to_parquet(ws / "financials" / kind / f"{code}.parquet")
    (ws / "fund_flow").mkdir()
    pd.DataFrame({"code": [code], "date": ["2019-04-11"],
                  **{src: [1e6] for src in fundamentals.FLOW_RATIOS.values()}}).to_parquet(
        ws / "fund_flow" / f"{code}.parquet")
    monkeypatch.setenv("ALPHASIEVE_HOT_ROOT", str(hot))
    monkeypatch.setenv("ALPHASIEVE_CONFIG_DIR", str(root / "configs"))
    monkeypatch.setenv("ALPHASIEVE_ROLE", "system")
    panel, _, info = build_long_panel(get_settings(), "2019-12-31")
    assert info["has_westock_financials"] and info["has_fund_flow"]
    rows = panel[panel["code"] == code].set_index("date")
    assert rows.loc["2019-04-25", "ws_stat_date"] == "2018-12-31"  # 2019Q1 announced on 04-25, usable on 04-26
    assert rows.loc["2019-04-26", "ws_stat_date"] == "2019-03-31"
    assert rows.loc["2019-04-26", "ws_roe_ttm"] == pytest.approx(104 / 380)
    assert rows.loc["2019-04-11", "mf_main_net_ratio"] == pytest.approx(1e6 / rows.loc["2019-04-11", "amount"])
    assert panel.loc[panel["code"] != code, "ws_roe_ttm"].isna().all()
    assert json.dumps(info)
