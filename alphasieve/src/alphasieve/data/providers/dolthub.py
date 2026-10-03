"""Historical CSI constituent weights mirrored by DoltHub from Tushare."""

import json

import pandas as pd

from alphasieve.data.providers import free_http

API_URL = "https://www.dolthub.com/api/v1alpha1/chenditc/investment_data/master"
INDEX_START = {"000905.SH": 2007, "399300.SZ": 2005, "000906.SH": 2007, "000852.SH": 2014}
COLUMNS = ["index_code", "stock_code", "trade_date", "weight"]
PAGE_SIZE = 1000


def _query(sql: str) -> list[dict]:
    last_error = None
    for attempt in range(3):
        try:
            response = json.loads(free_http.get(API_URL, {"q": sql}))
            if response.get("query_execution_status") != "Success" or not isinstance(response.get("rows"), list):
                raise free_http.FreeDataError(
                    f"DoltHub query failed: {str(response.get('query_execution_message', 'invalid response'))[:160]}")
            return response["rows"]
        except (json.JSONDecodeError, free_http.FreeDataError) as exc:
            last_error = exc
            if attempt == 2:
                break
    raise free_http.FreeDataError(f"DoltHub query retries exhausted: {last_error}") from last_error


def master_hash() -> str:
    rows = _query("SELECT DOLT_HASHOF('master') AS hash")
    if len(rows) != 1 or not isinstance(rows[0].get("hash"), str):
        raise free_http.FreeDataError("DoltHub master hash missing")
    commit = rows[0]["hash"]
    if len(commit) != 32 or not commit.isalnum():
        raise free_http.FreeDataError("DoltHub master hash invalid")
    return commit


def _year_rows(index_code: str, year: int, commit: str) -> list[dict]:
    scope = (f"FROM ts_index_weight AS OF '{commit}' WHERE index_code = '{index_code}' "
             f"AND trade_date >= '{year}-01-01' AND trade_date < '{year + 1}-01-01'")
    counts = _query(f"SELECT COUNT(*) AS n {scope}")
    if len(counts) != 1:
        raise free_http.FreeDataError(f"DoltHub count missing for {index_code} {year}")
    count = int(counts[0]["n"])
    rows = []
    for offset in range(0, count, PAGE_SIZE):
        page = _query(f"SELECT index_code, stock_code, trade_date, weight {scope} "
                      f"ORDER BY trade_date, stock_code LIMIT {PAGE_SIZE} OFFSET {offset}")
        if len(page) != min(PAGE_SIZE, count - offset):
            raise free_http.FreeDataError(f"DoltHub incomplete page for {index_code} {year} offset {offset}")
        rows.extend(page)
    return rows


def fetch_index_weights(index_code: str, start_year: int, end_year: int, commit: str) -> pd.DataFrame:
    if index_code not in INDEX_START or start_year < INDEX_START[index_code] or end_year < start_year:
        raise ValueError(f"unsupported DoltHub index/year range: {index_code}, {start_year}-{end_year}")
    if len(commit) != 32 or not commit.isalnum():
        raise ValueError("invalid DoltHub commit hash")
    rows = []
    for year in range(start_year, end_year + 1):
        rows.extend(_year_rows(index_code, year, commit))
    frame = pd.DataFrame(rows, columns=COLUMNS)
    if frame.empty:
        return frame.astype({"weight": "float64"})
    if (frame["index_code"] != index_code).any() or frame[COLUMNS].isna().any().any():
        raise free_http.FreeDataError(f"DoltHub invalid values for {index_code}")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    frame["weight"] = pd.to_numeric(frame["weight"], errors="coerce")
    if (frame["trade_date"].isna().any() or frame["weight"].isna().any()
            or (frame["weight"] < 0).any() or frame.duplicated(["trade_date", "stock_code"]).any()):
        raise free_http.FreeDataError(f"DoltHub invalid weights for {index_code}")
    return frame.sort_values(["trade_date", "stock_code"]).reset_index(drop=True)
