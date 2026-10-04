"""Cash-flow import is explicit and remains append-only."""

import json

import pytest

from alphasieve.cli.main import main
from alphasieve.state import connect


def _call(args, capsys):
    exit_code = main(["book", "cashflow", *args, "--json"])
    return exit_code, json.loads(capsys.readouterr().out)


def test_cashflow_csv_and_append_only(settings, tmp_path, capsys):
    path = tmp_path / "flows.csv"
    path.write_text("account,as_of,amount,note\nalpha,2026-10-01,1000,deposit\n"
                    "alpha,2026-10-02,-100,withdrawal\n", encoding="utf-8")
    code, added = _call(["add", "--file", str(path)], capsys)
    assert code == 0 and [row["amount"] for row in added["data"]["cashflows"]] == [1000, -100]
    code, listed = _call(["list", "--account", "alpha"], capsys)
    assert code == 0 and len(listed["data"]["cashflows"]) == 2
    with connect(settings.state_db) as conn:
        with pytest.raises(Exception, match="append-only"):
            conn.execute("UPDATE book_cashflows SET amount=0")
        with pytest.raises(Exception, match="append-only"):
            conn.execute("DELETE FROM book_cashflows")


def test_cashflow_csv_rejects_partial_batch(settings, tmp_path, capsys):
    path = tmp_path / "bad.csv"
    path.write_text("account,as_of,amount\nalpha,2026-10-01,100\nalpha,bad,200\n", encoding="utf-8")
    code, result = _call(["add", "--file", str(path)], capsys)
    assert code != 0 and result["error"]["code"] == "VALIDATION_ERROR"
    with connect(settings.state_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM book_cashflows").fetchone()[0] == 0
