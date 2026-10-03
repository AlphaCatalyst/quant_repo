import json
import sqlite3

import pandas as pd
import pytest

from alphasieve.data import sync
from alphasieve.data.providers import westock
from alphasieve.state import connect


def test_reports_stop_at_known_id_and_preserve_old_lists(settings, monkeypatch):
    monkeypatch.setattr(sync, "universe_codes", lambda *args: ["sh.600000"])
    db_path = sync.westock_root(settings) / "reports" / "reports.sqlite"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE lists (code TEXT PRIMARY KEY, rows_json TEXT NOT NULL, n INTEGER NOT NULL, "
                   "fetched_at TEXT NOT NULL)")
        db.execute("CREATE TABLE details (id TEXT PRIMARY KEY, code TEXT NOT NULL, list_time TEXT, "
                   "detail_json TEXT, fetched_at TEXT NOT NULL)")
        old = [{"id": "old", "title": "【券商】旧", "time": "2020-01-01"},
               {"id": "older", "title": "【券商】更旧", "time": "2019-01-01"}]
        db.execute("INSERT INTO lists VALUES (?,?,?,?)", ("sh600000", json.dumps(old), 2, "x"))
        db.execute("INSERT INTO details VALUES (?,?,?,?,?)", ("old", "sh600000", "2020-01-01", "{}", "x"))
        db.execute("INSERT INTO details VALUES (?,?,?,?,?)", ("older", "sh600000", "2019-01-01", "{}", "x"))
    calls = []

    def page(code, offset):
        calls.append(offset)
        return [{"id": "new", "title": "【券商】新", "time": "2021-01-01"}, old[0], old[1]]

    monkeypatch.setattr(westock, "report_page", page)
    monkeypatch.setattr(westock, "report_detail", lambda rid: {"id": rid, "content": "预测EPS 1元"})
    result = sync.sync_westock_reports(settings, connect(settings.state_db))
    assert calls == [0]
    assert result["new_reports"] == result["new_details"] == 1
    with sqlite3.connect(db_path) as db:
        ids = [r["id"] for r in json.loads(db.execute("SELECT rows_json FROM lists").fetchone()[0])]
        assert ids == ["new", "old", "older"]
        assert db.execute("SELECT COUNT(*) FROM details").fetchone()[0] == 3
    result = sync.sync_westock_reports(settings, connect(settings.state_db))
    assert result["new_reports"] == result["new_details"] == 0


def test_snapshot_day_never_rewrites_existing_partition(settings, monkeypatch):
    monkeypatch.setattr(sync, "universe_codes", lambda *args: ["sh.600000"])
    value = [1.0]
    monkeypatch.setattr(westock, "consensus", lambda codes: pd.DataFrame(
        [{"code": codes[0], "year": 2026, "eps": value[0]}]))
    conn = connect(settings.state_db)
    first = sync.sync_westock_consensus(settings, conn, "2026-10-02", workers=1)
    value[0] = 9.0
    second = sync.sync_westock_consensus(settings, conn, "2026-10-02", workers=1)
    assert first["rows"] == 1 and second["existing"]
    path = sync.westock_root(settings) / "consensus" / "2026-10-02.parquet"
    assert pd.read_parquet(path).iloc[0]["eps"] == 1.0
    assert pd.read_parquet(path).iloc[0]["snapshot_date"] == "2026-10-02"


def test_sector_and_return_kline_resume_without_changing_old_rows(settings, monkeypatch):
    monkeypatch.setattr(westock, "sector_list", lambda level: pd.DataFrame(
        [{"sector_code": f"pt{level}", "level": level, "sector_name": "x"}]))
    calls = []

    def fake_kline(codes, start, end):
        calls.append((codes[0], start, end))
        return pd.DataFrame([{"code": codes[0], "date": start, "last": len(calls)}])

    monkeypatch.setattr(westock, "kline", fake_kline)
    conn = connect(settings.state_db)
    sync.sync_westock_index_kline(settings, conn, "2012-01-03", "sector_index_daily", workers=1)
    assert calls == [("pt1", "2012-01-01", "2012-01-03"),
                     ("pt2", "2012-01-01", "2012-01-03")]
    sync.sync_westock_index_kline(settings, conn, "2012-01-05", "sector_index_daily", workers=1)
    assert calls[-2:] == [("pt1", "2012-01-02", "2012-01-05"),
                          ("pt2", "2012-01-02", "2012-01-05")]
    frame = pd.read_parquet(sync.westock_root(settings) / "sector_index_daily" / "pt1.parquet")
    assert frame.iloc[0]["last"] == 1
    sync.sync_westock_index_kline(settings, conn, "2024-05-08", "return_index_daily", workers=1)
    assert {c for c, _, _ in calls[-2:]} == set(sync.WESTOCK_NET_INDICES)


def test_kline_retries_throttled_answers(monkeypatch):
    answers = iter([{"success": False, "error": {"code": "NO_OUTPUT"}}, [{"date": "2026-09-30", "last": 1.0}]])
    monkeypatch.setattr(westock, "_call", lambda args: next(answers))
    monkeypatch.setattr(westock, "KLINE_GAP_S", 0)
    monkeypatch.setattr(westock, "KLINE_BACKOFF_S", 0)
    frame = westock.kline(["pt01801010"], "2026-09-01", "2026-09-30")
    assert frame[["code", "date", "last"]].values.tolist() == [["pt01801010", "2026-09-30", 1.0]]

    monkeypatch.setattr(westock, "_call", lambda args: {"success": False})
    with pytest.raises(westock.WestockError):
        westock.kline(["pt01801010"], "2026-09-01", "2026-09-30")


def test_provider_filters_media_and_retries_consensus_missing_sections(monkeypatch):
    assert westock.is_broker_report("【中信证券】公司点评")
    assert not westock.is_broker_report("【证券时报】市场快讯")
    calls = []

    def fake_call(args):
        codes = args[1].split(",")
        calls.append(codes)
        if len(codes) == 1:
            return [{"year": 2026, "eps": 1.0}]
        return {"sections": [[{"year": 2026, "eps": 1.0}]]}

    monkeypatch.setattr(westock, "_call", fake_call)
    frame = westock.consensus(["sh.600000", "sz.000001"])
    assert sorted(frame["code"]) == ["sh.600000", "sz.000001"]
    assert calls == [["sh600000", "sz000001"], ["sh600000"], ["sz000001"]]
