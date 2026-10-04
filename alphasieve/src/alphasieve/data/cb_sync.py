"""Raw, versioned convertible bond collection. No panel or research features depend on it."""

from datetime import date
from pathlib import Path

import pandas as pd

from alphasieve.data import sync
from alphasieve.data.providers import westock
from alphasieve.util import utcnow_iso


def _root(settings) -> Path:
    return sync.westock_root(settings)


def _universe(settings, day: str) -> pd.DataFrame:
    path = _root(settings) / "cb_universe" / f"{day}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"CB universe snapshot missing: {path}; run data sync --dataset cb_universe")
    return pd.read_parquet(path)


def sync_cb_universe(settings, conn, day: str) -> dict:
    path = _root(settings) / "cb_universe" / f"{day}.parquet"
    if path.exists():
        frame = pd.read_parquet(path)
        return {"rows": len(frame), "date": day, "existing": True}
    frame = westock.bond_universe()
    if frame.empty:
        raise RuntimeError("AKShare returned an empty convertible bond universe")
    frame.insert(0, "snapshot_date", day)
    frame["fetched_at"] = utcnow_iso()
    sync._write_parquet(frame, path)
    snap = sync.record_snapshot(conn, "akshare:cb_universe", {"date": day, "endpoint": "bond_zh_cov"},
                                [path], len(frame), "akshare")
    return {"rows": len(frame), "date": day, "existing": False, "snapshot": snap}


def sync_cb_terms(settings, conn, day: str, progress=None) -> dict:
    universe = _universe(settings, day)
    codes = sorted(universe["code"].dropna().unique())
    root = _root(settings) / "cb_terms" / day
    tables = ("terms", "coupons", "puts", "calls", "revisions", "cashflows")
    counts = dict.fromkeys(tables, 0)
    errors = []
    pending = [code for code in codes if not (root / "terms" / f"{code.replace('.', '')}.parquet").exists()]
    def save(parsed, batch):
        found = set(parsed["terms"].get("code", []))
        for code in batch:
            if code not in found:
                errors.append({"item": code, "error": "no detail returned"})
                continue
            filename = f"{code.replace('.', '')}.parquet"
            for table, frame in parsed.items():
                part = frame.loc[frame["code"] == code].copy()
                if not part.empty:
                    part["snapshot_date"] = day
                    part["fetched_at"] = utcnow_iso()
                    sync._write_parquet(part, root / table / filename)

    for offset in range(0, len(pending), 4):
        batch = pending[offset:offset + 4]
        try:
            save(westock.bond_detail(batch), batch)
        except Exception:  # noqa: BLE001
            for code in batch:
                try:
                    save(westock.bond_detail([code]), [code])
                except Exception as single_exc:  # noqa: BLE001
                    errors.append({"item": code, "error": str(single_exc)[:300]})
        done = min(offset + 4, len(pending))
        if progress and (done % 20 == 0 or done == len(pending)):
            progress(done, len(pending))
    paths = []
    for table in tables:
        files = sorted((root / table).glob("*.parquet"))
        paths.extend(files)
        parts = [pd.read_parquet(path) for path in files]
        counts[table] = sum(map(len, parts))
        if parts:
            combined = root / f"{table}.parquet"
            sync._write_parquet(pd.concat(parts, ignore_index=True), combined)
            paths.append(combined)
    snap = sync.record_snapshot(conn, "westock:cb_terms", {"date": day}, paths, counts["terms"], "westock")
    return {"codes": len(codes), "tables": counts, "errors": errors, "snapshot": snap}


def sync_cb_quote(settings, conn, day: str) -> dict:
    universe = _universe(settings, day)
    # AKShare retains retired bonds in the issue list. A current conversion price
    # identifies live quote candidates and avoids hundreds of empty retired calls.
    codes = sorted(universe.loc[universe["convert_price"].notna(), "code"].dropna().unique())

    def fetch():
        parts = []
        errors = []
        for i in range(0, len(codes), 20):
            batch = codes[i:i + 20]
            try:
                part = westock.bond_quote(batch)
                if not part.empty:
                    parts.append(part)
            except Exception as exc:  # noqa: BLE001
                errors.append({"item": batch[0], "error": str(exc)[:300]})
        if errors:
            raise RuntimeError(f"CB quote batches failed: {errors[:3]}")
        if not parts:
            return pd.DataFrame()
        frame = pd.concat(parts, ignore_index=True).drop_duplicates("code")
        frame["fetched_at"] = utcnow_iso()
        return frame

    return sync._snapshot_day(settings, conn, "cb_quote", day, fetch)


def sync_cb_daily(settings, conn, end: str, *, full: bool = True, progress=None, day: str | None = None,
                  workers: int = 1) -> dict:
    day = day or date.today().isoformat()
    universe = _universe(settings, day)
    universe = universe[universe["list_date"].notna() & (universe["list_date"] <= end)]
    codes = {row.code: row.list_date for row in universe.itertuples()}
    quote_dates = {}
    if not full:
        quote_path = _root(settings) / "cb_quote" / f"{day}.parquet"
        if not quote_path.exists():
            raise FileNotFoundError(f"CB quote snapshot missing: {quote_path}")
        quotes = pd.read_parquet(quote_path)
        cutoff = (pd.Timestamp(end) - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
        active = set(quotes.loc[quotes["time"].astype(str) >= cutoff, "code"])
        codes = {code: listed for code, listed in codes.items() if code in active}
        quote_dates = dict(zip(quotes["code"], quotes["time"], strict=True))
    root = _root(settings) / "cb_daily"
    recent_start = (pd.Timestamp(end) - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
    def job(item):
        code, listed = item[0]
        target_end = min(end, quote_dates.get(code, end))
        path = root / f"{code.replace('.', '')}.parquet"
        existing = pd.read_parquet(path) if path.exists() else pd.DataFrame()
        lo = sync._next_day(str(existing["date"].max())) if not existing.empty else str(listed)
        if not full:
            lo = max(lo, recent_start)
        if lo <= target_end:
            fresh = westock.bond_daily([code], lo, target_end)
            if not fresh.empty:
                fresh["source"] = "westock:kline"
                merged = pd.concat([existing, fresh], ignore_index=True).drop_duplicates("date", keep="first")
                sync._write_parquet(merged.sort_values("date").reset_index(drop=True), path)
                return len(fresh)
        return 0

    results, errors = sync._run_threads(job, [[item] for item in codes.items()], min(max(1, workers), 4), progress)
    new_rows = sum(results)
    paths = sorted(root.glob("*.parquet"))
    snap = sync.record_snapshot(conn, "westock:cb_daily", {"end": end, "full": full}, paths,
                                new_rows, "westock")
    return {"codes": len(codes), "new_rows": new_rows, "files": len(paths), "errors": errors, "snapshot": snap}
