import json
import os
import shutil
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from alphasieve.config import Settings
from alphasieve.data.providers.baostock import INDEX_MEMBER_QUERIES, BaoStockSession
from alphasieve.data.universe import universe_config
from alphasieve.util import canonical_json, file_sha256, sha256_hex, utcnow_iso

SOURCE = "baostock"
HISTORY_START = "2011-01-01"
MEMBERS_START = "2012-01-01"
INDEX_CODES = {"hs300": "sh.000300", "zz500": "sh.000905", "csi800": "sh.000906"}
FINANCIAL_TABLES = ("profit", "growth")
FINANCIAL_PUBLICATION_LAG_DAYS = 150


def raw_root(settings: Settings, universe: str | None = None) -> Path:
    if universe in (None, "csi800"):
        return settings.raw_dir / SOURCE
    return settings.raw_dir / universe_config(settings, universe)["raw_subdir"]


def _dataset(name: str, universe: str | None) -> str:
    return name if universe in (None, "csi800") else f"{universe}:{name}"


def history_start(settings: Settings, universe: str | None = None) -> str:
    return HISTORY_START if universe in (None, "csi800") else universe_config(settings, universe)["history_start"]


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def record_snapshot(conn: sqlite3.Connection, dataset: str, params: dict, paths: list[Path], rows: int) -> str:
    content_hash = sha256_hex("".join(sorted(f"{p.name}:{file_sha256(p)}" for p in paths if p.exists())))
    snapshot_id = sha256_hex(canonical_json({"dataset": dataset, "params": params, "content": content_hash}))[:16]
    root = str(paths[0].parent) if paths else ""
    conn.execute(
        "INSERT OR REPLACE INTO data_snapshots (snapshot_id, source, dataset, params_json, rows, content_hash, path,"
        " fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (snapshot_id, SOURCE, dataset, canonical_json(params), rows, content_hash, root, utcnow_iso()),
    )
    return snapshot_id


def latest_trading_day(settings: Settings, on_or_before: str | None = None, universe: str | None = None) -> str:
    cal = load_calendar(settings, universe)
    limit = on_or_before or date.today().isoformat()
    days = cal[cal <= limit]
    return days.iloc[-1]


def load_calendar(settings: Settings, universe: str | None = None) -> pd.Series:
    df = pd.read_parquet(raw_root(settings, universe) / "trade_dates.parquet")
    return df.loc[df["is_trading_day"] == 1, "calendar_date"].reset_index(drop=True)


def sync_reference(settings: Settings, conn: sqlite3.Connection, end: str, universe: str | None = None) -> dict:
    root = raw_root(settings, universe)
    cal_start = f"{int(history_start(settings, universe)[:4]) - 1}-01-01"
    with BaoStockSession() as s:
        cal = s.trade_dates(cal_start, (datetime.fromisoformat(end) + timedelta(days=30)).date().isoformat())
        _write_parquet(cal, root / "trade_dates.parquet")
        basic = s.stock_basic()
        _write_parquet(basic, root / "stock_basic.parquet")
        industry = s.industry()
        industry["fetched_date"] = date.today().isoformat()
        _write_parquet(industry, root / "industry.parquet")
    out = {}
    for name, df in (("trade_dates", cal), ("stock_basic", basic), ("industry", industry)):
        snap = record_snapshot(conn, _dataset(name, universe), {"end": end}, [root / f"{name}.parquet"], len(df))
        out[name] = {"rows": len(df), "snapshot": snap}
    return out


def month_starts(start: str, end: str) -> list[str]:
    return [d.date().isoformat() for d in pd.date_range(start, end, freq="MS")]


def sync_members(settings: Settings, conn: sqlite3.Connection, end: str) -> dict:
    path = raw_root(settings) / "members.parquet"
    existing = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=["snapshot_date", "index"])
    done = set(zip(existing["snapshot_date"], existing["index"], strict=True))
    frames = [existing]
    fetched = 0
    with BaoStockSession() as s:
        for snapshot in month_starts(MEMBERS_START, end):
            for index in INDEX_MEMBER_QUERIES:
                if (snapshot, index) in done:
                    continue
                frames.append(s.index_members(index, snapshot))
                fetched += 1
    df = pd.concat(frames, ignore_index=True).drop_duplicates(["snapshot_date", "index", "code"])
    _write_parquet(df, path)
    snap = record_snapshot(conn, "members", {"start": MEMBERS_START, "end": end}, [path], len(df))
    return {"rows": len(df), "snapshots_fetched": fetched, "codes": int(df["code"].nunique()), "snapshot": snap}


def member_codes(settings: Settings) -> list[str]:
    df = pd.read_parquet(raw_root(settings) / "members.parquet")
    return sorted(df["code"].unique())


def universe_codes(settings: Settings, universe: str | None = None) -> list[str]:
    cfg = universe_config(settings, universe)
    if cfg["codes"] == "index_members":
        return member_codes(settings)
    if cfg["codes"] == "hs300_members":
        members = pd.read_parquet(raw_root(settings) / "members.parquet")
        members = members[(members["index"] == "hs300") & (members["snapshot_date"] >= cfg["history_start"])]
        return sorted(members["code"].unique())
    basic = pd.read_parquet(raw_root(settings, universe) / "stock_basic.parquet")
    stocks = basic[(basic["type"].astype(str) == "1") & basic["code"].str.startswith(("sh.", "sz."))]
    ipo_ok = stocks["ipoDate"].fillna("") <= date.today().isoformat()
    out = stocks["outDate"].fillna("")
    alive = (out == "") | (out >= cfg["history_start"])
    return sorted(stocks.loc[ipo_ok & alive, "code"].unique())


_SESSION: BaoStockSession | None = None


def _worker_init() -> None:
    import socket

    global _SESSION
    socket.setdefaulttimeout(120)
    _SESSION = BaoStockSession().__enter__()


def _next_day(day: str) -> str:
    return (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()


def _daily_job(code: str, start: str, end: str, root: str) -> dict:
    root_path = Path(root)
    path = root_path / "daily" / f"{code}.parquet"
    existing = pd.read_parquet(path) if path.exists() else None
    fetch_start = start if existing is None or existing.empty else _next_day(existing["date"].max())
    new_rows = 0
    if fetch_start <= end:
        df = _SESSION.daily(code, fetch_start, end)
        new_rows = len(df)
        combined = df if existing is None else pd.concat([existing, df], ignore_index=True)
        combined = combined.drop_duplicates("date", keep="last").sort_values("date")
        _write_parquet(combined, path)
        adj = _SESSION.adjust_factor(code, end)
        _write_parquet(adj, root_path / "adj" / f"{code}.parquet")
    return {"code": code, "new_rows": new_rows}


def _index_job(code: str, start: str, end: str, root: str) -> dict:
    path = Path(root) / "index_daily" / f"{code}.parquet"
    df = _SESSION.index_daily(code, start, end)
    _write_parquet(df, path)
    return {"code": code, "new_rows": len(df)}


def _run_jobs(job, items: list, workers: int, progress=None) -> list[dict]:
    results, errors = [], []
    with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init) as pool:
        futures = {pool.submit(job, *item): item for item in items}
        for i, fut in enumerate(as_completed(futures), start=1):
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                errors.append({"item": futures[fut][0], "error": str(exc)})
            if progress and (i % 50 == 0 or i == len(items)):
                progress(i, len(items))
    if errors:
        results.append({"errors": errors})
    return results


def sync_daily(settings: Settings, conn: sqlite3.Connection, end: str, workers: int = 6, progress=None,
               universe: str | None = None) -> dict:
    root = raw_root(settings, universe)
    start = history_start(settings, universe)
    codes = universe_codes(settings, universe)
    items = [(code, start, end, str(root)) for code in codes]
    results = _run_jobs(_daily_job, items, workers, progress)
    index_items = [(code, start, end, str(root)) for code in INDEX_CODES.values()]
    results += _run_jobs(_index_job, index_items, min(workers, len(index_items)))
    errors = [e for r in results for e in r.get("errors", [])]
    paths = sorted((root / "daily").glob("*.parquet")) + sorted((root / "index_daily").glob("*.parquet"))
    new_rows = sum(r.get("new_rows", 0) for r in results)
    snap = record_snapshot(conn, _dataset("daily", universe), {"start": start, "end": end}, paths, new_rows)
    return {"codes": len(codes), "new_rows": new_rows, "errors": errors, "snapshot": snap}


def _quarters(end: str, start_year: int | None = None) -> list[tuple[int, int]]:
    end_date = datetime.fromisoformat(end).date()
    out = []
    for year in range(start_year or int(HISTORY_START[:4]), end_date.year + 1):
        for quarter in range(1, 5):
            quarter_end = date(year, quarter * 3, 1) + pd.offsets.MonthEnd(0)
            if pd.Timestamp(quarter_end).date() <= end_date:
                out.append((year, quarter))
    return out


def _financial_job(code: str, end: str, root: str, start_year: int | None = None) -> dict:
    root_path = Path(root) / "financials"
    progress_path = root_path / "_progress" / f"{code}.json"
    done = set(json.loads(progress_path.read_text())) if progress_path.exists() else set()
    end_date = datetime.fromisoformat(end).date()
    frames = {t: [] for t in FINANCIAL_TABLES}
    newly_done = set()
    for year, quarter in _quarters(end, start_year):
        key = f"{year}Q{quarter}"
        if key in done:
            continue
        mature = (end_date - date(year, quarter * 3, 28)).days > FINANCIAL_PUBLICATION_LAG_DAYS
        got_any = False
        for table in FINANCIAL_TABLES:
            df = getattr(_SESSION, table)(code, year, quarter)
            if not df.empty:
                frames[table].append(df)
                got_any = True
        if got_any or mature:
            newly_done.add(key)
    for table, parts in frames.items():
        if not parts:
            continue
        path = root_path / table / f"{code}.parquet"
        existing = [pd.read_parquet(path)] if path.exists() else []
        df = pd.concat(existing + parts, ignore_index=True).drop_duplicates(["statDate", "pubDate"], keep="first")
        _write_parquet(df, path)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(json.dumps(sorted(done | newly_done)))
    return {"code": code, "new_quarters": len(newly_done)}


def sync_financials(settings: Settings, conn: sqlite3.Connection, end: str, workers: int = 4, progress=None,
                    universe: str | None = None) -> dict:
    root = raw_root(settings, universe)
    codes = universe_codes(settings, universe)
    start_year = int(history_start(settings, universe)[:4])
    results = _run_jobs(_financial_job, [(code, end, str(root), start_year) for code in codes], workers, progress)
    errors = [e for r in results for e in r.get("errors", [])]
    paths = []
    for table in FINANCIAL_TABLES:
        paths += sorted((root / "financials" / table).glob("*.parquet"))
    snap = record_snapshot(conn, _dataset("financials", universe), {"end": end}, paths, len(paths))
    return {"codes": len(codes), "errors": errors, "files": len(paths), "snapshot": snap}


def mirror_to_store(settings: Settings, universe: str | None = None) -> dict:
    src_root = raw_root(settings, universe)
    dst_root = settings.raw_store_dir / src_root.name
    copied = 0
    for src in src_root.rglob("*"):
        if not src.is_file() or src.suffix == ".tmp":
            continue
        dst = dst_root / src.relative_to(src_root)
        if dst.exists() and dst.stat().st_size == src.stat().st_size and dst.stat().st_mtime >= src.stat().st_mtime:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    return {"copied": copied, "store": str(dst_root)}


def _events_job(code: str, start: str, end: str, root: str) -> dict:
    root_path = Path(root) / "events"
    fc = _SESSION.forecast(code, start, end)
    ex = _SESSION.express(code, start, end)
    _write_parquet(fc, root_path / "forecast" / f"{code}.parquet")
    _write_parquet(ex, root_path / "express" / f"{code}.parquet")
    return {"code": code, "forecast": len(fc), "express": len(ex)}


def sync_events(settings: Settings, conn: sqlite3.Connection, end: str, workers: int = 4, progress=None,
                universe: str | None = None) -> dict:
    root = raw_root(settings, universe)
    codes = universe_codes(settings, universe)
    items = [(code, history_start(settings, universe), end, str(root)) for code in codes]
    results = _run_jobs(_events_job, items, workers, progress)
    errors = [e for r in results for e in r.get("errors", [])]
    paths = sorted((root / "events").rglob("*.parquet"))
    snap = record_snapshot(conn, _dataset("events", universe), {"end": end}, paths, len(paths))
    return {"codes": len(codes), "forecasts": sum(r.get("forecast", 0) for r in results),
            "express": sum(r.get("express", 0) for r in results), "errors": errors, "snapshot": snap}


def _intraday_job(code: str, start: str, end: str, root: str) -> dict:
    from alphasieve.data.events import intraday_features

    path = Path(root) / "intraday" / f"{code}.parquet"
    existing = pd.read_parquet(path) if path.exists() else None
    fetch_start = start if existing is None or existing.empty else _next_day(str(existing["date"].max())[:10])
    new_rows = 0
    year = int(fetch_start[:4])
    frames = [] if existing is None else [existing]
    while fetch_start <= end:
        chunk_end = min(end, f"{year}-12-31")
        bars = _SESSION.minute(code, fetch_start, chunk_end)
        if not bars.empty:
            bars["code"] = code
            feats = intraday_features(bars)
            new_rows += len(feats)
            frames.append(feats)
            _write_parquet(pd.concat(frames, ignore_index=True).drop_duplicates(["date"], keep="last"), path)
        year += 1
        fetch_start = f"{year}-01-01"
    return {"code": code, "new_rows": new_rows}


def sync_intraday(settings: Settings, conn: sqlite3.Connection, start: str, end: str, workers: int = 4,
                  progress=None, universe: str | None = None) -> dict:
    root = raw_root(settings, universe)
    codes = universe_codes(settings, universe)
    results = _run_jobs(_intraday_job, [(code, start, end, str(root)) for code in codes], workers, progress)
    errors = [e for r in results for e in r.get("errors", [])]
    paths = sorted((root / "intraday").glob("*.parquet"))
    snap = record_snapshot(conn, _dataset("intraday", universe), {"start": start, "end": end}, paths, len(paths))
    return {"codes": len(codes), "new_rows": sum(r.get("new_rows", 0) for r in results), "errors": errors,
            "snapshot": snap}
