"""State database backups to the store (docs/02 §4: hourly copies on Ceph).

A backup is taken with SQLite's online backup API, so it is consistent while writers are active. The copy is
checked (``PRAGMA integrity_check`` and the ledger hash chain) before it is renamed into place; a copy that fails
either check is deleted and the command fails. Retention keeps the newest ``KEEP_RECENT`` copies plus the newest
copy of each of the last ``KEEP_DAILY`` days.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alphasieve.config import get_settings
from alphasieve.errors import AlphaSieveError
from alphasieve.ledger import verify_ledger
from alphasieve.util import canonical_json, sha256_hex

KEEP_RECENT = 48
KEEP_DAILY = 30
PREFIX = "alphasieve-"


def _stamp(path: Path) -> str:
    return path.name[len(PREFIX):-len(".db")]


def list_backups(backups_dir: Path) -> list[Path]:
    return sorted(backups_dir.glob(f"{PREFIX}*.db"), key=_stamp)


def take_backup(conn: sqlite3.Connection, backups_dir: Path, now: datetime | None = None, settings=None) -> dict:
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    backups_dir.mkdir(parents=True, exist_ok=True)
    final = backups_dir / f"{PREFIX}{now.strftime('%Y%m%dT%H%M%SZ')}.db"
    partial = final.with_suffix(".db.partial")
    partial.unlink(missing_ok=True)
    dst = sqlite3.connect(partial)
    try:
        conn.backup(dst)
        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        dst.row_factory = sqlite3.Row
        ledger = verify_ledger(dst, settings)
        head = dst.execute("SELECT hash FROM trials ORDER BY seq DESC LIMIT 1").fetchone()
    finally:
        dst.close()
    if integrity != "ok" or not ledger["ok"]:
        partial.unlink(missing_ok=True)
        raise AlphaSieveError("CONFLICT", "backup copy failed verification",
                              {"integrity_check": integrity, "ledger_errors": ledger["errors"][:20]})
    partial.rename(final)
    info = {"path": str(final), "bytes": final.stat().st_size, "sha256": sha256_hex(final.read_bytes()),
            "ledger_rows": ledger["rows"], "ledger_head": head[0] if head else None, "taken_at": now.isoformat()}
    final.with_suffix(".json").write_text(canonical_json(info), encoding="utf-8")
    return info


def prune_backups(backups_dir: Path, keep_recent: int = KEEP_RECENT, keep_daily: int = KEEP_DAILY) -> list[str]:
    backups = list_backups(backups_dir)
    keep = set(backups[-keep_recent:]) if keep_recent > 0 else set()
    newest_per_day: dict[str, Path] = {}
    for path in backups:
        newest_per_day[_stamp(path)[:8]] = path
    for day in sorted(newest_per_day)[-keep_daily:] if keep_daily > 0 else []:
        keep.add(newest_per_day[day])
    removed = []
    for path in backups:
        if path not in keep:
            path.unlink()
            path.with_suffix(".json").unlink(missing_ok=True)
            removed.append(path.name)
    return removed
