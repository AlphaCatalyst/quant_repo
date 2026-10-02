"""Resumable bulk crawl of westock sell-side research reports into the local raw store.

Lists every report of each code (newest first, 20 per page, capped at 9999 per code by the service), then fetches the
full text of broker reports once per report id. Everything is stored verbatim; parsing lives elsewhere.

    uv run python tools/westock_reports_crawl.py --codes-file codes.txt [--workers 12] [--phase lists|details|all]
"""

import argparse
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from alphasieve.data.providers.westock import is_broker_report as is_broker
from alphasieve.data.providers.westock import report_call as call

ROOT = Path("/data/alphasieve/data/raw/westock/reports")


def connect() -> sqlite3.Connection:
    ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROOT / "reports.sqlite", check_same_thread=False, timeout=60)
    conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS lists (code TEXT PRIMARY KEY, rows_json TEXT NOT NULL, n INTEGER NOT NULL,
                                          fetched_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS details (id TEXT PRIMARY KEY, code TEXT NOT NULL, list_time TEXT,
                                            detail_json TEXT, fetched_at TEXT NOT NULL);
    """)
    return conn


def fetch_list(code: str) -> list[dict]:
    rows, off = [], 0
    while off < 10000:
        page = call(["report", code, "--limit", "20", "--offset", str(off)])
        if isinstance(page, dict):
            page = page.get("data") or []
        rows += page
        if len(page) < 20:
            break
        off += 20
    return rows


def fetch_detail(rid: str) -> dict:
    d = call(["report", "detail", rid])
    if isinstance(d, dict):
        d = d.get("data") or []
    return d[0] if d and isinstance(d[0], dict) else {}


def run(phase: str, codes: list[str], workers: int) -> None:
    conn = connect()
    lock = threading.Lock()
    now = lambda: datetime.now(UTC).isoformat()  # noqa: E731
    log = open(ROOT / "crawl.log", "a", buffering=1)
    if phase in ("lists", "all"):
        done = {r[0] for r in conn.execute("SELECT code FROM lists")}
        todo = [c for c in codes if c not in done]
        log.write(f"{now()} lists todo={len(todo)} done={len(done)}\n")
        with ThreadPoolExecutor(workers) as ex:
            futs = {ex.submit(fetch_list, c): c for c in todo}
            for k, f in enumerate(as_completed(futs), 1):
                c = futs[f]
                try:
                    rows = f.result()
                except Exception as exc:  # noqa: BLE001
                    log.write(f"{now()} list {c} error {exc}\n")
                    continue
                with lock:
                    conn.execute("INSERT OR REPLACE INTO lists VALUES (?,?,?,?)",
                                 (c, json.dumps(rows, ensure_ascii=False), len(rows), now()))
                    conn.commit()
                if k % 50 == 0:
                    log.write(f"{now()} lists {k}/{len(todo)}\n")
    if phase in ("details", "all"):
        have = {r[0] for r in conn.execute("SELECT id FROM details")}
        todo, seen = [], set(have)
        for code, rows_json in conn.execute("SELECT code, rows_json FROM lists"):
            for r in json.loads(rows_json):
                if r["id"] not in seen and is_broker(r.get("title", "")):
                    seen.add(r["id"])
                    todo.append((r["id"], code, r.get("time")))
        log.write(f"{now()} details todo={len(todo)} done={len(have)}\n")
        errors = 0
        with ThreadPoolExecutor(workers) as ex:
            futs = {ex.submit(fetch_detail, rid): (rid, code, t) for rid, code, t in todo}
            for k, f in enumerate(as_completed(futs), 1):
                rid, code, t = futs[f]
                try:
                    d = f.result()
                except Exception as exc:  # noqa: BLE001
                    errors += 1
                    log.write(f"{now()} detail {rid} error {exc}\n")
                    continue
                with lock:
                    conn.execute("INSERT OR REPLACE INTO details VALUES (?,?,?,?,?)",
                                 (rid, code, t, json.dumps(d, ensure_ascii=False) if d else None, now()))
                    if k % 200 == 0:
                        conn.commit()
                if k % 2000 == 0:
                    log.write(f"{now()} details {k}/{len(todo)} errors={errors}\n")
        conn.commit()
    log.write(f"{now()} phase {phase} finished\n")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--codes-file", required=True)
    p.add_argument("--workers", type=int, default=12)
    p.add_argument("--phase", choices=("lists", "details", "all"), default="all")
    a = p.parse_args()
    codes = [c.strip() for c in Path(a.codes_file).read_text().split() if c.strip()]
    run(a.phase, codes, a.workers)


if __name__ == "__main__":
    main()
