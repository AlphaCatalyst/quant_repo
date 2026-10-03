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


def record_snapshot(conn: sqlite3.Connection, dataset: str, params: dict, paths: list[Path], rows: int,
                    source: str = SOURCE) -> str:
    content_hash = sha256_hex("".join(sorted(f"{p.name}:{file_sha256(p)}" for p in paths if p.exists())))
    snapshot_id = sha256_hex(canonical_json({"dataset": dataset, "params": params, "content": content_hash}))[:16]
    root = str(paths[0].parent) if paths else ""
    conn.execute(
        "INSERT OR REPLACE INTO data_snapshots (snapshot_id, source, dataset, params_json, rows, content_hash, path,"
        " fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (snapshot_id, source, dataset, canonical_json(params), rows, content_hash, root, utcnow_iso()),
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


def mirror_free_to_store(settings: Settings) -> dict:
    copied = 0
    for name in ("csindex", "exchange", "eastmoney"):
        src_root = settings.raw_dir / name
        for src in src_root.rglob("*.parquet"):
            dst = settings.raw_store_dir / name / src.relative_to(src_root)
            if dst.exists() and dst.stat().st_size == src.stat().st_size and dst.stat().st_mtime >= src.stat().st_mtime:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
    return {"copied": copied}


def sync_csindex_returns(settings: Settings, conn: sqlite3.Connection) -> dict:
    from alphasieve.data.providers import csindex

    out, errors = {}, []
    root = settings.raw_dir / "csindex" / "total_return"
    for symbol in ("H00905", "H00300", "H00906"):
        path = root / f"{symbol}.parquet"
        try:
            df = csindex.fetch_index_history(symbol)
            if df.empty:
                raise ValueError("empty index history")
            if path.exists() and len(df) < len(pd.read_parquet(path, columns=["date"])):
                raise ValueError("index history shorter than the stored copy")
            _write_parquet(df, path)
            snap = record_snapshot(conn, "csindex:total_return", {"symbol": symbol}, [path], len(df), "csindex")
            out[symbol] = {"rows": len(df), "snapshot": snap}
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)[:200]})
    return {"symbols": out, "errors": errors}


def sync_exchange_margin(settings: Settings, conn: sqlite3.Connection, start: str, end: str,
                         progress=None) -> dict:
    from alphasieve.data.providers import exchange

    dates = load_calendar(settings)
    dates = dates[(dates >= start) & (dates <= end)].tolist()
    out, errors = [], []
    root = settings.raw_dir / "exchange" / "margin"
    for i, day in enumerate(dates, 1):
        path = root / f"{day}.parquet"
        if not path.exists():
            try:
                df = exchange.fetch_margin_day(day)
                if df.empty:
                    raise ValueError("empty exchange margin day")
                _write_parquet(df, path)
                snap = record_snapshot(conn, "exchange:margin", {"date": day}, [path], len(df), "exchange")
                out.append({"date": day, "rows": len(df), "snapshot": snap})
            except Exception as exc:
                errors.append({"date": day, "error": str(exc)[:200]})
        if progress:
            progress(i, len(dates))
    return {"dates": len(dates), "new_files": len(out), "rows": sum(r["rows"] for r in out),
            "errors": errors}


def sync_eastmoney_holders(settings: Settings, conn: sqlite3.Connection, codes: list[str], progress=None) -> dict:
    from alphasieve.data.providers import eastmoney

    root = settings.raw_dir / "eastmoney" / "holders"
    out, errors = [], []
    for i, code in enumerate(codes, 1):
        path = root / f"{code}.parquet"
        try:
            df = eastmoney.fetch_holder_history(code)
            if df.empty:
                raise ValueError("empty shareholder history")
            if path.exists():
                old = pd.read_parquet(path)
                df = pd.concat([old, df], ignore_index=True).drop_duplicates(
                    ["code", "stat_date", "announce_date"], keep="last")
            _write_parquet(df, path)
            snap = record_snapshot(conn, "eastmoney:holders", {"code": code}, [path], len(df), "eastmoney")
            out.append({"code": code, "rows": len(df), "snapshot": snap})
        except Exception as exc:
            errors.append({"code": code, "error": str(exc)[:200]})
        if progress:
            progress(i, len(codes))
    return {"codes": len(codes), "files": len(out), "errors": errors}


def sync_csindex_weights(settings: Settings, conn: sqlite3.Connection) -> dict:
    from alphasieve.data.providers import csindex

    root = settings.raw_dir / "csindex" / "weights"
    out, errors = {}, []
    for symbol in ("000905", "000300", "000906"):
        try:
            df = csindex.fetch_index_weights(symbol)
            if df.empty:
                raise ValueError("empty index weights")
            snapshot_date = str(df["snapshot_date"].iloc[0])[:10]
            path = root / symbol / f"{snapshot_date}.parquet"
            if path.exists():
                out[symbol] = {"date": snapshot_date, "existing": True}
                continue
            _write_parquet(df, path)
            snap = record_snapshot(conn, "csindex:weights", {"symbol": symbol, "date": snapshot_date},
                                   [path], len(df), "csindex")
            out[symbol] = {"date": snapshot_date, "rows": len(df), "snapshot": snap}
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)[:200]})
    return {"symbols": out, "errors": errors}


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


WESTOCK_BATCH = 50
WESTOCK_FINANCIALS_START = "2000-01-01"
FUND_FLOW_START = "2020-01-01"


def westock_root(settings: Settings) -> Path:
    """westock data is shared by every universe: one file per code, whichever universe asked for it first."""
    return settings.raw_dir / "westock"


WESTOCK_INDICES = ("sh.000300", "sh.000905", "sh.000906", "sh.000852")
WESTOCK_NET_INDICES = ("csN00905", "csN00300")
SECTOR_INDEX_START = "2012-01-01"
NET_INDEX_START = "2024-05-08"


def sync_westock_reports(settings: Settings, conn: sqlite3.Connection, workers: int = 4, progress=None,
                         universe: str | None = None) -> dict:
    """Append newly listed reports, stopping each stock at its first stored report id.

    Only stocks already backfilled by ``tools/westock_reports_crawl.py`` are updated, and only new reports get their
    text fetched: a stock's full history takes minutes, which belongs in the backfill, not in the daily run.
    """
    from alphasieve.data.providers import westock

    root = westock_root(settings) / "reports"
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "reports.sqlite", timeout=60)
    db.execute("PRAGMA busy_timeout=60000")
    db.executescript("""CREATE TABLE IF NOT EXISTS lists
        (code TEXT PRIMARY KEY, rows_json TEXT NOT NULL, n INTEGER NOT NULL, fetched_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS details
        (id TEXT PRIMARY KEY, code TEXT NOT NULL, list_time TEXT, detail_json TEXT, fetched_at TEXT NOT NULL);""")
    codes = universe_codes(settings, universe)
    listed = detailed = 0
    errors = []
    try:
        for i, code in enumerate(codes, 1):
            raw_code = westock.to_westock(code)
            old = db.execute("SELECT rows_json FROM lists WHERE code=?", (raw_code,)).fetchone()
            if old is None:
                continue
            previous = json.loads(old[0])
            known = {str(r.get("id")) for r in previous}
            new = []
            try:
                for offset in range(0, 10000, 20):
                    page = westock.report_page(code, offset)
                    for row in page:
                        if str(row.get("id")) in known:
                            break
                        if row.get("id"):
                            new.append(row)
                    if len(new) < offset + len(page) or len(page) < 20:
                        break
                merged = new + [r for r in previous if str(r.get("id")) not in {str(n["id"]) for n in new}]
                db.execute("INSERT OR REPLACE INTO lists VALUES (?,?,?,?)",
                           (raw_code, json.dumps(merged, ensure_ascii=False), len(merged), utcnow_iso()))
                db.commit()
                listed += len(new)
                for row in new:
                    rid = str(row.get("id", ""))
                    if not rid or not westock.is_broker_report(row.get("title", "")):
                        continue
                    if db.execute("SELECT 1 FROM details WHERE id=?", (rid,)).fetchone():
                        continue
                    try:
                        detail = westock.report_detail(rid)
                        db.execute("INSERT OR IGNORE INTO details VALUES (?,?,?,?,?)",
                                   (rid, raw_code, row.get("time"),
                                    json.dumps(detail, ensure_ascii=False) if detail else None, utcnow_iso()))
                        detailed += 1
                    except Exception as exc:  # noqa: BLE001
                        errors.append({"item": rid, "error": str(exc)[:300]})
                db.commit()
            except Exception as exc:  # noqa: BLE001
                errors.append({"item": code, "error": str(exc)[:300]})
            if progress and (i % 50 == 0 or i == len(codes)):
                progress(i, len(codes))
    finally:
        db.close()
    parsed_rows = 0
    if detailed:
        from alphasieve.data.reports import write_parsed

        try:
            parsed_rows = len(write_parsed(root / "reports.sqlite", root / "parsed.parquet"))
        except Exception as exc:  # noqa: BLE001
            errors.append({"item": "parsed.parquet", "error": str(exc)[:300]})
    return {"codes": len(codes), "new_reports": listed, "new_details": detailed, "parsed_rows": parsed_rows,
            "errors": errors}


def _snapshot_day(settings: Settings, conn: sqlite3.Connection, dataset: str, day: str,
                  fetch) -> dict:
    path = westock_root(settings) / dataset / f"{day}.parquet"
    if path.exists():
        return {"date": day, "rows": len(pd.read_parquet(path)), "existing": True}
    df = fetch()
    if df.empty:
        return {"date": day, "rows": 0, "existing": False}
    df.insert(0, "snapshot_date", day)
    _write_parquet(df, path)
    snap = record_snapshot(conn, f"westock:{dataset}", {"date": day}, [path], len(df), "westock")
    return {"date": day, "rows": len(df), "existing": False, "snapshot": snap}


def sync_westock_consensus(settings: Settings, conn: sqlite3.Connection, day: str, workers: int = 4,
                           progress=None, universe: str | None = None) -> dict:
    from alphasieve.data.providers import westock

    codes = universe_codes(settings, universe)

    def fetch():
        parts, errors = _run_threads(westock.consensus, _batches(codes), workers, progress)
        if errors:
            raise RuntimeError(f"consensus batches failed: {errors[:3]}")
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    return _snapshot_day(settings, conn, "consensus", day, fetch)


def sync_westock_index_members(settings: Settings, conn: sqlite3.Connection, day: str) -> dict:
    from alphasieve.data.providers import westock

    return _snapshot_day(settings, conn, "index_members", day,
                         lambda: westock.index_constituents(list(WESTOCK_INDICES)))


def sync_westock_sw_industry(settings: Settings, conn: sqlite3.Connection, day: str) -> dict:
    from alphasieve.data.providers import westock

    def fetch():
        lists = pd.concat([westock.sector_list(level) for level in (1, 2, 3)], ignore_index=True)
        parts = westock.sector_constituents(lists["sector_code"].tolist())
        return parts.merge(lists, on="sector_code", how="left").drop_duplicates(["code", "level", "sector_code"])

    return _snapshot_day(settings, conn, "sw_industry", day, fetch)


def sync_westock_index_kline(settings: Settings, conn: sqlite3.Connection, end: str, kind: str,
                             workers: int = 4, progress=None) -> dict:
    from alphasieve.data.providers import westock

    if kind not in ("sector_index_daily", "return_index_daily"):
        raise ValueError(kind)
    root = westock_root(settings) / kind
    if kind == "sector_index_daily":
        lists = pd.concat([westock.sector_list(level) for level in (1, 2)], ignore_index=True)
        codes = sorted(lists["sector_code"].unique())
        start = SECTOR_INDEX_START
    else:
        codes, start = list(WESTOCK_NET_INDICES), NET_INDEX_START

    def job(batch: list[str]) -> int:
        code = batch[0]
        path = root / f"{code}.parquet"
        existing = pd.read_parquet(path) if path.exists() else pd.DataFrame()
        lo = _next_day(str(existing["date"].max())) if not existing.empty else start
        if lo > end:
            return 0
        frames = []
        for year in range(int(lo[:4]), int(end[:4]) + 1):
            a, b = max(lo, f"{year}-01-01"), min(end, f"{year}-12-31")
            if a <= b:
                frames.append(westock.kline([code], a, b))
        fresh = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not fresh.empty:
            merged = pd.concat([existing, fresh], ignore_index=True).drop_duplicates("date", keep="first")
            _write_parquet(merged.sort_values("date").reset_index(drop=True), path)
        return len(fresh)

    results, errors = _run_threads(job, [[c] for c in codes], workers, progress)
    paths = sorted(root.glob("*.parquet"))
    snap = record_snapshot(conn, f"westock:{kind}", {"end": end}, paths, sum(results), "westock")
    return {"codes": len(codes), "new_rows": sum(results), "files": len(paths), "errors": errors, "snapshot": snap}


def _batches(items: list, size: int = WESTOCK_BATCH) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _run_threads(job, batches: list, workers: int, progress=None) -> tuple[list, list]:
    from concurrent.futures import ThreadPoolExecutor

    results, errors = [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(job, batch): batch for batch in batches}
        for i, fut in enumerate(as_completed(futures), start=1):
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                errors.append({"item": futures[fut][0], "error": str(exc)[:300]})
            if progress and (i % 10 == 0 or i == len(batches)):
                progress(i, len(batches))
    return results, errors


def sync_westock_financials(settings: Settings, conn: sqlite3.Connection, end: str, workers: int = 4,
                            progress=None, universe: str | None = None) -> dict:
    """Full re-fetch of the three statements (westock keeps one version per period; history is cheap)."""
    from alphasieve.data.providers import westock

    root = westock_root(settings) / "financials"
    codes = universe_codes(settings, universe)

    def job(batch: list[str]) -> int:
        frames = {k: westock.statements(batch, k, WESTOCK_FINANCIALS_START, end) for k in westock.STATEMENTS}
        for code in batch:
            for kind, df in frames.items():
                part = df[df["code"] == code] if not df.empty else df
                if not part.empty:
                    _write_parquet(part.reset_index(drop=True), root / kind / f"{code}.parquet")
        return sum(len(df) for df in frames.values())

    results, errors = _run_threads(job, _batches(codes), workers, progress)
    paths = sorted(root.rglob("*.parquet"))
    snap = record_snapshot(conn, "westock:financials", {"end": end, "universe": universe or "csi800"}, paths,
                           sum(results), source="westock")
    return {"codes": len(codes), "rows": sum(results), "files": len(paths), "errors": errors, "snapshot": snap}


def sync_fund_flow(settings: Settings, conn: sqlite3.Connection, end: str, workers: int = 4, progress=None,
                   universe: str | None = None) -> dict:
    """Daily fund flow from 2020, fetched one calendar year per request (the service caps rows per call)."""
    from alphasieve.data.providers import westock

    root = westock_root(settings) / "fund_flow"
    codes = universe_codes(settings, universe)

    def job(batch: list[str]) -> int:
        existing = {c: pd.read_parquet(p) for c in batch if (p := root / f"{c}.parquet").exists()}
        last = min((str(existing[c]["date"].max()) if c in existing else "") for c in batch)
        start = _next_day(last) if last else FUND_FLOW_START
        frames = []
        for year in range(int(start[:4]), int(end[:4]) + 1):
            lo, hi = max(start, f"{year}-01-01"), min(end, f"{year}-12-31")
            if lo <= hi:
                frames.append(westock.fund_flow(batch, lo, hi))
        new = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        for code in batch:
            part = new[new["code"] == code] if not new.empty else new
            if part.empty:
                continue
            old = [existing[code]] if code in existing else []
            merged = pd.concat(old + [part], ignore_index=True).drop_duplicates("date", keep="last")
            _write_parquet(merged.sort_values("date").reset_index(drop=True), root / f"{code}.parquet")
        return len(new)

    results, errors = _run_threads(job, _batches(codes), workers, progress)
    paths = sorted(root.glob("*.parquet"))
    snap = record_snapshot(conn, "westock:fund_flow", {"end": end, "universe": universe or "csi800"}, paths,
                           sum(results), source="westock")
    return {"codes": len(codes), "new_rows": sum(results), "files": len(paths), "errors": errors, "snapshot": snap}


def sync_margin_snapshot(settings: Settings, conn: sqlite3.Connection, day: str, workers: int = 8, progress=None,
                         universe: str | None = None) -> dict:
    """Margin balances for one trading day (one call per code); history accumulates from the first run (D-30)."""
    from alphasieve.data.providers import westock

    path = westock_root(settings) / "margin" / f"{day}.parquet"
    codes = universe_codes(settings, universe)
    done = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=["code"])
    todo = [c for c in codes if c not in set(done["code"])]

    def job(batch: list[str]) -> list[dict]:
        return [r for c in batch if (r := westock.margin(c, day)) is not None]

    results, errors = _run_threads(job, _batches(todo, 10), workers, progress)
    rows = [r for batch in results for r in batch]
    df = pd.concat([done, pd.DataFrame(rows)], ignore_index=True) if rows else done
    if not df.empty:
        _write_parquet(df.drop_duplicates("code", keep="last"), path)
    snap = None
    if path.exists():
        snap = record_snapshot(conn, "westock:margin", {"date": day}, [path], len(df), "westock")
    return {"date": day, "codes": len(codes), "rows": len(df), "errors": errors, "snapshot": snap}


def week_ends(calendar: pd.Series, start: str, end: str) -> list[str]:
    days = pd.to_datetime(calendar[(calendar >= start) & (calendar <= end)])
    return sorted(days.groupby(days.dt.strftime("%G-%V")).max().dt.strftime("%Y-%m-%d"))


def sync_margin_history(settings: Settings, conn: sqlite3.Connection, start: str, end: str, workers: int = 8,
                        progress=None, universe: str | None = None) -> dict:
    """Weekly margin snapshots (last trading day of each week); resumable per date. westock has data from 2018."""
    dates = week_ends(load_calendar(settings, universe), start, end)
    out, errors = [], []
    for i, day in enumerate(dates, start=1):
        res = sync_margin_snapshot(settings, conn, day, workers, None, universe)
        out.append(res["rows"])
        errors += res["errors"]
        if progress:
            progress(i, len(dates))
    return {"dates": len(dates), "rows": sum(out), "errors": errors}


def _intraday_job(code: str, start: str, end: str, root: str, minutes: int = 5) -> dict:
    from alphasieve.data.events import intraday_features

    path = Path(root) / "intraday" / f"{code}.parquet"
    existing = pd.read_parquet(path) if path.exists() else None
    fetch_start = start if existing is None or existing.empty else _next_day(str(existing["date"].max())[:10])
    new_rows = 0
    year = int(fetch_start[:4])
    frames = [] if existing is None else [existing]
    while fetch_start <= end:
        chunk_end = min(end, f"{year}-12-31")
        bars = _SESSION.minute(code, fetch_start, chunk_end, str(minutes))
        if not bars.empty:
            bars["code"] = code
            feats = intraday_features(bars, minutes)
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
    minutes = int(universe_config(settings, universe).get("intraday_minutes", 5))
    results = _run_jobs(_intraday_job, [(code, start, end, str(root), minutes) for code in codes], workers, progress)
    errors = [e for r in results for e in r.get("errors", [])]
    paths = sorted((root / "intraday").glob("*.parquet"))
    snap = record_snapshot(conn, _dataset("intraday", universe), {"start": start, "end": end}, paths, len(paths))
    return {"codes": len(codes), "new_rows": sum(r.get("new_rows", 0) for r in results), "errors": errors,
            "snapshot": snap}
