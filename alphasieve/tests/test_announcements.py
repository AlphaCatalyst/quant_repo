import io
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pypdf
from pypdf import PdfWriter

from alphasieve.announcements import classify, evidence_pack, fetch, fetch_selected, list_local, recent_for


def _settings(tmp_path):
    root = tmp_path / "cninfo" / "announcements"
    root.mkdir(parents=True)
    fixture = json.loads((Path(__file__).parent / "fixtures" / "cninfo" / "page.json").read_text())
    from alphasieve.data.providers.cninfo import normalize

    pd.DataFrame([normalize(item) for item in fixture["announcements"]]).to_parquet(root / "2026-07-15.parquet")
    return SimpleNamespace(raw_dir=tmp_path)


def test_classification_and_recent(tmp_path):
    settings = _settings(tmp_path)
    assert classify("可转债提前赎回公告")["event_type"] == "convertible_redemption"
    assert classify("收到行政处罚决定书")["importance"] == "high"
    assert classify("年度报告")["event_type"] == "annual_report"
    assert len(list_local(settings, event_type="earnings_guidance")) == 1
    assert [row["code"] for row in recent_for(settings, ["000001"], "2026-07-01")] == ["000001"]


def test_selected_extraction_hook(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    calls = []
    monkeypatch.setattr("alphasieve.announcements._fetch_row",
                        lambda settings, row: calls.append(row["announcement_id"]))
    fetch_selected(settings, list_local(settings))
    assert set(calls) == {"1225000001", "1225000002"}


def test_fetch_preserves_pdf_hash_and_page_location(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    monkeypatch.setattr("alphasieve.data.providers.cninfo.download_pdf", lambda url: buffer.getvalue())
    class Page:
        def __init__(self, value):
            self.value = value

        def extract_text(self):
            return self.value

    monkeypatch.setattr(pypdf, "PdfReader", lambda stream: SimpleNamespace(pages=[Page("first"), Page("second")]))
    result = fetch(settings, "1225000001")
    assert result["pages"][0]["page"] == 1
    assert result["pages"][0]["start"] == 0
    assert result["pages"][1]["start"] == 6
    assert result["pages"][1]["end"] == 12
    assert len(result["pdf_sha256"]) == 64
    pack = evidence_pack(settings, "1225000001")
    assert pack["pdf_sha256"] == result["pdf_sha256"]
    assert pack["excerpts"][1]["citation"] == "1225000001#page=2"
    assert Path(result["pdf_path"]).read_bytes() == buffer.getvalue()
