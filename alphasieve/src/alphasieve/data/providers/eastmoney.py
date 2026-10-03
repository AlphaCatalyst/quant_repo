"""Eastmoney historical shareholder counts with announcement dates."""

import json

import pandas as pd

from alphasieve.data.providers import free_http

URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
COLUMNS_PARAM = ("SECURITY_CODE,SECURITY_NAME_ABBR,CHANGE_SHARES,CHANGE_REASON,END_DATE,INTERVAL_CHRATE,"
                 "AVG_MARKET_CAP,AVG_HOLD_NUM,TOTAL_MARKET_CAP,TOTAL_A_SHARES,HOLD_NOTICE_DATE,HOLDER_NUM,"
                 "PRE_HOLDER_NUM,HOLDER_NUM_CHANGE,HOLDER_NUM_RATIO,PRE_END_DATE")
COLUMNS = ["code", "stat_date", "announce_date", "holder_num", "prev_holder_num", "holder_num_change",
           "holder_num_ratio", "avg_hold_num", "avg_market_cap", "total_market_cap", "total_a_shares",
           "change_shares", "change_reason", "interval_change_pct", "name"]


def parse_holder_page(data: bytes, code: str) -> tuple[pd.DataFrame, int]:
    payload = json.loads(data)
    result = payload.get("result")
    if payload.get("success") is False or not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise free_http.FreeDataError(f"Eastmoney holder response invalid for {code}")
    pages = int(result.get("pages") or 0)
    if not 1 <= pages <= 100:
        raise free_http.FreeDataError(f"Eastmoney holder pages invalid for {code}")
    rows = []
    for row in result["data"]:
        if row.get("SECURITY_CODE") != code[3:]:
            raise free_http.FreeDataError(f"Eastmoney holder code mismatch for {code}")
        stat_date = pd.to_datetime(row.get("END_DATE"), errors="coerce")
        announce_date = pd.to_datetime(row.get("HOLD_NOTICE_DATE"), errors="coerce")
        if pd.isna(stat_date) or pd.isna(announce_date):
            raise free_http.FreeDataError(f"Eastmoney holder PIT dates missing for {code}")
        rows.append({"code": code, "stat_date": stat_date.strftime("%Y-%m-%d"),
                     "announce_date": announce_date.strftime("%Y-%m-%d"),
                     "holder_num": row.get("HOLDER_NUM"), "prev_holder_num": row.get("PRE_HOLDER_NUM"),
                     "holder_num_change": row.get("HOLDER_NUM_CHANGE"),
                     "holder_num_ratio": row.get("HOLDER_NUM_RATIO"), "avg_hold_num": row.get("AVG_HOLD_NUM"),
                     "avg_market_cap": row.get("AVG_MARKET_CAP"), "total_market_cap": row.get("TOTAL_MARKET_CAP"),
                     "total_a_shares": row.get("TOTAL_A_SHARES"), "change_shares": row.get("CHANGE_SHARES"),
                     "change_reason": row.get("CHANGE_REASON"), "interval_change_pct": row.get("INTERVAL_CHRATE"),
                     "name": row.get("SECURITY_NAME_ABBR")})
    frame = pd.DataFrame(rows, columns=COLUMNS)
    for column in COLUMNS[3:-1]:
        if column != "change_reason":
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame, pages


def fetch_holder_history(code: str) -> pd.DataFrame:
    if len(code) != 9 or code[:3] not in ("sh.", "sz.") or not code[3:].isdigit():
        raise ValueError(f"invalid A-share code: {code}")
    params = {"sortColumns": "END_DATE", "sortTypes": "-1", "pageSize": "500",
              "reportName": "RPT_HOLDERNUM_DET", "columns": COLUMNS_PARAM,
              "quoteColumns": "f2,f3", "filter": f'(SECURITY_CODE="{code[3:]}")',
              "source": "WEB", "client": "WEB"}
    frames = []
    page = 1
    pages = 1
    while page <= pages:
        data = free_http.get(URL, {**params, "pageNumber": str(page)},
                             referer=f"https://data.eastmoney.com/gdhs/detail/{code[3:]}.html")
        frame, reported_pages = parse_holder_page(data, code)
        if page > 1 and reported_pages != pages:
            raise free_http.FreeDataError(f"Eastmoney holder pagination changed for {code}")
        pages = reported_pages
        frames.append(frame)
        page += 1
    return pd.concat(frames, ignore_index=True).drop_duplicates(["stat_date", "announce_date"]).sort_values(
        ["stat_date", "announce_date"]).reset_index(drop=True)
