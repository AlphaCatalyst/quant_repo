from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from alphasieve.redflag import flags_for
from alphasieve.redflag.service import scan


def _settings(tmp_path):
    return SimpleNamespace(raw_dir=tmp_path / "raw", hot_root=tmp_path / "hot")


def _write(settings, code="sz.000001", cash=10, industry="电子", pub="2024-04-30"):
    root = settings.raw_dir / "westock"
    for kind in ("lrb", "zcfz", "xjll"):
        (root / "financials" / kind).mkdir(parents=True, exist_ok=True)
    entries = {
        "lrb": [
            {"EndDate": "2023-12-31", "InfoPublDate": "2024-04-30", "TotalOperatingRevenueTTM": 100},
            {
                "EndDate": "2024-12-31",
                "InfoPublDate": pub,
                "TotalOperatingRevenueTTM": 110,
                "NPParentCompanyOwnersTTM": 50,
                "GrossProfitTTM": 80,
            },
        ],
        "zcfz": [
            {
                "EndDate": "2023-12-31",
                "InfoPublDate": "2024-04-30",
                "BillAccReceivable": 10,
                "Inventories": 10,
                "AdvancePayment": 2,
            },
            {
                "EndDate": "2024-12-31",
                "InfoPublDate": pub,
                "BillAccReceivable": 30,
                "Inventories": 20,
                "AdvancePayment": 6,
                "TotalLiability": 100,
                "TotalShareholderEquity": 100,
                "SEWithoutMI": 90,
                "GoodWill": 50,
                "OtherReceivableED": 30,
                "CashEquivalents": 40,
                "ShortTermLoan": 50,
            },
        ],
        "xjll": [
            {"EndDate": "2023-12-31", "InfoPublDate": "2024-04-30"},
            {"EndDate": "2024-12-31", "InfoPublDate": pub, "NetOperateCashFlowTTM": cash},
        ],
    }
    for kind, rows in entries.items():
        pd.DataFrame([{"code": code, **x} for x in rows]).to_parquet(
            root / "financials" / kind / f"{code}.parquet", index=False
        )
    p = root / "sw_industry" / "2025-01-01.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"code": code, "level": 1, "sector_name": industry}]).to_parquet(p, index=False)


def test_pit_and_thresholds(tmp_path):
    settings = _settings(tmp_path)
    _write(settings, pub="2025-04-30")
    before = flags_for(settings, ["sz.000001"], "2025-04-30")[0]
    assert before["period"] == "2023-12-31"  # latest year is still unavailable
    after = flags_for(settings, ["sz.000001"], "2025-05-01")[0]
    assert after["period"] == "2024-12-31"
    assert after["rules"]["receivables_growth"]["level"] == "red"
    assert after["rules"]["cash_profit"]["level"] == "red"
    assert after["rules"]["goodwill"]["level"] == "red"
    assert after["rules"]["cash_debt"]["level"] == "red"
    assert after["rules"]["audit_opinion"]["level"] == "unavailable"
    assert after["level"] == "red"
    assert after["rules"]["receivables_growth"]["evidence"]["prior_fields"]["zcfz"]["BillAccReceivable"] == 10


def test_financial_skip_and_save(tmp_path):
    settings = _settings(tmp_path)
    _write(settings, industry="银行", pub="2025-04-30")
    row = flags_for(settings, ["sz.000001"], "2025-05-01")[0]
    assert row["status"] == "financial_industry_skipped"
    assert row["level"] == "unavailable"
    out = scan(settings, ["sz.000001"], "2025-05-01")
    assert out["levels"]["unavailable"] == 1
    assert Path(out["paths"][0]).exists() and Path(out["paths"][1]).exists()


def test_nontrading_asof_uses_last_trade_date(tmp_path):
    settings = _settings(tmp_path)
    _write(settings, pub="2025-05-03")
    cal = settings.raw_dir / "baostock" / "trade_dates.parquet"
    cal.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {"calendar_date": "2025-05-02", "is_trading_day": 1},
            {"calendar_date": "2025-05-03", "is_trading_day": 0},
            {"calendar_date": "2025-05-04", "is_trading_day": 0},
            {"calendar_date": "2025-05-05", "is_trading_day": 1},
        ]
    ).to_parquet(cal, index=False)
    before = flags_for(settings, ["sz.000001"], "2025-05-04")[0]
    after = flags_for(settings, ["sz.000001"], "2025-05-05")[0]
    assert before["effective_trade_date"] == "2025-05-02"
    assert before["period"] == "2023-12-31"
    assert after["period"] == "2024-12-31"
