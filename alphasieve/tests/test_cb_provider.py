"""Offline fixtures for the convertible-bond provider."""

import sys
from types import SimpleNamespace

import pandas as pd

from alphasieve.data.providers import westock


def test_bond_detail_native_batch_and_clauses(monkeypatch):
    calls = []

    def fake_call(args):
        calls.append(args)
        return {"success": True, "data": [{"symbol": "sz127045", "data": {
            "code": "sz127045", "issuer": "牧原食品", "dueDate": "2027-08-16",
            "convertPrice": 43.32, "cpRevisionTerm": "可向下修正",
            "couponRateList": '[{"CouponBeginDate":"20210816","CouponRate":0.2}]',
            "putDetail": '[{"PutStartDate":"20250816","PutLevel":70}]',
            "callDetail": '[{"CallStartDate":"20220221","CallLevel":1.3}]',
            "changeDetail": '[{"ChangeAdjDirection":"向下修正","ChangeLevel":0.8}]',
            "cashflowDetail": '[{"CashflowPaymentDate":"20220816","InterestPer":0.2}]',
        }}]}

    monkeypatch.setattr(westock, "_call", fake_call)
    result = westock.bond_detail(["sz.127045"])
    assert calls[0][2] == "sz127045,sz127045"
    assert result["terms"].iloc[0]["cpRevisionTerm"] == "可向下修正"
    assert result["terms"].iloc[0]["code"] == "sz.127045"
    assert result["coupons"].iloc[0]["CouponBeginDate"] == "2021-08-16"
    assert result["puts"].iloc[0]["PutLevel"] == 70
    assert result["calls"].iloc[0]["CallStartDate"] == "2022-02-21"
    assert result["revisions"].iloc[0]["ChangeLevel"] == 0.8
    assert result["cashflows"].iloc[0]["CashflowPaymentDate"] == "2022-08-16"


def test_bond_detail_retries_dropped_code(monkeypatch):
    calls = []

    def fake_call(args):
        calls.append(args[2])
        symbol = args[2].split(",")[0]
        if symbol == "sz127045" and "sh113052" in args[2]:
            return {"success": True, "data": []}
        return {"success": True, "data": [{"data": {"code": symbol, "issuer": symbol}}]}

    monkeypatch.setattr(westock, "_call", fake_call)
    result = westock.bond_detail(["sz.127045", "sh.113052"])
    assert set(result["terms"]["code"]) == {"sz.127045", "sh.113052"}
    assert calls == ["sz127045,sh113052", "sz127045,sz127045", "sh113052,sh113052"]


def test_bond_daily_pages_past_vendor_cap(monkeypatch):
    calls = []

    def fake_kline(codes, start, end):
        calls.append((codes, start, end))
        dates = pd.date_range("2024-01-01", end).strftime("%Y-%m-%d").tolist()
        return pd.DataFrame([{"code": codes[0], "date": day, "open": "100", "last": "101",
                              "high": "102", "low": "99", "volume": "1000", "amount": "100000",
                              "exchange": "0.1"} for day in dates[-250:]])

    monkeypatch.setattr(westock, "kline", fake_kline)
    frame = westock.bond_daily(["sz.127045"], "2024-01-01", "2025-01-31")
    assert len(frame) == 397
    assert frame["date"].iloc[0] == "2024-01-01"
    assert frame["date"].iloc[-1] == "2025-01-31"
    assert len(calls) == 2
    assert frame["last"].dtype.kind == "f" or frame["last"].dtype.kind == "i"


def test_bond_quote_preserves_valuation(monkeypatch):
    monkeypatch.setattr(westock, "quote", lambda codes: {
        "sz.127045": {"code": "sz127045", "time": "2026-09-30", "price": 117.965,
                      "bond_convert_price": 43.32, "bond_stock_code": "002714"}})
    frame = westock.bond_quote(["sz.127045"])
    assert frame.iloc[0]["code"] == "sz.127045"
    assert frame.iloc[0]["bond_convert_price"] == 43.32


def test_bond_universe_retains_retired_codes(monkeypatch):
    fixture = pd.DataFrame({"债券代码": ["113052", "404001"], "债券简称": ["兴业转债", "蓝盾退债"],
                            "上市时间": ["2022-01-14", "2023-09-18"], "正股代码": ["601166", "300297"]})
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(bond_zh_cov=lambda: fixture))
    frame = westock.bond_universe()
    assert frame["code"].tolist() == ["sh.113052", "sz.404001"]
    assert frame["list_date"].tolist() == ["2022-01-14", "2023-09-18"]


def test_bond_daily_recovers_retired_bond_from_annual_ranges(monkeypatch):
    calls = []

    def fake_kline(codes, start, end):
        calls.append((start, end))
        if start == "2007-07-12" and end == "2007-12-31":
            return pd.DataFrame([{"code": codes[0], "date": "2007-07-12", "open": 100, "last": 101,
                                  "high": 102, "low": 99, "volume": 1, "amount": 100, "exchange": 0.1}])
        return pd.DataFrame()

    monkeypatch.setattr(westock, "kline", fake_kline)
    frame = westock.bond_daily(["sh.110026"], "2007-07-12", "2026-10-03")
    assert frame[["code", "date", "last"]].values.tolist() == [["sh.110026", "2007-07-12", 101]]
    assert calls[:2] == [("2007-07-12", "2026-10-03"), ("2007-07-12", "2007-12-31")]
