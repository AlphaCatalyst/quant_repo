import sqlite3

from alphasieve.config import Settings
from alphasieve.util import canonical_json, utcnow_iso


def record_event(
    conn: sqlite3.Connection,
    settings: Settings,
    event_type: str,
    status: str = "ok",
    command: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    payload: dict | None = None,
) -> None:
    conn.execute(
        "INSERT INTO events (ts, role, user, command, event_type, object_type, object_id, payload_json, status)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            utcnow_iso(),
            settings.role,
            settings.user,
            command,
            event_type,
            object_type,
            object_id,
            canonical_json(payload or {}),
            status,
        ),
    )
