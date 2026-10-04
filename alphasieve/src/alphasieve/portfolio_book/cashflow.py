"""Explicit external cash movements for time weighted book returns."""

import math
import sqlite3
import uuid
from datetime import date

from alphasieve.errors import validation_error
from alphasieve.util import utcnow_iso


def add_cashflow(conn: sqlite3.Connection, account: str, as_of: str, amount: float, note: str, actor: str) -> dict:
    if not account.strip():
        raise validation_error("account is required")
    try:
        date.fromisoformat(as_of)
    except ValueError as exc:
        raise validation_error("cashflow date must be YYYY-MM-DD") from exc
    if not math.isfinite(amount) or amount == 0:
        raise validation_error("cashflow amount must be finite and nonzero")
    result = {
        "cashflow_id": uuid.uuid4().hex,
        "account": account,
        "as_of": as_of,
        "amount": float(amount),
        "note": note,
        "created_by": actor,
        "created_at": utcnow_iso(),
    }
    conn.execute("INSERT INTO book_cashflows VALUES (?, ?, ?, ?, ?, ?, ?)", tuple(result.values()))
    conn.commit()
    return result


def list_cashflows(conn: sqlite3.Connection, account: str | None = None) -> list[dict]:
    query = "SELECT * FROM book_cashflows"
    rows = conn.execute(
        query + (" WHERE account=?" if account else "") + " ORDER BY as_of, created_at, cashflow_id",
        (account,) if account else (),
    ).fetchall()
    return [dict(row) for row in rows]
