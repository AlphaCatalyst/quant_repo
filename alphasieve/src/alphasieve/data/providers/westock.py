"""westock-data CLI wrapper: full financial statements (2000+), daily fund flow (2020+) and margin snapshots.

The CLI prints a JSON array for batch requests and silently drops unknown codes. Codes are written without the
dot (``sh600000``); this module converts to and from the BaoStock style (``sh.600000``) used everywhere else.
Margin data is served one code and one date per call, so it can only be collected going forward (D-30).
"""

import json
import os
import subprocess

import pandas as pd

CLI = os.environ.get("ALPHASIEVE_WESTOCK_CLI", "/usr/local/bin/westock-data")
CALL_TIMEOUT_S = 300
STATEMENTS = ("lrb", "zcfz", "xjll")
FLOW_FIELDS = ("MainNetFlow", "JumboNetFlow", "BlockNetFlow", "MidNetFlow", "SmallNetFlow",
               "MainInFlow", "MainOutFlow", "RetailInFlow", "RetailOutFlow")
MARGIN_FIELDS = ("FinanceValue", "SecurityValue", "FinanceBuyValue", "FinanceRefundValue", "TradingValue")


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
