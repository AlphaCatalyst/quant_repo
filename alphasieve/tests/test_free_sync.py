import runpy
from pathlib import Path

import pandas as pd

from alphasieve.data import sync
from alphasieve.data.fundamentals import margin_rows
from alphasieve.data.panel import _exchange_before_westock, _load_exchange_margin
from alphasieve.data.providers import csindex, eastmoney, exchange
from alphasieve.state import connect

compare = runpy.run_path(str(Path(__file__).resolve().parents[1] / "tools" / "compare_exchange_margin.py"))["compare"]


def _margin(code, day, value):
    return {"code": code, "date": day, "FinanceValue": value, "SecurityValue": 5.0,
            "FinanceBuyValue": 20.0, "FinanceRefundValue": float("nan"), "TradingValue": value + 5.0}


def test_free_sync_resume_provenance_and_mirror(settings, monkeypatch):
    conn = connect(settings.state_db)
    calendar = pd.DataFrame({"calendar_date": ["2019-10-11", "2019-10-14"], "is_trading_day": [1, 1]})
    sync._write_parquet(calendar, sync.raw_root(settings) / "trade_dates.parquet")
    calls = []

    def fetch(day):
        calls.append(day)
        if day == "2019-10-14":
            raise RuntimeError("transient")
        return pd.DataFrame([_margin("sz.000001", day, 100.0)])

    monkeypatch.setattr(exchange, "fetch_margin_day", fetch)
    first = sync.sync_exchange_margin(settings, conn, "2019-10-11", "2019-10-14")
    assert first["new_files"] == 1 and len(first["errors"]) == 1
    path = settings.raw_dir / "exchange" / "margin" / "2019-10-11.parquet"
    assert pd.read_parquet(path).iloc[0]["FinanceValue"] == 100.0
    monkeypatch.setattr(exchange, "fetch_margin_day", lambda day: pd.DataFrame([_margin("sz.000001", day, 101.0)]))
    second = sync.sync_exchange_margin(settings, conn, "2019-10-11", "2019-10-14")
    assert second["new_files"] == 1 and not second["errors"]
    assert pd.read_parquet(path).iloc[0]["FinanceValue"] == 100.0
    assert conn.execute("SELECT count(*) FROM data_snapshots WHERE dataset='exchange:margin'").fetchone()[0] == 2
    assert sync.mirror_free_to_store(settings)["copied"] == 2
    assert (settings.raw_store_dir / "exchange" / "margin" / path.name).exists()


def test_total_return_history_is_not_replaced_by_a_shorter_response(settings, monkeypatch):
    conn = connect(settings.state_db)
    full = pd.DataFrame({"index_code": "H00905", "date": ["2022-12-29", "2022-12-30"], "close": [1.0, 1.1]})
    monkeypatch.setattr(csindex, "fetch_index_history", lambda symbol: full.assign(index_code=symbol))
    assert not sync.sync_csindex_returns(settings, conn)["errors"]
    monkeypatch.setattr(csindex, "fetch_index_history", lambda symbol: full.tail(1).assign(index_code=symbol))
    out = sync.sync_csindex_returns(settings, conn)
    assert len(out["errors"]) == 3
    assert len(pd.read_parquet(settings.raw_dir / "csindex" / "total_return" / "H00905.parquet")) == 2


def test_other_free_sync_never_replaces_on_failure(settings, monkeypatch):
    conn = connect(settings.state_db)
    monkeypatch.setattr(csindex, "fetch_index_history", lambda symbol: pd.DataFrame(
        {"index_code": [symbol], "date": ["2022-01-04"], "close": [100.0]}))
    assert not sync.sync_csindex_returns(settings, conn)["errors"]
    path = settings.raw_dir / "csindex" / "total_return" / "H00905.parquet"
    monkeypatch.setattr(csindex, "fetch_index_history", lambda symbol: (_ for _ in ()).throw(RuntimeError("failed")))
    assert len(sync.sync_csindex_returns(settings, conn)["errors"]) == 3
    assert pd.read_parquet(path).iloc[0]["close"] == 100.0

    monkeypatch.setattr(csindex, "fetch_index_weights", lambda symbol: pd.DataFrame(
        {"index_code": [symbol], "snapshot_date": ["2022-08-31"], "code": ["sh.600000"], "weight": [0.1]}))
    assert not sync.sync_csindex_weights(settings, conn)["errors"]
    assert all(v["existing"] for v in sync.sync_csindex_weights(settings, conn)["symbols"].values())

    monkeypatch.setattr(eastmoney, "fetch_holder_history", lambda code: pd.DataFrame(
        {"code": [code], "stat_date": ["2021-12-31"], "announce_date": ["2022-01-10"], "holder_num": [100]}))
    assert not sync.sync_eastmoney_holders(settings, conn, ["sz.000001"])["errors"]
    holder_path = settings.raw_dir / "eastmoney" / "holders" / "sz.000001.parquet"
    assert pd.read_parquet(holder_path).iloc[0]["announce_date"] == "2022-01-10"


def test_exchange_margin_rows_keep_unmatched_fields_empty_and_westock_priority(settings):
    day = "2019-10-11"
    path = settings.raw_dir / "exchange" / "margin" / f"{day}.parquet"
    sync._write_parquet(pd.DataFrame([_margin("sz.000001", day, 100.0)]), path)
    exchange_rows = margin_rows(_load_exchange_margin(settings.raw_dir / "exchange", ["sz.000001"], day))
    assert pd.isna(exchange_rows.iloc[0]["mg_fin_buy_share"])
    westock_rows = margin_rows(pd.DataFrame([_margin("sz.000001", day, 200.0)]))
    later = pd.DataFrame([_margin("sz.000001", "2019-10-14", 105.0),
                          _margin("sz.000002", "2019-10-14", 105.0)])
    filtered = _exchange_before_westock(pd.concat([pd.read_parquet(path), later]),
                                         pd.DataFrame([_margin("sz.000001", day, 200.0)]))
    assert filtered["code"].tolist() == ["sz.000002"]
    combined = pd.concat([exchange_rows, westock_rows]).drop_duplicates(["code", "pub_date"], keep="last")
    assert combined.iloc[0]["_fin_value"] == 200.0


def test_margin_overlap_audit_is_dev_only(settings):
    root = settings.raw_dir
    for day in ("2019-10-11", "2023-01-03"):
        ex = pd.DataFrame([_margin("sz.000001", day, 100.0)])
        ws = pd.DataFrame([_margin("sz.000001", day, 100.0)])
        sync._write_parquet(ex, root / "exchange" / "margin" / f"{day}.parquet")
        sync._write_parquet(ws, root / "westock" / "margin" / f"{day}.parquet")
    result = compare(root)
    assert result["days"] == result["overlap_rows"] == 1
    assert result["fields"]["sz"]["FinanceValue"]["exact"] == 1
