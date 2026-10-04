import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest

from alphasieve.data.providers import cninfo
from alphasieve.data.sync import sync_cninfo_announcements

FIXTURE = Path(__file__).parent / "fixtures" / "cninfo" / "page.json"


def test_query_normalizes_fixture(monkeypatch):
    calls = []

    def fake(url, data=None, **kwargs):
        calls.append((url, data.decode()))
        return FIXTURE.read_bytes()

    monkeypatch.setattr(cninfo, "_request", fake)
    rows = cninfo.query("2026-07-15", "2026-07-15", category="业绩预告")
    assert len(rows) == 2
    assert rows[0]["announcement_id"] == "1225000001"
    assert rows[0]["pdf_url"] == "https://static.cninfo.com.cn/finalpage/2026-07-15/1225000001.PDF"
    assert rows[0]["published_at"].endswith("+08:00")
    assert "category_yjygjxz_szsh" in calls[0][1]


def test_stock_query_uses_org_id_and_title_category(monkeypatch):
    calls = []
    monkeypatch.setattr(cninfo, "_stock_ids", lambda: {"000001": "gssz0000001"})

    def fake(url, data=None, **kwargs):
        calls.append(data.decode())
        return FIXTURE.read_bytes()

    monkeypatch.setattr(cninfo, "_request", fake)
    rows = cninfo.query("2026-07-15", "2026-07-15", code="000001")
    assert [row["announcement_id"] for row in rows] == ["1225000001"]
    assert "stock=000001%2Cgssz0000001" in calls[0]
    assert [row["announcement_id"] for row in cninfo.query("2026-07-15", "2026-07-15", category="回购")] == [
        "1225000002"]


def test_all_market_splits_when_page_limit_is_exceeded(monkeypatch):
    fixture = json.loads(FIXTURE.read_text())
    seen = []

    def fake(url, data=None, **kwargs):
        plate = parse_qs(data.decode(), keep_blank_values=True)["plate"][0]
        seen.append(plate)
        if not plate:
            return json.dumps({**fixture, "totalAnnouncement": 3001}).encode()
        if plate == "szmb":
            return json.dumps(fixture).encode()
        return json.dumps({"totalAnnouncement": 0, "announcements": []}).encode()

    monkeypatch.setattr(cninfo, "_request", fake)
    rows = cninfo.query("2026-07-15", "2026-07-15")
    assert len(rows) == 2
    assert seen == ["", *cninfo.MARKET_PLATES]


def test_query_validation_and_pdf_origin(monkeypatch):
    with pytest.raises(ValueError):
        cninfo.query("2026-10-02", "2026-10-01")
    with pytest.raises(ValueError):
        cninfo.query("2026-10-01", "2026-10-02", code="../../")
    with pytest.raises(ValueError):
        cninfo.download_pdf("https://example.com/a.pdf")


def test_sync_daily_partition_and_snapshot(tmp_path, monkeypatch):
    payload = json.loads(FIXTURE.read_text())
    monkeypatch.setattr(cninfo, "query",
                        lambda start, end: [cninfo.normalize(item) for item in payload["announcements"]])
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE data_snapshots (snapshot_id TEXT PRIMARY KEY, source TEXT, dataset TEXT, "
                 "params_json TEXT, rows INTEGER, content_hash TEXT, path TEXT, fetched_at TEXT)")
    settings = SimpleNamespace(raw_dir=tmp_path)
    result = sync_cninfo_announcements(settings, conn, "2026-07-15", "2026-07-15")
    assert result["rows"] == 2
    assert result["fetched_days"] == 1
    assert (tmp_path / "cninfo" / "announcements" / "2026-07-15.parquet").exists()
    assert conn.execute("SELECT source, dataset, rows FROM data_snapshots").fetchone() == (
        "cninfo", "cninfo_announcements", 2)
    assert sync_cninfo_announcements(settings, conn, "2026-07-15", "2026-07-15")["fetched_days"] == 0
    monkeypatch.setattr(cninfo, "query", lambda start, end: [cninfo.normalize(payload["announcements"][0])])
    refreshed = sync_cninfo_announcements(settings, conn, "2026-07-15", "2026-07-15", refresh=True)
    assert refreshed["rows"] == 1
