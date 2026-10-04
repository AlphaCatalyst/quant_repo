from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from alphasieve.redflag import flags_for
from alphasieve.redflag.service import _overall_level, scan


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
    industry_rows = pd.read_parquet(p).to_dict("records") if p.exists() else []
    pd.DataFrame([*industry_rows, {"code": code, "level": 1, "sector_name": industry}]).to_parquet(p, index=False)


def test_pit_and_hard_condition(tmp_path):
    settings = _settings(tmp_path)
    _write(settings, pub="2025-04-30")
    before = flags_for(settings, ["sz.000001"], "2025-04-30")[0]
    assert before["period"] == "2023-12-31"  # latest year is still unavailable
    after = flags_for(settings, ["sz.000001"], "2025-05-01")[0]
    assert after["period"] == "2024-12-31"
    assert after["rules"]["receivables_growth"]["level"] == "none"
    assert after["hard_conditions"]["goodwill_over_half_net_assets"]["triggered"]
    assert after["rules"]["cash_debt"]["peer_group"] == "universe"
    assert after["rules"]["cash_debt"]["peer_count"] == 1
    assert after["rules"]["audit_opinion"]["level"] == "unavailable"
    assert after["level"] == "red"
    assert after["rules"]["receivables_growth"]["evidence"]["prior_fields"]["zcfz"]["BillAccReceivable"] == 10


def test_industry_percentiles_fallback_and_combination(tmp_path):
    settings = _settings(tmp_path)
    codes = [f"sz.{i:06d}" for i in range(30)]
    for i, code in enumerate(codes):
        _write(settings, code=code, industry="电子" if i < 20 else "机械", cash=10 + i)
        root = settings.raw_dir / "westock" / "financials" / "zcfz" / f"{code}.parquet"
        df = pd.read_parquet(root)
        df.loc[df.EndDate == "2024-12-31", "GoodWill"] = i
        df.to_parquet(root, index=False)
    rows = flags_for(settings, codes, "2025-05-01")
    top = rows[19]["rules"]["goodwill"]
    assert top["level"] == "red"
    assert top["peer_group"] == "电子" and top["peer_count"] == 20
    assert top["industry_percentile"] == 0.975
    fallback = rows[29]["rules"]["goodwill"]
    assert fallback["peer_group"] == "universe" and fallback["peer_count"] == 30
    assert rows[19]["level"] == "amber"  # one percentile red alone


def test_combination_boundaries():
    assert _overall_level(["amber"], False) == "none"
    assert _overall_level(["amber", "amber"], False) == "amber"
    assert _overall_level(["red"], False) == "amber"
    assert _overall_level(["red", "red"], False) == "red"
    assert _overall_level(["red", "amber", "amber"], False) == "red"
    assert _overall_level([], True) == "red"


def test_three_year_cash_hard_condition_respects_pit(tmp_path):
    settings = _settings(tmp_path)
    _write(settings, cash=-5, pub="2025-04-30")
    root = settings.raw_dir / "westock" / "financials"
    for kind, field, value in (
        ("lrb", "NPParentCompanyOwnersTTM", 20),
        ("xjll", "NetOperateCashFlowTTM", -2),
    ):
        path = root / kind / "sz.000001.parquet"
        df = pd.read_parquet(path)
        df.loc[df.EndDate == "2023-12-31", field] = value
        extra = {"code": "sz.000001", "EndDate": "2022-12-31", "InfoPublDate": "2023-04-30", field: value}
        pd.concat([df, pd.DataFrame([extra])], ignore_index=True).to_parquet(path, index=False)
    after = flags_for(settings, ["sz.000001"], "2025-05-01")[0]
    assert after["hard_conditions"]["negative_cash_three_years_with_profit"]["triggered"]
    assert after["level"] == "red"
    before = flags_for(settings, ["sz.000001"], "2025-04-30")[0]
    assert not before["hard_conditions"]["negative_cash_three_years_with_profit"]["triggered"]


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
