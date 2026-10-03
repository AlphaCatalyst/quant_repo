import sqlite3

from alphasieve.fresh.service import append_ledger, verify_forward_ledger
from alphasieve.ledger.chain import append_chained, verify_chain
from alphasieve.state import connect


def test_shared_chain_detects_changed_content_and_link(tmp_path):
    conn = sqlite3.connect(tmp_path / "chain.sqlite", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, value TEXT, created_at TEXT, "
                 "prev_hash TEXT, row_hash TEXT)")
    first = append_chained(conn, "events", {"value": "first"}, ("value",))
    second = append_chained(conn, "events", {"value": "second"}, ("value",))
    assert first["seq"] == 1
    assert second["prev_hash"] == first["row_hash"]
    assert verify_chain(conn, "events", ("value",)) == {"ok": True, "rows": 2, "errors": []}
    conn.execute("UPDATE events SET value='changed' WHERE seq=1")
    result = verify_chain(conn, "events", ("value",))
    assert result["ok"] is False
    assert result["errors"][0]["seq"] == 1
    conn.execute("UPDATE events SET prev_hash='wrong' WHERE seq=2")
    assert {error["seq"] for error in verify_chain(conn, "events", ("value",))["errors"]} == {1, 2}


def test_forward_verifier_preserves_existing_digest_format(tmp_path):
    conn = connect(tmp_path / "state.sqlite")
    append_ledger(conn, "test", "human", {"number": 1})
    append_ledger(conn, "test", "system", {"number": 2})
    assert verify_forward_ledger(conn) == {"ok": True, "rows": 2, "errors": []}
    conn.execute("INSERT INTO forward_ledger (record_kind,actor,payload_json,created_at,prev_hash,row_hash) "
                 "VALUES ('test','human','{}','now',?,'wrong')",
                 (conn.execute("SELECT row_hash FROM forward_ledger WHERE seq=2").fetchone()[0],))
    result = verify_forward_ledger(conn)
    assert result["ok"] is False
    assert result["errors"][0]["seq"] == 3
