"""Serial, rate-limited access to cninfo public announcement metadata and PDFs."""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from alphasieve.data.providers.free_http import FreeDataError

QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
PDF_BASE = "https://static.cninfo.com.cn/"
REFERER = "https://www.cninfo.com.cn/"
PAGE_SIZE = 30  # cninfo silently caps larger values at 30
PAGE_LIMIT = 100  # cninfo repeats page 1 beyond page 100
MARKET_PLATES = ("szmb", "szcy", "shmb", "shkcp", "bj")
CATEGORIES = {
    "年报": "category_ndbg_szsh", "半年报": "category_bndbg_szsh",
    "一季报": "category_yjdbg_szsh", "三季报": "category_sjdbg_szsh",
    "业绩预告": "category_yjygjxz_szsh", "权益分派": "category_qyfpxzcs_szsh",
    "股权变动": "category_gqbd_szsh", "可转债": "category_kzzq_szsh",
    "风险提示": "category_fxts_szsh", "补充更正": "category_bcgz_szsh",
}
TITLE_CATEGORIES = {"季报": r"一季度报告|三季度报告|第一季度报告|第三季度报告",
                    "重大事项": r"重大事项|重大合同|重大资产", "回购": r"回购",
                    "增减持": r"增持|减持", "诉讼": r"诉讼|仲裁", "监管问询": r"问询函|关注函|监管函",
                    "可转债相关": r"可转债|可转换公司债"}
_LOCK = threading.Lock()
_NEXT = 0.0
GAP_SECONDS = 0.8


def _request(url: str, data: bytes | None = None, *, max_bytes: int = 30_000_000) -> bytes:
    global _NEXT
    headers = {"User-Agent": "Mozilla/5.0", "Referer": REFERER}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        headers["X-Requested-With"] = "XMLHttpRequest"
    last = None
    for attempt in range(4):
        with _LOCK:
            pause = max(0, _NEXT - time.monotonic())
            if pause:
                time.sleep(pause)
            _NEXT = time.monotonic() + GAP_SECONDS
            try:
                request = urllib.request.Request(url, data=data, headers=headers)
                with urllib.request.urlopen(request, timeout=30) as response:
                    raw = response.read(max_bytes + 1)
                    if len(raw) > max_bytes:
                        raise FreeDataError("cninfo response exceeds size limit")
                    return raw
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in (408, 429, 500, 502, 503, 504):
                    break
                retry_after = exc.headers.get("Retry-After")
                delay = max(2 ** attempt, int(retry_after) if retry_after and retry_after.isdigit() else 0)
            except (OSError, TimeoutError) as exc:
                last = exc
                delay = 2 ** attempt
            if attempt < 3:
                time.sleep(delay)
    raise FreeDataError(f"cninfo request failed: {last}") from last


def normalize(item: dict) -> dict:
    stamp = datetime.fromtimestamp(int(item["announcementTime"]) / 1000, ZoneInfo("Asia/Shanghai"))
    adjunct = item.get("adjunctUrl") or ""
    return {
        "announcement_id": str(item["announcementId"]), "code": str(item.get("secCode") or ""),
        "name": item.get("secName") or "", "title": re.sub(r"<[^>]+>", "", item.get("announcementTitle") or ""),
        "published_at": stamp.isoformat(timespec="seconds"), "published_date": stamp.date().isoformat(),
        "org_id": item.get("orgId") or "", "pdf_url": urllib.parse.urljoin(PDF_BASE, adjunct) if adjunct else "",
        "adjunct_size_kb": item.get("adjunctSize"), "source": "cninfo",
    }


@lru_cache(maxsize=1)
def _stock_ids() -> dict[str, str]:
    payload = json.loads(_request("https://www.cninfo.com.cn/new/data/szse_stock.json"))
    return {str(item["code"]): str(item["orgId"]) for item in payload["stockList"]}


def query(start: str, end: str, *, code: str | None = None, category: str | None = None,
          max_pages: int | None = None) -> list[dict]:
    """Query a closed date range. cninfo's all-market listing uses stock='' and column=szse."""
    date.fromisoformat(start)
    date.fromisoformat(end)
    if start > end:
        raise ValueError("start must not exceed end")
    if category and category not in CATEGORIES and category not in TITLE_CATEGORIES:
        raise ValueError(f"unknown cninfo category: {category}")
    if code and not re.fullmatch(r"\d{6}", code):
        raise ValueError("code must contain six digits")
    stock = f"{code},{_stock_ids()[code]}" if code else ""
    payload = {"pageNum": 1, "pageSize": PAGE_SIZE, "column": "szse", "tabName": "fulltext",
               "plate": "", "stock": stock, "searchkey": "", "secid": "", "category": CATEGORIES.get(category, ""),
               "trade": "", "seDate": f"{start}~{end}", "sortName": "", "sortType": "", "isHLtitle": "true"}
    def pages(plate: str) -> tuple[list[dict], int]:
        payload["plate"] = plate
        result_rows = []
        page = 1
        total = 0
        while True:
            payload["pageNum"] = page
            body = urllib.parse.urlencode(payload).encode()
            result = json.loads(_request(QUERY_URL, body))
            batch = result.get("announcements") or []
            total = int(result.get("totalAnnouncement") or 0)
            if total > PAGE_LIMIT * PAGE_SIZE and max_pages is None:
                raise _PageLimit(total)
            result_rows.extend(normalize(item) for item in batch)
            if not batch or page * PAGE_SIZE >= total or (max_pages and page >= max_pages):
                break
            page += 1
        if max_pages is None and len(result_rows) != total:
            raise FreeDataError(f"cninfo pagination incomplete: {len(result_rows)} of {total}")
        return result_rows, total

    try:
        rows, total = pages("")
    except _PageLimit as exc:
        if code:
            raise FreeDataError(f"stock query exceeds cninfo page limit: {exc.total}") from exc
        rows = []
        total = 0
        for plate in MARKET_PLATES:
            try:
                part, count = pages(plate)
            except _PageLimit as plate_exc:
                raise FreeDataError(f"cninfo {plate} exceeds page limit: {plate_exc.total}") from plate_exc
            rows.extend(part)
            total += count
    unique = list({row["announcement_id"]: row for row in rows}.values())
    if max_pages is None and len(unique) < total:
        raise FreeDataError(f"cninfo pagination duplicated records: {len(unique)} unique of {total}")
    if code:
        unique = [row for row in unique if row["code"] == code]
    if category in TITLE_CATEGORIES:
        unique = [row for row in unique if re.search(TITLE_CATEGORIES[category], row["title"])]
    return unique


class _PageLimit(Exception):
    def __init__(self, total: int):
        self.total = total


def download_pdf(url: str) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "static.cninfo.com.cn" or not parsed.path.lower().endswith(".pdf"):
        raise ValueError("PDF URL must be a cninfo HTTPS PDF")
    raw = _request(url, max_bytes=80_000_000)
    if not raw.startswith(b"%PDF"):
        raise FreeDataError("cninfo response is not a PDF")
    return raw
