"""Local cninfo announcement index, classification, and cited PDF text."""

import hashlib
import io
import json
import os
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from alphasieve.data.providers import cninfo

TAXONOMY = (
    ("convertible_redemption", r"可转债.*(强赎|赎回|有条件赎回)"),
    ("convertible_revision", r"可转债.*(下修|向下修正)|向下修正.*转股价格"),
    ("regulatory_penalty", r"行政处罚|监管措施|纪律处分|立案调查"),
    ("regulatory_inquiry", r"问询函|关注函|监管函|回复.*问询"),
    ("audit_opinion", r"审计意见|非标准.*审计|保留意见|无法表示意见|否定意见"),
    ("auditor_change", r"变更.*会计师|改聘.*会计师|续聘.*会计师"),
    ("earnings_guidance", r"业绩预告|业绩预增|业绩预亏|业绩修正"),
    ("repurchase", r"回购.*(股份|股票)|股份回购|股票回购"),
    ("shareholding_change", r"增持|减持|持股变动|权益变动"),
    ("litigation", r"诉讼|仲裁"),
    ("annual_report", r"年度报告|年报"),
    ("semiannual_report", r"半年度报告|半年报"),
    ("quarterly_report", r"季度报告|一季报|三季报"),
    ("convertible_other", r"可转债|可转换公司债"),
    ("material_event", r"重大事项|重大合同|重大资产重组|收购|合并"),
    ("equity_change", r"股权变动|股权转让|股权激励"),
)
EXTRACT_TYPES = {"earnings_guidance", "repurchase", "shareholding_change", "convertible_revision",
                 "convertible_redemption", "regulatory_inquiry", "regulatory_penalty", "litigation",
                 "audit_opinion", "auditor_change"}
HIGH_TYPES = {"convertible_redemption", "regulatory_penalty", "audit_opinion", "litigation"}
MEDIUM_TYPES = EXTRACT_TYPES | {"material_event", "annual_report", "semiannual_report"}


def classify(title: str) -> dict:
    clean = re.sub(r"<[^>]+>", "", title)
    event_type = next((name for name, pattern in TAXONOMY if re.search(pattern, clean, re.I)), "other")
    return {"event_type": event_type, "importance": "high" if event_type in HIGH_TYPES else
            "medium" if event_type in MEDIUM_TYPES else "low", "extract_text": event_type in EXTRACT_TYPES}


def _root(settings) -> Path:
    return settings.raw_dir / "cninfo"


def list_local(settings, *, code: str | None = None, since: str | None = None,
               event_type: str | None = None) -> list[dict]:
    root = _root(settings) / "announcements"
    rows = []
    for path in sorted(root.glob("*.parquet"), reverse=True):
        if since and path.stem < since:
            break
        for row in pd.read_parquet(path).to_dict("records"):
            if code and row["code"] != code:
                continue
            row.update(classify(row["title"]))
            if event_type and row["event_type"] != event_type:
                continue
            rows.append(row)
    return sorted(rows, key=lambda row: row["published_at"], reverse=True)


def get_local(settings, announcement_id: str) -> dict:
    if not re.fullmatch(r"\d+", announcement_id):
        raise ValueError("announcement ID must be numeric")
    for path in sorted((_root(settings) / "announcements").glob("*.parquet"), reverse=True):
        df = pd.read_parquet(path)
        matches = df[df["announcement_id"].astype(str) == announcement_id]
        if not matches.empty:
            row = matches.iloc[0].to_dict()
            row.update(classify(row["title"]))
            return row
    raise KeyError(f"announcement {announcement_id} is not in local metadata")


def fetch(settings, announcement_id: str) -> dict:
    row = get_local(settings, announcement_id)
    return _fetch_row(settings, row)


def _fetch_row(settings, row: dict) -> dict:
    row = {**row, **classify(row["title"])}
    announcement_id = str(row["announcement_id"])
    root = _root(settings) / "documents" / announcement_id
    pdf_path = root / "original.pdf"
    text_path = root / "text.json"
    root.mkdir(parents=True, exist_ok=True)
    if not pdf_path.exists():
        raw = cninfo.download_pdf(row["pdf_url"])
        temp = pdf_path.with_suffix(".pdf.tmp")
        temp.write_bytes(raw)
        os.replace(temp, pdf_path)
    raw = pdf_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if text_path.exists():
        saved = json.loads(text_path.read_text())
        if saved.get("pdf_sha256") == digest:
            return saved
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    pages = []
    offset = 0
    for number, page in enumerate(reader.pages, 1):
        value = page.extract_text() or ""
        pages.append({"page": number, "start": offset, "end": offset + len(value), "text": value})
        offset += len(value) + 1
    result = {"announcement_id": announcement_id, "source": "cninfo", "pdf_url": row["pdf_url"],
              "pdf_sha256": digest, "pdf_path": str(pdf_path), "published_at": row["published_at"],
              "fetched_at": datetime.now(UTC).isoformat(), "event_type": row["event_type"],
              "pages": pages, "text_length": max(0, offset - 1)}
    temp = text_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, text_path)
    return result


def evidence_pack(settings, announcement_id: str, *, max_chars: int = 12000) -> dict:
    """Return citeable page excerpts; each location resolves to original.pdf#page=N."""
    result = fetch(settings, announcement_id)
    remaining = max_chars
    excerpts = []
    for page in result["pages"]:
        if remaining <= 0:
            break
        excerpt = page["text"][:remaining]
        if excerpt.strip():
            excerpts.append({"page": page["page"], "char_start": page["start"],
                             "char_end": page["start"] + len(excerpt), "text": excerpt,
                             "citation": f"{announcement_id}#page={page['page']}"})
        remaining -= len(excerpt)
    return {"announcement": get_local(settings, announcement_id), "pdf_sha256": result["pdf_sha256"],
            "pdf_path": result["pdf_path"], "excerpts": excerpts}


def fetch_selected(settings, rows: list[dict]) -> list[dict]:
    """Explicit batch hook for selected event types; each PDF request remains serial."""
    return [_fetch_row(settings, row) for row in rows
            if classify(row["title"])["extract_text"]]


def recent_for(settings, codes, since: str | None = None) -> list[dict]:
    """Important local announcements for a holdings check-up; no network or panel access."""
    since = since or (date.today() - timedelta(days=14)).isoformat()
    wanted = {str(code).zfill(6) for code in codes}
    return [row for row in list_local(settings, since=since) if row["code"] in wanted
            and row["importance"] in ("high", "medium")]
