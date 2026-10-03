import json
import sqlite3
import uuid

from pydantic import ValidationError

from alphasieve.errors import not_found, validation_error
from alphasieve.journal.spec import EntrySpec
from alphasieve.ledger.chain import append_chained, verify_chain
from alphasieve.util import canonical_json

HASHED_COLS = (
    "entry_id", "entry_kind", "thesis_id", "holdings_snapshot_id", "actor", "payload_json",
)


def _decode(row: sqlite3.Row | dict) -> dict:
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    return result


def add_entry(conn: sqlite3.Connection, spec: EntrySpec | dict, actor: str) -> dict:
    if isinstance(spec, dict):
        try:
            spec = EntrySpec.model_validate(spec)
        except ValidationError as exc:
            raise validation_error(f"invalid journal entry: {exc}") from None
    if not isinstance(spec, EntrySpec):
        raise validation_error("invalid journal entry")
    if not actor or not actor.strip():
        raise validation_error("actor is required")
    if spec.holdings_snapshot_id and conn.execute(
        "SELECT 1 FROM holdings_snapshots WHERE snapshot_id = ?", (spec.holdings_snapshot_id,)
    ).fetchone() is None:
        raise not_found(f"holdings snapshot {spec.holdings_snapshot_id} not found")
    payload = spec.model_dump(exclude={"entry_kind", "thesis_id", "holdings_snapshot_id"}, exclude_none=True)
    row = {
        "entry_id": f"J-{uuid.uuid4().hex[:16]}",
        "entry_kind": spec.entry_kind,
        "thesis_id": spec.thesis_id,
        "holdings_snapshot_id": spec.holdings_snapshot_id,
        "actor": actor.strip(),
        "payload_json": canonical_json(payload),
    }
    return _decode(append_chained(conn, "journal_entries", row, HASHED_COLS))


def list_entries(conn: sqlite3.Connection, thesis_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM journal_entries"
    params: tuple = ()
    if thesis_id is not None:
        sql += " WHERE thesis_id = ?"
        params = (thesis_id,)
    return [_decode(row) for row in conn.execute(sql + " ORDER BY seq DESC", params)]


def show_entry(conn: sqlite3.Connection, entry_id: str) -> dict:
    row = conn.execute("SELECT * FROM journal_entries WHERE entry_id = ?", (entry_id,)).fetchone()
    if row is None:
        raise not_found(f"journal entry {entry_id} not found")
    return _decode(row)


def verify_entries(conn: sqlite3.Connection) -> dict:
    return verify_chain(conn, "journal_entries", HASHED_COLS)
