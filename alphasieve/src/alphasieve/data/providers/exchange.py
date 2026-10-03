"""SSE/SZSE daily margin details, with unmatched fields kept empty."""

import io
import json

import pandas as pd

from alphasieve.data.providers import free_http

SZ_URL = "https://www.szse.cn/api/report/ShowReport"
SSE_URL = "https://query.sse.com.cn/marketdata/tradedata/queryMargin.do"
MARGIN_FIELDS = ("FinanceValue", "SecurityValue", "FinanceBuyValue", "FinanceRefundValue", "TradingValue")
COLUMNS = ["code", "date", *MARGIN_FIELDS, "security_sell_volume", "security_balance_volume",
           "security_refund_volume", "name", "exchange"]


def _number(value):
    return pd.to_numeric(str(value).replace(",", ""), errors="coerce")


def parse_szse(data: bytes, day: str) -> pd.DataFrame:
    if not data.startswith(b"PK"):
        raise free_http.FreeDataError(f"SZSE margin workbook invalid for {day}")
    try:
        source = pd.read_excel(io.BytesIO(data), engine="openpyxl", dtype={"证券代码": str})
    except (ImportError, ValueError) as exc:
        raise free_http.FreeDataError(f"SZSE margin workbook unreadable for {day}: {exc}") from exc
    source.columns = [str(column).split("(")[0] for column in source.columns]
    required = {"证券代码", "融资买入额", "融资余额", "融券卖出量", "融券余量", "融券余额", "融资融券余额"}
    if not required.issubset(source.columns):
        raise free_http.FreeDataError(f"SZSE margin columns missing for {day}")
    rows = []
    for row in source.to_dict("records"):
        code = str(row["证券代码"]).zfill(6)
        if len(code) != 6 or not code.isdigit():
            raise free_http.FreeDataError(f"SZSE margin code invalid for {day}")
        rows.append({"code": f"sz.{code}", "date": day, "FinanceValue": _number(row["融资余额"]),
                     "SecurityValue": _number(row["融券余额"]), "FinanceBuyValue": _number(row["融资买入额"]),
                     "FinanceRefundValue": float("nan"), "TradingValue": _number(row["融资融券余额"]),
                     "security_sell_volume": _number(row["融券卖出量"]),
                     "security_balance_volume": _number(row["融券余量"]),
                     "security_refund_volume": float("nan"),
                     "name": str(row.get("证券简称") or "").replace("&nbsp;", ""), "exchange": "szse"})
    return pd.DataFrame(rows, columns=COLUMNS).drop_duplicates("code").reset_index(drop=True)


def parse_sse(data: bytes, day: str) -> pd.DataFrame:
    payload = json.loads(data)
    rows = payload.get("result")
    if not isinstance(rows, list):
        raise free_http.FreeDataError(f"SSE margin response invalid for {day}")
    result = []
    for row in rows:
        code = str(row.get("stockCode", "")).zfill(6)
        if len(code) != 6 or not code.isdigit() or row.get("opDate") != day.replace("-", ""):
            raise free_http.FreeDataError(f"SSE margin code/date invalid for {day}")
        result.append({"code": f"sh.{code}", "date": day, "FinanceValue": _number(row.get("rzye")),
                       "SecurityValue": float("nan"), "FinanceBuyValue": _number(row.get("rzmre")),
                       "FinanceRefundValue": _number(row.get("rzche")), "TradingValue": float("nan"),
                       "security_sell_volume": _number(row.get("rqmcl")),
                       "security_balance_volume": _number(row.get("rqyl")),
                       "security_refund_volume": _number(row.get("rqchl")),
                       "name": row.get("securityAbbr") or "", "exchange": "sse"})
    return pd.DataFrame(result, columns=COLUMNS).drop_duplicates("code").reset_index(drop=True)


def fetch_margin_day(day: str) -> pd.DataFrame:
    day = pd.Timestamp(day).strftime("%Y-%m-%d")
    compact = day.replace("-", "")
    sz = free_http.get(SZ_URL, {"SHOWTYPE": "xlsx", "CATALOGID": "1837_xxpl", "txtDate": day,
                                "tab2PAGENO": "1", "TABKEY": "tab2"},
                       referer="https://www.szse.cn/disclosure/margin/margin/index.html")
    params = {"isPagination": "true", "tabType": "mxtype", "detailsDate": compact,
              "stockCode": "", "beginDate": "", "endDate": "", "pageHelp.pageSize": "5000"}
    sh_pages = []
    page_count = 1
    page = 1
    while page <= page_count:
        sh = free_http.get(SSE_URL, {**params, "pageHelp.pageNo": str(page)}, referer="https://www.sse.com.cn/")
        payload = json.loads(sh)
        if page == 1:
            page_count = int((payload.get("pageHelp") or {}).get("pageCount") or 1)
            if not 1 <= page_count <= 100:
                raise free_http.FreeDataError(f"SSE margin page count invalid for {day}")
        sh_pages.append(parse_sse(sh, day))
        page += 1
    sz_rows, sh_rows = parse_szse(sz, day), pd.concat(sh_pages, ignore_index=True)
    if sz_rows.empty != sh_rows.empty:
        raise free_http.FreeDataError(f"only one exchange returned margin rows for {day}")
    return pd.concat([sz_rows, sh_rows], ignore_index=True).drop_duplicates("code")
