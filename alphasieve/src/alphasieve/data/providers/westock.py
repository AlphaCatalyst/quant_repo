"""westock-data CLI wrapper: full financial statements (2000+), daily fund flow (2020+) and margin snapshots.

The CLI prints a JSON array for batch requests and silently drops unknown codes. Codes are written without the
dot (``sh600000``); this module converts to and from the BaoStock style (``sh.600000``) used everywhere else.
Margin data is served one code and one date per call, so it can only be collected going forward (D-30).
"""

import json
import os
import re
import subprocess
import time
from datetime import date, timedelta

import pandas as pd

CLI = os.environ.get("ALPHASIEVE_WESTOCK_CLI", "/usr/local/bin/westock-data")
CALL_TIMEOUT_S = 300
KLINE_RETRIES = 6
KLINE_GAP_S = 1.5
KLINE_BACKOFF_S = 20
STATEMENTS = ("lrb", "zcfz", "xjll")
FLOW_FIELDS = ("MainNetFlow", "JumboNetFlow", "BlockNetFlow", "MidNetFlow", "SmallNetFlow",
               "MainInFlow", "MainOutFlow", "RetailInFlow", "RetailOutFlow")
MARGIN_FIELDS = ("FinanceValue", "SecurityValue", "FinanceBuyValue", "FinanceRefundValue", "TradingValue")
MEDIA = re.compile(r"报$|网|新闻|财经|杂志|周刊|资讯|日报|时报|快讯")


class WestockError(RuntimeError):
    pass


def to_westock(code: str) -> str:
    return code.replace(".", "")


def from_westock(code: str) -> str:
    return f"{code[:2]}.{code[2:]}"


def _call(args: list[str]):
    proc = subprocess.run([CLI, *args, "--raw"], capture_output=True, text=True, timeout=CALL_TIMEOUT_S)
    out = proc.stdout.strip()
    try:
        return json.loads(out) if out else None
    except json.JSONDecodeError as exc:
        raise WestockError(f"{' '.join(args[:2])}: non-JSON output: {(out or proc.stderr)[:200]}") from exc


def _frame(rows, numeric: tuple[str, ...] | None = None) -> pd.DataFrame:
    df = pd.DataFrame(rows if isinstance(rows, list) else [])
    if df.empty:
        return df
    df["code"] = df["code"].map(from_westock)
    skip = {"code", "symbol", "SecuCode", "date", "_date", "EndDate", "InfoPublDate", "EnterpriseType", "name"}
    for col in numeric or [c for c in df.columns if c not in skip]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.drop(columns=[c for c in ("symbol", "SecuCode", "_date") if c in df.columns])


def _batched(fetch, codes: list[str]) -> pd.DataFrame:
    """Batch mode degrades silently under load and drops codes; codes missing from the answer are asked alone."""
    df = fetch(codes)
    got = set(df["code"]) if not df.empty else set()
    retries = [fetch([c]) for c in codes if c not in got] if len(codes) > 1 else []
    return pd.concat([df, *[r for r in retries if not r.empty]], ignore_index=True)


def is_broker_report(title: str) -> bool:
    match = re.match(r"^【([^】]+)】", title)
    return bool(match) and not MEDIA.search(match.group(1))


def report_call(args: list[str], retries: int = 4):
    """Report endpoint occasionally emits a preamble or times out during a long crawl."""
    for attempt in range(retries):
        try:
            proc = subprocess.run([CLI, *args, "--raw"], capture_output=True, text=True, timeout=90)
            out = proc.stdout.strip()
            pos = min((p for p in (out.find("["), out.find("{")) if p >= 0), default=-1)
            if pos >= 0:
                return json.loads(out[pos:])
            if "数据为空" in out or "暂无" in out:
                return []
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            pass
        if attempt + 1 < retries:
            time.sleep(2 ** attempt)
    raise WestockError(f"westock {' '.join(args[:3])} failed after {retries} attempts")


def report_page(code: str, offset: int = 0) -> list[dict]:
    result = report_call(["report", to_westock(code), "--limit", "20", "--offset", str(offset)])
    return result if isinstance(result, list) else result.get("data") or []


def report_detail(report_id: str) -> dict:
    result = report_call(["report", "detail", report_id])
    rows = result if isinstance(result, list) else result.get("data") or []
    return rows[0] if rows and isinstance(rows[0], dict) else {}


def consensus(codes: list[str]) -> pd.DataFrame:
    def fetch(batch: list[str]) -> pd.DataFrame:
        result = _call(["consensus", ",".join(map(to_westock, batch))]) or {}
        # a multi-code call answers {"sections": [...]}; a single-code call answers the bare list
        if isinstance(result, list) and len(batch) == 1:
            result = {"sections": [result]}
        sections = result.get("sections", []) if isinstance(result, dict) else []
        if len(sections) != len(batch):
            return pd.DataFrame(columns=["code"])
        rows = [{"code": code, **row} for code, section in zip(batch, sections, strict=True) for row in section]
        return pd.DataFrame(rows, columns=["code", "year", "eps", "revenue", "netProfit", "targetPrice",
                                           "pe", "pb", "ps", "revenueYoy", "netProfitYoy", "institutionCnt"])

    return _batched(fetch, codes)


def index_constituents(indices: list[str]) -> pd.DataFrame:
    def fetch(batch: list[str]) -> pd.DataFrame:
        # The endpoint returns a flat list without index identifiers; query one index at a time.
        if len(batch) > 1:
            return pd.concat([fetch([c]) for c in batch], ignore_index=True)
        rows = _call(["index", "constituent", to_westock(batch[0])]) or []
        return pd.DataFrame([{"index": batch[0], "code": from_westock(r["code"]),
                              "name": r.get("name", "")} for r in rows if r.get("code")])

    return fetch(indices)


def sector_list(level: int) -> pd.DataFrame:
    rows = _call(["sector", "list", f"industry_list_sw{level}"]) or []
    return pd.DataFrame([{"level": level, "sector_code": r["code"], "sector_name": r.get("name", ""),
                          "sector_id": r.get("sectorCode", "")} for r in rows if r.get("code")])


def sector_constituents(sectors: list[str]) -> pd.DataFrame:
    rows = []
    for sector in sectors:
        result = _call(["sector", "constituent", sector]) or []
        rows.extend({"sector_code": sector, "code": from_westock(r["code"]), "name": r.get("name", "")}
                    for r in result if r.get("code"))
    return pd.DataFrame(rows, columns=["sector_code", "code", "name"]).drop_duplicates(
        ["sector_code", "code"])


def kline(codes: list[str], start: str, end: str) -> pd.DataFrame:
    def fetch(batch: list[str]) -> pd.DataFrame:
        # Kline rows contain no symbol, so retain the code by requesting each series separately.
        if len(batch) > 1:
            return pd.concat([fetch([c]) for c in batch], ignore_index=True)
        for attempt in range(KLINE_RETRIES):
            time.sleep(KLINE_GAP_S)
            rows = _call(["kline", batch[0], "--period", "day", "--start", start, "--end", end]) or []
            # a throttled call answers {"success": false, ...} instead of a list
            if isinstance(rows, list):
                break
            time.sleep(KLINE_BACKOFF_S * (attempt + 1))
        else:
            raise WestockError(f"kline {batch[0]} {start}..{end}: still throttled after {KLINE_RETRIES} attempts")
        return pd.DataFrame([{"code": batch[0], **r} for r in rows], columns=["code", "date", "open",
                            "last", "high", "low", "volume", "amount", "exchange"])

    return fetch(codes)


def quote(codes: list[str]) -> dict[str, dict]:
    """Current quote rows keyed by dotted code; retry symbols omitted by a batch response."""
    if not codes:
        return {}

    def fetch(batch: list[str]) -> dict[str, dict]:
        response = _call(["quote", ",".join(map(to_westock, batch))])
        entries = response.get("data", []) if isinstance(response, dict) else response
        result = {}
        for entry in entries if isinstance(entries, list) else []:
            row = entry.get("data", entry) if isinstance(entry, dict) else None
            symbol = (row or {}).get("code") or entry.get("symbol")
            if isinstance(symbol, str) and re.fullmatch(r"(sh|sz|bj)\d{6}", symbol):
                result[from_westock(symbol)] = row
        return result

    result = fetch(codes)
    for code in codes:
        if code not in result and len(codes) > 1:
            result.update(fetch([code]))
    return result


_BOND_SCHEDULES = {
    "coupons": "couponRateList",
    "puts": "putDetail",
    "calls": "callDetail",
    "revisions": "changeDetail",
    "cashflows": "cashflowDetail",
}
_BOND_DATE_FIELDS = {
    "issueStartDate", "issueEndDate", "couponStart", "couponEnd", "dueDate",
    "convertStartDate", "buybackStartDate", "nextPaymentDate",
}


def _bond_date(value):
    if isinstance(value, str) and re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def _bond_schedule(value) -> list[dict]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return value if isinstance(value, list) else []


def _bond_entries(response) -> list[dict]:
    """Extract native batch rows; the CLI's single-code display is parsed separately."""
    if isinstance(response, dict) and isinstance(response.get("data"), list):
        return [entry.get("data", entry) for entry in response["data"] if isinstance(entry, dict)]
    return []


def bond_detail(codes: list[str]) -> dict[str, pd.DataFrame]:
    """Current bond terms and clause schedules, with one static row per bond.

    ``terms`` includes full vendor clause text where available. These are current
    observations; the provider does not supply historical conversion-price changes.
    """
    tables: dict[str, list[dict]] = {key: [] for key in ("terms", *_BOND_SCHEDULES)}
    if not codes:
        return {key: pd.DataFrame(columns=["code"]) for key in tables}

    def fetch(batch: list[str]):
        symbols = [to_westock(code) for code in batch]
        # A one-code CLI request is reformatted into Chinese display sections and
        # loses the full clause text. Duplicate the symbol to select native batch JSON.
        if len(symbols) == 1:
            symbols *= 2
        response = _call(["bond", "detail", ",".join(symbols), "--terms", "--schedule"])
        if isinstance(response, dict) and response.get("success") is False:
            raise WestockError(f"bond detail {','.join(batch)}: {response.get('status', 'failed')}")
        entries = _bond_entries(response)
        if not entries and isinstance(response, dict) and "sections" in response:
            # Some CLI versions still return display sections for a duplicated code.
            sections = response["sections"]
            if sections and isinstance(sections[0], list):
                summary = {row["项目"]: row.get("内容") for row in sections[0]
                           if isinstance(row, dict) and "项目" in row}
                entries = [{"code": summary.get("债券代码", symbols[0]),
                            "issuer": summary.get("发行人"), "stockCode": summary.get("正股代码"),
                            "dueDate": summary.get("到期日"), "convertPrice": summary.get("转股价"),
                            **{field: sections[index] if len(sections) > index else []
                               for index, field in enumerate(_BOND_SCHEDULES.values(), 1)}}]
        return entries

    # Small batches reduce dropped records. Retry every omitted symbol separately.
    for offset in range(0, len(codes), 4):
        batch = codes[offset:offset + 4]
        entries = fetch(batch)
        found = {row.get("code") for row in entries}
        for code in batch:
            if to_westock(code) not in found and len(batch) > 1:
                entries.extend(fetch([code]))
        for row in entries:
            symbol = row.get("code")
            if not isinstance(symbol, str) or not re.fullmatch(r"(?:sh|sz|bj)\d{6}", symbol):
                continue
            code = from_westock(symbol)
            static = {key: _bond_date(value) if key in _BOND_DATE_FIELDS else value
                      for key, value in row.items() if key not in _BOND_SCHEDULES.values()}
            static["code"] = code
            tables["terms"].append(static)
            for table, field in _BOND_SCHEDULES.items():
                for item in _bond_schedule(row.get(field)):
                    if isinstance(item, dict):
                        tables[table].append({"code": code, **{
                            key: _bond_date(value) if key.endswith("Date") else value
                            for key, value in item.items()}})
    return {name: pd.DataFrame(rows).drop_duplicates().reset_index(drop=True)
            if rows else pd.DataFrame(columns=["code"]) for name, rows in tables.items()}


def bond_daily(codes: list[str], start: str, end: str) -> pd.DataFrame:
    """Daily CB OHLCV; dotted codes in, dotted codes out."""
    if not codes:
        return pd.DataFrame(columns=["code", "date", "open", "last", "high", "low",
                                     "volume", "amount", "exchange"])
    # The CLI silently caps range responses at 250 rows. Page backwards until
    # the requested start, including retired bonds whose final quote is old.
    pages = []
    for code in codes:
        first_page = len(pages)
        cursor = end
        while cursor >= start:
            page = kline([to_westock(code)], start, cursor)
            if page.empty:
                # Retired bonds can answer [] for a long range ending years
                # after their last trade, even when their early annual slices
                # have history. Query from listing forward in that case.
                if len(pages) == first_page and int(end[:4]) > int(start[:4]):
                    blank_years = 0
                    for year in range(int(start[:4]), int(end[:4]) + 1):
                        lo, hi = max(start, f"{year}-01-01"), min(end, f"{year}-12-31")
                        if lo > hi:
                            continue
                        annual = kline([to_westock(code)], lo, hi)
                        if annual.empty:
                            blank_years += 1
                            if blank_years >= 2:
                                break
                        else:
                            pages.append(annual)
                            blank_years = 0
                break
            pages.append(page)
            oldest = str(page["date"].min())
            if oldest <= start:
                break
            next_cursor = (date.fromisoformat(oldest) - timedelta(days=1)).isoformat()
            if next_cursor >= cursor:
                raise WestockError(f"bond kline {code}: pagination did not advance")
            cursor = next_cursor
    result = pd.concat(pages, ignore_index=True) if pages else pd.DataFrame(
        columns=["code", "date", "open", "last", "high", "low", "volume", "amount", "exchange"])
    if not result.empty:
        result["code"] = result["code"].map(from_westock)
        for field in ("open", "last", "high", "low", "volume", "amount", "exchange"):
            result[field] = pd.to_numeric(result[field], errors="coerce")
        result = result.drop_duplicates(["code", "date"]).sort_values(["code", "date"])
    return result.reset_index(drop=True)


def bond_quote(codes: list[str]) -> pd.DataFrame:
    """Current CB quote and valuation fields, preserving the quote's market date."""
    rows = quote(codes)
    return pd.DataFrame([{"code": code, **{key: value for key, value in row.items()
                                            if key not in ("code", "symbol")}}
                         for code, row in rows.items()])


def bond_universe() -> pd.DataFrame:
    """Eastmoney/AKShare CB issue list, including entries no longer trading.

    The endpoint has no delisting date or status; current issue quote fields
    are observations, not historical point-in-time values.
    """
    import akshare as ak  # lazy: the rest of the westock provider does not need AKShare

    rename = {
        "债券代码": "code", "债券简称": "name", "申购日期": "subscription_date",
        "申购代码": "subscription_code", "申购上限": "subscription_limit",
        "正股代码": "stock_code", "正股简称": "stock_name", "正股价": "stock_price",
        "转股价": "convert_price", "转股价值": "equity_value", "债现价": "bond_price",
        "转股溢价率": "equity_premium", "原股东配售-股权登记日": "allotment_record_date",
        "原股东配售-每股配售额": "allotment_per_share", "发行规模": "issue_size",
        "中签号发布日": "lottery_publish_date", "中签率": "lottery_rate",
        "上市时间": "list_date", "信用评级": "rating",
    }
    frame = ak.bond_zh_cov().rename(columns=rename)
    frame["code"] = frame["code"].astype(str).str.zfill(6).map(
        lambda value: f"{'sh' if value.startswith('11') else 'sz'}.{value}")
    for field in ("subscription_date", "allotment_record_date", "lottery_publish_date", "list_date"):
        if field in frame:
            frame[field] = pd.to_datetime(frame[field], errors="coerce").dt.strftime("%Y-%m-%d")
    return frame.drop_duplicates("code").reset_index(drop=True)


def statements(codes: list[str], kind: str, start: str, end: str) -> pd.DataFrame:
    """One statement type for a batch of codes; one row per (code, EndDate) with InfoPublDate as YYYY-MM-DD."""
    if kind not in STATEMENTS:
        raise ValueError(f"unknown statement {kind}")
    df = _batched(lambda cs: _frame(_call(["finance", ",".join(map(to_westock, cs)), "--type", kind,
                                           "--start", start, "--end", end])), codes)
    if df.empty:
        return df
    df["InfoPublDate"] = df["InfoPublDate"].fillna("").str[:10]
    return df.drop(columns=["date"], errors="ignore").drop_duplicates(["code", "EndDate"], keep="last")


def fund_flow(codes: list[str], start: str, end: str) -> pd.DataFrame:
    """Daily fund flow by order size; the service caps rows per request, so callers should query one year at a time."""
    df = _batched(lambda cs: _frame(_call(["fund", "flow", ",".join(map(to_westock, cs)), "--start", start,
                                           "--end", end]), FLOW_FIELDS), codes)
    if df.empty:
        return df
    return df[["code", "date", *[f for f in FLOW_FIELDS if f in df.columns]]].drop_duplicates(["code", "date"])


def margin(code: str, day: str) -> dict | None:
    data = _call(["fund", "margin", to_westock(code), "--date", day])
    row = (data or {}).get("data") if isinstance(data, dict) else None
    if not row or row.get("date") != day:
        return None
    return {"code": code, "date": day, **{f: pd.to_numeric(row.get(f), errors="coerce") for f in MARGIN_FIELDS}}
