"""Current personal-book market data, separate from research panels."""

import json
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from alphasieve.config import Settings
from alphasieve.data.providers.baostock import BaoStockSession
from alphasieve.errors import validation_error


def _cache_path(settings: Settings, code: str, end: str) -> Path:
    return settings.hot_root / "book" / "market" / f"{code}-{end}.json"


def _fetch_closes(code: str, start: str, end: str, benchmark: bool = False) -> pd.DataFrame:
    with BaoStockSession() as session:
        return session.index_daily(code, start, end) if benchmark else session.daily(code, start, end)


def recent_closes(settings: Settings, codes: list[str], benchmark: str, end: str | None = None,
                  refresh: bool = False) -> dict[str, pd.Series]:
    end = end or date.today().isoformat()
    start = (date.fromisoformat(end) - timedelta(days=110)).isoformat()
    result = {}
    for code in sorted(set([*codes, benchmark])):
        path = _cache_path(settings, code, end)
        if path.exists() and not refresh:
            rows = json.loads(path.read_text(encoding="utf-8"))
        else:
            frame = _fetch_closes(code, start, end, code == benchmark)
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


def current_industry(settings: Settings, codes: list[str]) -> tuple[dict[str, str], dict]:
    """Latest locally synced CSRC industry file; this is a current snapshot, not PIT."""
    path = settings.raw_dir / "baostock" / "industry.parquet"
    if not path.is_file():
        return {}, {"label": "current snapshot, not PIT", "source": "unavailable", "as_of": None}
    frame = pd.read_parquet(path)
    if "fetched_date" in frame:
        frame = frame.sort_values("fetched_date")
    mapping = dict(zip(frame["code"], frame["industry"], strict=True))
    as_of = str(frame["fetched_date"].max()) if "fetched_date" in frame and not frame.empty else None
    return {code: mapping[code] for code in codes if code in mapping and mapping[code]}, {
        "label": "current snapshot, not PIT", "source": "baostock/industry.parquet", "as_of": as_of}
