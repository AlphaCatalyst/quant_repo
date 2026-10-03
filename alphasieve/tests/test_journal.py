import sqlite3

import pytest
from pydantic import ValidationError

from alphasieve.errors import AlphaSieveError
from alphasieve.journal import EntrySpec, add_entry, list_entries, show_entry, verify_entries
from alphasieve.state import connect


@pytest.fixture
def conn(tmp_path):
    db = connect(tmp_path / "hot" / "state" / "alphasieve.db")
    yield db
    db.close()


def test_add_list_show_and_verify(conn):
    first = add_entry(conn, {"entry_kind": "note", "reason": "Watch costs", "thesis_id": "T-1"}, "human")
    second = add_entry(conn, EntrySpec(entry_kind="no_action", reason="Wait for filing"), "human")
    assert first["entry_id"].startswith("J-")
    assert first["payload"]["reason"] == "Watch costs"
    assert first["seq"] < second["seq"]
    assert show_entry(conn, first["entry_id"]) == first
    assert [row["entry_id"] for row in list_entries(conn, "T-1")] == [first["entry_id"]]
    assert [row["entry_id"] for row in list_entries(conn)] == [second["entry_id"], first["entry_id"]]
    assert verify_entries(conn) == {"ok": True, "rows": 2, "errors": []}
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE journal_entries SET actor = 'other' WHERE seq = ?", (first["seq"],))


def test_trade_and_snapshot_reference(conn):
    with pytest.raises(AlphaSieveError) as exc:
        add_entry(conn, {"entry_kind": "trade", "reason": "Entry", "code": "600000", "side": "buy",
                         "quantity": 100, "price": 10, "amount": 1000,
                         "holdings_snapshot_id": "missing"}, "human")
    assert exc.value.code == "NOT_FOUND"
    conn.execute(
        "INSERT INTO holdings_snapshots VALUES (?,?,?,?,?,?,?,?)",
        ("S-1", "demo", "2026-10-03", "fixture", "[]", "hash", "human", "2026-10-03T00:00:00Z"),
    )
    row = add_entry(conn, {"entry_kind": "trade", "reason": "Entry", "code": "600000", "side": "buy",
                           "quantity": 100, "price": 10, "amount": 1000,
                           "holdings_snapshot_id": "S-1", "forecast_ids": ["F-1"],
                           "falsifiers": ["Margins fall"], "expected_holding_period": "12 months"}, "human")
    assert row["holdings_snapshot_id"] == "S-1"
    assert row["payload"]["forecast_ids"] == ["F-1"]
    assert verify_entries(conn)["ok"]


@pytest.mark.parametrize("spec", [
    {"entry_kind": "trade", "reason": "Missing trade details"},
    {"entry_kind": "note", "reason": " ", "code": "600000"},
    {"entry_kind": "review", "reason": "Fine", "quantity": 1},
    {"entry_kind": "unknown", "reason": "Fine"},
])
def test_invalid_entry_spec(spec):
    with pytest.raises(ValidationError):
        EntrySpec.model_validate(spec)


def test_verify_detects_wrong_hash(conn):
    first = add_entry(conn, {"entry_kind": "review", "reason": "Quarterly review"}, "human")
    conn.execute(
        "INSERT INTO journal_entries (entry_id,entry_kind,actor,payload_json,created_at,prev_hash,row_hash)"
        " VALUES (?,?,?,?,?,?,?)",
        ("J-bad", "note", "human", '{"reason":"tampered"}', "2026-10-03T00:00:00Z",
         first["row_hash"], "0" * 64),
    )
    report = verify_entries(conn)
    assert report["ok"] is False
    assert report["rows"] == 2
    assert any(error["seq"] == first["seq"] + 1 for error in report["errors"])
