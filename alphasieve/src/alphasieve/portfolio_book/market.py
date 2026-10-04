"""Current personal-book market data, separate from research panels."""

import json
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from alphasieve.config import Settings
from alphasieve.data.providers import westock
from alphasieve.data.providers.baostock import BaoStockSession
from alphasieve.errors import validation_error


def _cache_path(settings: Settings, code: str, end: str) -> Path:
    return settings.hot_root / "book" / "market" / f"{code}-{end}.json"


def _fetch_closes(code: str, start: str, end: str, benchmark: bool = False) -> pd.DataFrame:
    with BaoStockSession() as session:
        return session.index_daily(code, start, end) if benchmark else session.daily(code, start, end)


def asset_class(code: str, quote: dict | None = None) -> str:
    if code == "CASH":
        return "cash"
    exchange, number = code.split(".", 1)
    if (exchange == "sh" and number.startswith(("11", "13"))) or (exchange == "sz" and number.startswith("12")):
        return "convertible_bond"
    if (quote or {}).get("bond_convertible") or (quote or {}).get("bond_equity_value") is not None:
        return "convertible_bond"
    if (exchange == "sh" and number.startswith(("50", "51", "56", "58"))) or (
        exchange == "sz" and number.startswith("15")
    ):
        return "etf"
    if (exchange == "sh" and number.startswith(("60", "68", "90"))) or (
        exchange == "sz" and number.startswith(("00", "30"))
    ) or exchange == "bj":
        return "stock"
    return "other"


def current_quotes(settings: Settings, codes: list[str], end: str | None = None,
                   refresh: bool = False) -> dict[str, dict]:
    """One market snapshot per calendar day, stored only below book/market."""
    end = end or date.today().isoformat()
    path = settings.hot_root / "book" / "market" / f"quote-{end}.json"
    cached = json.loads(path.read_text(encoding="utf-8")) if path.exists() and not refresh else {}
    missing = [code for code in sorted(set(codes)) if code not in cached]
    if missing:
        try:
            cached.update(westock.quote(missing))
        except (westock.WestockError, OSError, TimeoutError):
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(cached, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        os.replace(tmp, path)
    return {code: cached[code] for code in codes if code in cached}


def _westock_closes(code: str, start: str, end: str) -> pd.DataFrame:
    frame = westock.kline([westock.to_westock(code)], start, end)
    if frame.empty:
        return frame
    return frame.rename(columns={"last": "close"})


def recent_closes(settings: Settings, codes: list[str], benchmark: str, end: str | None = None,
                  refresh: bool = False, asset_classes: dict[str, str] | None = None) -> dict[str, pd.Series]:
    end = end or date.today().isoformat()
    start = (date.fromisoformat(end) - timedelta(days=110)).isoformat()
    result = {}
    for code in sorted(set([*codes, benchmark])):
        path = _cache_path(settings, code, end)
        if path.exists() and not refresh:
            rows = json.loads(path.read_text(encoding="utf-8"))
        else:
            kind = (asset_classes or {}).get(code, asset_class(code))
            try:
                if code != benchmark and kind in {"etf", "convertible_bond"}:
                    frame = _westock_closes(code, start, end)
                elif code != benchmark and kind == "other":
                    frame = pd.DataFrame()
                else:
                    frame = _fetch_closes(code, start, end, code == benchmark)
                    if frame.empty and code != benchmark:
                        frame = _westock_closes(code, start, end)
            except (westock.WestockError, OSError, TimeoutError):
                frame = pd.DataFrame()
            if frame.empty or not {"date", "close"}.issubset(frame.columns):
                rows = []
            else:
                frame = frame[["date", "close"]].copy()
                frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
                frame = frame.dropna().drop_duplicates("date", keep="last").sort_values("date")
                rows = frame.to_dict("records")
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        result[code] = pd.Series({row["date"]: float(row["close"]) for row in rows}, dtype=float).sort_index()
    if result[benchmark].empty:
        raise validation_error("benchmark prices unavailable", benchmark=benchmark)
    return result


def history_closes(settings: Settings, codes: list[str], benchmark: str, start: str,
                   end: str) -> tuple[dict[str, pd.Series], dict]:
    """Daily closes for a personal book; the local CSI300 TR series is preferred."""
    result: dict[str, pd.Series] = {}
    source = "BaoStock price index"
    if benchmark == "sh.000300":
        path = settings.raw_dir / "csindex" / "total_return" / "H00300.parquet"
        if path.is_file():
            frame = pd.read_parquet(path, columns=["date", "close"])
            frame = frame[(frame["date"] >= start) & (frame["date"] <= end)]
            if not frame.empty:
                result[benchmark] = pd.Series(pd.to_numeric(frame["close"], errors="coerce").values,
                                               index=frame["date"].astype(str)).dropna().sort_index()
                source = "csindex CSI300 total return H00300"
    for code in sorted(set([*codes, benchmark])):
        if code in result:
            continue
        path = settings.hot_root / "book" / "market" / f"history-{code}-{start}-{end}.json"
        if path.is_file():
            rows = json.loads(path.read_text(encoding="utf-8"))
        else:
            kind = asset_class(code)
            try:
                if code != benchmark and kind in {"etf", "convertible_bond"}:
                    frame = _westock_closes(code, start, end)
                else:
                    frame = _fetch_closes(code, start, end, benchmark=code == benchmark)
                    if frame.empty and code != benchmark:
                        frame = _westock_closes(code, start, end)
            except (westock.WestockError, OSError, TimeoutError):
                frame = pd.DataFrame()
            if frame.empty or not {"date", "close"}.issubset(frame.columns):
                rows = []
            else:
                frame = frame[["date", "close"]].copy()
                frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
                frame = frame.dropna().drop_duplicates("date", keep="last").sort_values("date")
                rows = frame.to_dict("records")
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        result[code] = pd.Series({str(row["date"]): float(row["close"]) for row in rows}, dtype=float).sort_index()
    if result[benchmark].empty:
        raise validation_error("benchmark prices unavailable", benchmark=benchmark)
    return result, {"code": benchmark, "source": source}


def current_industry(settings: Settings, codes: list[str]) -> tuple[dict[str, str], dict]:
    """Latest local SW L1 snapshot, with CSRC fallback; neither source is PIT."""
    sw_dir = settings.raw_dir / "westock" / "sw_industry"
    sw_files = sorted(sw_dir.glob("*.parquet")) if sw_dir.is_dir() else []
    sw = {}
    if sw_files:
        frame = pd.read_parquet(sw_files[-1])
        if {"code", "level", "sector_name"}.issubset(frame.columns):
            frame = frame[frame["level"].astype(str) == "1"].drop_duplicates("code", keep="last")
            sw = dict(zip(frame["code"], frame["sector_name"], strict=True))
    path = settings.raw_dir / "baostock" / "industry.parquet"
    if not path.is_file():
        return {code: sw[code] for code in codes if sw.get(code)}, {
            "label": "current snapshot, not PIT", "source": "westock/SW L1" if sw_files else "unavailable",
            "as_of": sw_files[-1].stem if sw_files else None}
    frame = pd.read_parquet(path)
    if "fetched_date" in frame:
        frame = frame.sort_values("fetched_date")
    mapping = dict(zip(frame["code"], frame["industry"], strict=True))
    as_of = str(frame["fetched_date"].max()) if "fetched_date" in frame and not frame.empty else None
    return {code: sw.get(code) or mapping.get(code) for code in codes if sw.get(code) or mapping.get(code)}, {
        "label": "current snapshot, not PIT",
        "source": "westock/SW L1; baostock/industry.parquet fallback" if sw_files else "baostock/industry.parquet",
        "as_of": sw_files[-1].stem if sw_files else as_of}
