"""CSI official total-return daily levels and current constituent weights."""

import io
import json

import pandas as pd

from alphasieve.data.providers import free_http

PERF_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
WEIGHT_URL = "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/closeweight/"
PERF_COLUMNS = ["index_code", "date", "close", "open", "high", "low", "change", "change_pct",
                "volume", "amount", "constituents"]
WEIGHT_COLUMNS = ["index_code", "snapshot_date", "code", "name", "weight", "exchange"]


def parse_index_history(data: bytes, symbol: str) -> pd.DataFrame:
    payload = json.loads(data)
    rows = payload.get("data")
    if payload.get("success") is False or not isinstance(rows, list):
        raise free_http.FreeDataError(f"CSI history response invalid for {symbol}")
    result = []
    for row in rows:
        if row.get("indexCode") != symbol:
            raise free_http.FreeDataError(f"CSI history index mismatch for {symbol}")
        day = pd.to_datetime(row.get("tradeDate"), format="%Y%m%d", errors="coerce")
        close = pd.to_numeric(row.get("close"), errors="coerce")
        if pd.isna(day) or pd.isna(close):
            raise free_http.FreeDataError(f"CSI history date/close missing for {symbol}")
        result.append({"index_code": symbol, "date": day.strftime("%Y-%m-%d"), "close": close,
                       "open": row.get("open"), "high": row.get("high"), "low": row.get("low"),
                       "change": row.get("change"), "change_pct": row.get("changePct"),
                       "volume": row.get("tradingVol"), "amount": row.get("tradingValue"),
                       "constituents": row.get("consNumber")})
    frame = pd.DataFrame(result, columns=PERF_COLUMNS)
    for column in PERF_COLUMNS[3:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.drop_duplicates("date").sort_values("date").reset_index(drop=True)


def fetch_index_history(symbol: str, start: str = "20100101", end: str | None = None) -> pd.DataFrame:
    if symbol not in {"H00905", "H00300", "H00906"}:
        raise ValueError(f"unsupported CSI total-return index: {symbol}")
    end = end or pd.Timestamp.today().strftime("%Y%m%d")
    data = free_http.get(PERF_URL, {"indexCode": symbol, "startDate": start.replace("-", ""),
                                    "endDate": end.replace("-", "")})
    return parse_index_history(data, symbol)


def parse_index_weights(data: bytes, symbol: str) -> pd.DataFrame:
    if not data.startswith(b"\xd0\xcf\x11\xe0"):
        raise free_http.FreeDataError(f"CSI weight workbook invalid for {symbol}")
    try:
        source = pd.read_excel(io.BytesIO(data), engine="xlrd", dtype=str)
    except (ImportError, ValueError) as exc:
        raise free_http.FreeDataError(f"CSI weight workbook unreadable for {symbol}: {exc}") from exc
    source.columns = [str(column).split(" ")[0].split("(")[0] for column in source.columns]
    source = source.rename(columns={"日期Date": "日期", "成份券代码Constituent": "成分券代码",
                                    "成份券名称Constituent": "成分券名称", "交易所Exchange": "交易所"})
    required = {"日期", "指数代码", "成分券代码", "权重", "交易所"}
    if not required.issubset(source.columns) or source.empty:
        raise free_http.FreeDataError(f"CSI weight columns/rows missing for {symbol}")
    frame = pd.DataFrame({"index_code": source["指数代码"].astype(str).str.zfill(6),
                          "snapshot_date": pd.to_datetime(source["日期"].astype(str), format="%Y%m%d",
                                                          errors="coerce").dt.strftime("%Y-%m-%d"),
                          "code": source["成分券代码"].astype(str).str.zfill(6),
                          "name": source.get("成分券名称", pd.Series("", index=source.index)),
                          "weight": pd.to_numeric(source["权重"], errors="coerce"),
                          "exchange": source["交易所"]})
    if frame["snapshot_date"].isna().any() or frame["weight"].isna().any() or \
            (frame["index_code"] != symbol).any():
        raise free_http.FreeDataError(f"CSI weight values invalid for {symbol}")
    exchange = {"上海证券交易所": "sh", "深圳证券交易所": "sz", "Shanghai Stock Exchange": "sh",
                "Shenzhen Stock Exchange": "sz"}
    prefix = frame["exchange"].map(exchange)
    if prefix.isna().any():
        raise free_http.FreeDataError(f"CSI unknown constituent exchange for {symbol}")
    frame["code"] = prefix + "." + frame["code"]
    return frame[WEIGHT_COLUMNS].drop_duplicates(["snapshot_date", "code"]).reset_index(drop=True)


def fetch_index_weights(symbol: str) -> pd.DataFrame:
    if symbol not in {"000905", "000300", "000906"}:
        raise ValueError(f"unsupported CSI constituent index: {symbol}")
    return parse_index_weights(free_http.get(f"{WEIGHT_URL}{symbol}closeweight.xls"), symbol)
