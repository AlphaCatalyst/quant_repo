import pandas as pd
import pytest

from alphasieve.data import sync
from alphasieve.data.providers import dolthub, free_http
from alphasieve.state import connect

COMMIT = "d5ouv06svuoat4193aiefibgu2fa3ose"


def test_dolthub_pinned_year_pages_and_values(monkeypatch):
    queries = []

    def query(sql):
        queries.append(sql)
        if "COUNT(*)" in sql:
            return [{"n": "2"}]
        return [{"index_code": "000905.SH", "stock_code": "000001.SZ", "trade_date": "2012-01-31",
                 "weight": "0.25"},
                {"index_code": "000905.SH", "stock_code": "600000.SH", "trade_date": "2012-01-31",
                 "weight": "0.31"}]

    monkeypatch.setattr(dolthub, "_query", query)
    df = dolthub.fetch_index_weights("000905.SH", 2012, 2012, COMMIT)
    assert df.columns.tolist() == dolthub.COLUMNS
    assert df["weight"].tolist() == [0.25, 0.31]
    assert all(f"AS OF '{COMMIT}'" in sql for sql in queries)
    assert all("2012-01-01" in sql and "2013-01-01" in sql for sql in queries)


def test_dolthub_incomplete_page_is_rejected(monkeypatch):
    monkeypatch.setattr(dolthub, "_query", lambda sql: [{"n": "2"}] if "COUNT(*)" in sql else [])
    with pytest.raises(free_http.FreeDataError, match="incomplete page"):
        dolthub.fetch_index_weights("000905.SH", 2012, 2012, COMMIT)


def test_dolthub_sync_preserves_good_raw_on_failure(settings, monkeypatch):
    conn = connect(settings.state_db)
    monkeypatch.setattr(dolthub, "master_hash", lambda: COMMIT)
    monkeypatch.setattr(dolthub, "INDEX_START", dict.fromkeys(dolthub.INDEX_START, 2022))

    def fetch(symbol, start_year, end_year, commit):
        assert end_year == 2022 and commit == COMMIT
        return pd.DataFrame([{"index_code": symbol, "stock_code": "000001.SZ",
                              "trade_date": "2022-12-30", "weight": 100.0}])

    monkeypatch.setattr(dolthub, "fetch_index_weights", fetch)
    result = sync.sync_dolthub_weights(settings, conn, "2022-12-31")
    assert len(result["symbols"]) == 4 and not result["errors"]
    path = settings.raw_dir / "dolthub" / "index_weights" / "000905.SH.parquet"
    assert pd.read_parquet(path).iloc[0]["weight"] == 100.0
    params = conn.execute("SELECT params_json FROM data_snapshots WHERE dataset='dolthub:index_weights' "
                          "LIMIT 1").fetchone()[0]
    assert COMMIT in params and "percent" in params
    monkeypatch.setattr(dolthub, "master_hash", lambda: "e" * 32)
    monkeypatch.setattr(dolthub, "fetch_index_weights", lambda *args: (_ for _ in ()).throw(RuntimeError("offline")))
    result = sync.sync_dolthub_weights(settings, conn, "2022-12-31")
    assert len(result["errors"]) == 4
    assert pd.read_parquet(path).iloc[0]["weight"] == 100.0
