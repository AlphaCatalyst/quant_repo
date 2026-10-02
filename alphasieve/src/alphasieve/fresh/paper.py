"""Append-only observation books. All calls use sealed, system-only panel snapshots."""

import json
import sqlite3
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from alphasieve.data.access import Panel
from alphasieve.fresh.service import append_ledger
from alphasieve.strategy.execution import initial_state, step_day, trailing_liquidity
from alphasieve.util import canonical_json, sha256_hex, utcnow_iso


def _row(conn: sqlite3.Connection, book_id: str):
    book = conn.execute("SELECT * FROM paper_books WHERE book_id=?", (book_id,)).fetchone()
    if book is None:
        raise ValueError("paper book does not exist")
    return book


def record_target(
    conn: sqlite3.Connection,
    book_id: str,
    date: str,
    weights: dict[str, float],
    *,
    sealed_at: str | None = None,
    next_trading_date: str | None = None,
) -> str:
    """Seal a T-close target before the next trading day's 09:25 China time."""
    _row(conn, book_id)
    sealed_at = sealed_at or utcnow_iso()
    sealed = datetime.fromisoformat(sealed_at.replace("Z", "+00:00"))
    if sealed.tzinfo is None:
        raise ValueError("sealed_at must carry a timezone")
    china = ZoneInfo("Asia/Shanghai")
    sealed_local = sealed.astimezone(china)
    signal = pd.Timestamp(date).date()
    next_day = pd.Timestamp(next_trading_date).date() if next_trading_date else signal + timedelta(days=1)
    if sealed_local.date() < signal or sealed_local >= datetime.combine(next_day, time(9, 25), china):
        raise ValueError("missed_signal: target sealing deadline passed")
    if not weights or any(not np.isfinite(v) or v < 0 for v in weights.values()) or sum(weights.values()) > 1 + 1e-9:
        raise ValueError("invalid target")
    payload = {str(k): float(v) for k, v in sorted(weights.items())}
    digest = sha256_hex(canonical_json(payload))
    existing = conn.execute("SELECT digest FROM paper_targets WHERE book_id=? AND date=?", (book_id, date)).fetchone()
    if existing:
        if existing[0] != digest:
            raise ValueError("IMMUTABLE_TARGET_CONFLICT")
        return digest
    conn.execute(
        "INSERT INTO paper_targets VALUES (?,?,?,?,?)", (book_id, date, canonical_json(payload), digest, sealed_at)
    )
    return digest


def _benchmark_close(panel: Panel, benchmark: str, date: pd.Timestamp) -> float:
    if panel.benchmark is None:
        return float("nan")
    frame = panel.benchmark.set_index(pd.to_datetime(panel.benchmark["date"]))
    col = f"{benchmark}_close"
    if col not in frame or date not in frame.index:
        return float("nan")
    return float(frame.loc[date, col])


def settle_day(
    conn: sqlite3.Connection,
    book_id: str,
    date: str,
    panel: Panel,
    *,
    costs: dict | None = None,
    max_participation: float = 0.10,
    manifest: dict | None = None,
) -> dict:
    """Replay T+1 at the open after its sealed daily snapshot arrives.

    Gap rows are immutable. On recovery the whole interval is marked multi_day and
    excluded from daily statistics; its valuation is still carried forward.
    """
    book = _row(conn, book_id)
    day = pd.Timestamp(date)
    day_key = day.date().isoformat()
    existing = conn.execute("SELECT * FROM paper_days WHERE book_id=? AND date=?", (book_id, day_key)).fetchone()
    if existing:
        return dict(existing)
    prior = conn.execute("SELECT * FROM paper_days WHERE book_id=? ORDER BY date DESC LIMIT 1", (book_id,)).fetchone()
    if prior and day_key <= prior["date"]:
        raise ValueError("cannot backfill paper day")
    if day_key < book["start_date"]:
        raise ValueError("day before book start")
    state = json.loads(prior["checkpoint_json"])["state"] if prior else initial_state(panel.codes)
    if list(state["codes"]) != list(panel.codes):
        raise ValueError("book universe changed")
    previous_close = json.loads(prior["checkpoint_json"]).get("close") if prior else None
    previous_benchmark = json.loads(prior["checkpoint_json"]).get("benchmark_close") if prior else None
    if previous_close is None and day in panel.dates:
        pos = panel.dates.get_loc(day)
        if pos > 0:
            previous_close = panel.wide("close").iloc[pos - 1].to_numpy(dtype=float).tolist()
            previous_benchmark = _benchmark_close(panel, book["benchmark"], panel.dates[pos - 1])
    status = "valid"
    detail = {}
    b_close = _benchmark_close(panel, book["benchmark"], day)
    if (
        day not in panel.dates
        or previous_close is None
        or not np.isfinite(b_close)
        or b_close <= 0
        or previous_benchmark is None
        or not np.isfinite(previous_benchmark)
        or previous_benchmark <= 0
    ):
        status = "data_gap"
    if status == "valid":
        pos = panel.dates.get_loc(day)
        opening = panel.wide("open").iloc[pos].to_numpy(dtype=float)
        closing = panel.wide("close").iloc[pos].to_numpy(dtype=float)
    if status == "valid":
        signal_date = panel.dates[pos - 1].date().isoformat() if pos else None
        target = (
            conn.execute(
                "SELECT target_json,digest FROM paper_targets WHERE book_id=? AND date=?", (book_id, signal_date)
            ).fetchone()
            if signal_date and not (prior and prior["status"] == "data_gap")
            else None
        )
        goal = (
            np.array([json.loads(target["target_json"]).get(code, 0.0) for code in state["codes"]]) if target else None
        )
        adv = vol = None
        if target:
            adv_all, vol_all = trailing_liquidity(panel)
            adv, vol = adv_all[pos - 1], vol_all[pos - 1]
        try:
            state, detail = step_day(
                state,
                prev_close=np.asarray(previous_close),
                open_px=opening,
                close_px=closing,
                buy_ok=panel.mask("tradable_buy").iloc[pos].to_numpy(),
                sell_ok=panel.mask("tradable_sell").iloc[pos].to_numpy(),
                goal=goal,
                costs=costs,
                adv=adv,
                vol=vol,
                aum=book["capital"] if goal is not None else None,
                max_participation=max_participation,
            )
        except ValueError as exc:
            status = "data_gap"
            detail = {"reason": str(exc)}
    bench_nav = float(prior["benchmark_nav"]) if prior else 1.0
    if status == "valid":
        bench_ret = b_close / previous_benchmark - 1
        bench_nav *= 1 + bench_ret
        checkpoint = {"state": state, "close": closing.tolist(), "benchmark_close": b_close}
        if prior and prior["status"] == "data_gap":
            status = "recovered_multi_day"
            detail["multi_day_ret"] = detail["ret"]
            detail["multi_day_benchmark_ret"] = bench_ret
            ret = benchmark_ret = None
        else:
            ret, benchmark_ret = detail["ret"], bench_ret
    else:
        ret = benchmark_ret = None
        checkpoint = {"state": state, "close": previous_close, "benchmark_close": previous_benchmark}
        detail.setdefault("reason", "missing valuation or benchmark")
    nav = float(state["nav"])
    prev_hash = prior["row_hash"] if prior else "0" * 64
    manifest = {
        **(manifest or {}),
        "book_id": book_id,
        "date": day_key,
        "status": status,
        "checkpoint_digest": sha256_hex(canonical_json(checkpoint)),
    }
    row_payload = {
        "book_id": book_id,
        "date": day_key,
        "status": status,
        "nav": nav,
        "benchmark_nav": bench_nav,
        "ret": ret,
        "benchmark_ret": benchmark_ret,
        "checkpoint": checkpoint,
        "metrics": detail,
        "manifest": manifest,
        "prev_hash": prev_hash,
    }
    row_hash = sha256_hex(canonical_json(row_payload))
    conn.execute("SAVEPOINT paper_day")
    try:
        conn.execute(
            "INSERT INTO paper_days VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                book_id,
                day_key,
                status,
                nav,
                bench_nav,
                ret,
                benchmark_ret,
                canonical_json(checkpoint),
                canonical_json(detail),
                prev_hash,
                row_hash,
                canonical_json(manifest),
            ),
        )
        for fill in detail.get("fills", []):
            conn.execute(
                "INSERT INTO paper_fills VALUES (?,?,?,?)", (book_id, day_key, fill["code"], canonical_json(fill))
            )
        append_ledger(
            conn,
            "paper_day",
            "system",
            {"row_hash": row_hash, "status": status},
            cohort_id=book["cohort_id"],
            date=day_key,
            book_id=book_id,
        )
        conn.execute("RELEASE paper_day")
    except Exception:
        conn.execute("ROLLBACK TO paper_day")
        conn.execute("RELEASE paper_day")
        raise
    return {
        "book_id": book_id,
        "date": day_key,
        "status": status,
        "nav": nav,
        "benchmark_nav": bench_nav,
        "ret": ret,
        "benchmark_ret": benchmark_ret,
        "row_hash": row_hash,
        "metrics": detail,
    }
