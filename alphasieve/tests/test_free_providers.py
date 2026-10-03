import json

import pandas as pd
import pytest

from alphasieve.data.providers import csindex, eastmoney, exchange, free_http


def _json(obj):
    return json.dumps(obj).encode()


def test_csindex_history_parse_keeps_official_dates_and_rejects_wrong_index():
    rows = [{"tradeDate": "20220104", "indexCode": "H00905", "close": 8506.54, "changePct": -0.07,
             "tradingVol": 20081001837},
            {"tradeDate": "20220105", "indexCode": "H00905", "close": 8500.0}]
    frame = csindex.parse_index_history(_json({"success": True, "data": rows}), "H00905")
    assert frame[["date", "close"]].values.tolist() == [["2022-01-04", 8506.54], ["2022-01-05", 8500.0]]
    rows[1]["indexCode"] = "H00300"
    with pytest.raises(free_http.FreeDataError, match="mismatch"):
        csindex.parse_index_history(_json({"success": True, "data": rows}), "H00905")


def test_csindex_weights_parse_latest_snapshot(monkeypatch):
    monkeypatch.setattr(pd, "read_excel", lambda *args, **kwargs: pd.DataFrame([{
        "日期Date": "20220831", "指数代码 Index Code": "000905", "成份券代码Constituent Code": "000001",
        "成份券名称Constituent Name": "平安银行", "交易所Exchange": "深圳证券交易所",
        "权重(%)weight": "0.128"}]))
    frame = csindex.parse_index_weights(b"\xd0\xcf\x11\xe0" + b"fixture", "000905")
    assert frame.iloc[0][["snapshot_date", "code", "weight"]].tolist() == ["2022-08-31", "sz.000001", 0.128]


def test_exchange_parses_only_matching_margin_fields(monkeypatch):
    monkeypatch.setattr(pd, "read_excel", lambda *args, **kwargs: pd.DataFrame([{
        "证券代码": "000001", "证券简称": "平安银行", "融资买入额(元)": "89,526,573",
        "融资余额(元)": "3,530,397,313", "融券卖出量(股/份)": "361,100",
        "融券余量(股/份)": "7,690,197", "融券余额(元)": "101,202,993",
        "融资融券余额(元)": "3,631,600,306"}]))
    sz = exchange.parse_szse(b"PKfixture", "2022-12-30").iloc[0]
    assert sz["code"] == "sz.000001" and sz["FinanceValue"] == 3530397313
    assert sz["SecurityValue"] == 101202993 and sz["TradingValue"] == 3631600306
    assert pd.isna(sz["FinanceRefundValue"])
    sse = exchange.parse_sse(_json({"result": [{"stockCode": "600000", "opDate": "20221230",
        "securityAbbr": "浦发银行", "rzye": 1000, "rzmre": 200, "rzche": 100,
        "rqyl": 50, "rqmcl": 20, "rqchl": 10}]}), "2022-12-30").iloc[0]
    assert sse["code"] == "sh.600000" and sse["FinanceRefundValue"] == 100
    assert pd.isna(sse["SecurityValue"]) and pd.isna(sse["TradingValue"])


def test_exchange_fetches_both_venues_and_all_sse_pages(monkeypatch):
    calls = []
    monkeypatch.setattr(pd, "read_excel", lambda *args, **kwargs: pd.DataFrame(columns=[
        "证券代码", "融资买入额", "融资余额", "融券卖出量", "融券余量", "融券余额", "融资融券余额"]))

    def fake_get(url, params, **kwargs):
        calls.append((url, params))
        if url == exchange.SZ_URL:
            return b"PKfixture"
        page = int(params["pageHelp.pageNo"])
        return _json({"pageHelp": {"pageCount": 2}, "result": [{"stockCode": f"60000{page}",
            "opDate": "20221230", "rzye": page, "rzmre": page, "rzche": page}]})

    monkeypatch.setattr(free_http, "get", fake_get)
    frame = exchange.fetch_margin_day("2022-12-30")
    assert frame["code"].tolist() == ["sh.600001", "sh.600002"]
    assert len(calls) == 3


def test_holder_page_uses_announcement_date_and_paginates(monkeypatch):
    seen = []

    def fake_get(url, params, **kwargs):
        page = int(params["pageNumber"])
        seen.append(page)
        return _json({"success": True, "result": {"pages": 2, "data": [{
            "SECURITY_CODE": "000001", "END_DATE": f"2022-0{page}-{'31' if page == 1 else '28'} 00:00:00",
            "HOLD_NOTICE_DATE": f"2022-0{page + 1}-15 00:00:00", "HOLDER_NUM": 100 + page}]}})

    monkeypatch.setattr(free_http, "get", fake_get)
    frame = eastmoney.fetch_holder_history("sz.000001")
    assert seen == [1, 2]
    assert frame[["stat_date", "announce_date", "holder_num"]].values.tolist() == [
        ["2022-01-31", "2022-02-15", 101], ["2022-02-28", "2022-03-15", 102]]
    with pytest.raises(ValueError):
        eastmoney.fetch_holder_history("000001")


def test_http_retries_transient_error_without_returning_partial_data(monkeypatch):
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(None)
        raise OSError("connection reset")

    monkeypatch.setattr(free_http.urllib.request, "urlopen", fail)
    monkeypatch.setattr(free_http.time, "sleep", lambda *_: None)
    monkeypatch.setattr(free_http, "_NEXT_CALL", 0.0)
    with pytest.raises(free_http.FreeDataError, match="failed"):
        free_http.get("https://example.org/a")
    assert len(attempts) == free_http.RETRIES
