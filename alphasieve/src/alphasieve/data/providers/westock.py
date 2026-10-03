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
