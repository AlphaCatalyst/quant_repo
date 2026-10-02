"""Normalize westock sell-side report text into point-in-time EPS estimates."""

import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import pandas as pd

COLUMNS = (
    "report_id",
    "code",
    "publish_date",
    "broker",
    "rating",
    "rating_raw",
    "fiscal_year",
    "eps",
    "is_revision",
    "title",
)
MEDIA = re.compile(r"报$|网|新闻|财经|杂志|周刊|资讯|日报|时报|快讯")
KEY = re.compile(r"(?i)EPS|每股收益|每股盈利")
FORECAST = re.compile(r"预计|预测|预期|估计|测算|盈利预测|投资建议|分别为|维持|调整|上调|下调|看好|假设")
ACTUAL = re.compile(
    r"(?:实现|录得|报告期|上半年|前三季度|一季度|三季度|半年报|季报|年报).{0,55}(?:基本|摊薄)?\s*(?:EPS|每股收益)|(?:基本|摊薄)\s*(?:EPS|每股收益)\s*(?:为|达|[0-9])",
    re.I,
)
SPAN = re.compile(r"(?<!\d)((?:20)?\d{2})\s*(?:年|E)?\s*[-—~～至到]\s*((?:20)?\d{2})\s*(?:年|E)?(?!\d)")
YEAR = re.compile(r"(?<!\d)((?:20)?\d{2})\s*(?:年|E)?(?!\d)")
NUM = re.compile(r"(?<![\d.])-?\d+(?:\.\d+)?(?![\d.])")
RATINGS = (
    "强烈推荐",
    "强推",
    "强烈买入",
    "谨慎推荐",
    "审慎推荐",
    "优于大市",
    "跑赢大市",
    "跑赢行业",
    "强于大市",
    "同步大市",
    "中性",
    "持有",
    "增持",
    "推荐",
    "买入",
    "减持",
    "卖出",
    "回避",
)
RATING_RE = re.compile("|".join(RATINGS))
RATING_MAP = {
    **dict.fromkeys(("强烈推荐", "强推", "强烈买入"), "strong_buy"),
    **dict.fromkeys(
        ("买入", "推荐", "增持", "谨慎推荐", "审慎推荐", "优于大市", "跑赢大市", "跑赢行业", "强于大市"), "buy"
    ),
    **dict.fromkeys(("中性", "持有", "同步大市"), "hold"),
    "减持": "sell",
    "卖出": "strong_sell",
    "回避": "strong_sell",
}
STOCK_CODE = re.compile(r"(?<!\d)([036]\d{5})(?!\d)")


def is_broker(title: str) -> bool:
    match = re.match(r"^【([^】]+)】", title or "")
    return bool(match) and not MEDIA.search(match.group(1))


def _year(value: str) -> int:
    n = int(value)
    return 2000 + n if n < 100 else n


def _years(text: str) -> list[int]:
    for m in reversed(list(SPAN.finditer(text))):
        a, b = _year(m[1]), _year(m[2])
        if 2000 <= a <= b <= min(a + 4, 2035):
            return list(range(a, b + 1))
    # A delimited list avoids treating nearby price/PE numbers as years.
    for m in reversed(
        list(
            re.finditer(
                r"(?<!\d)((?:20)?\d{2}(?:\s*(?:年|E))?(?:\s*[/、,，和及]\s*(?:20)?\d{2}(?:\s*(?:年|E))?){1,3})(?!\d)",
                text,
            )
        )
    ):
        ys = [_year(x[1]) for x in YEAR.finditer(m[1])]
        if len(ys) >= 2 and all(2000 <= y <= 2035 for y in ys) and ys == sorted(set(ys)):
            return ys
    for m in YEAR.finditer(text):
        y = _year(m[1])
        if 2000 <= y <= 2035 and (m.group().endswith(("年", "E")) or len(m[1]) == 4):
            return [y]
    return []


def _remove_old_values(text: str) -> str:
    return re.sub(r"[（(][^()（）]{0,100}[）)]", "", text)


def _eps_values(sentence: str, key: re.Match, count: int) -> list[float]:
    prefix = _remove_old_values(sentence[: key.start()])
    before = re.search(
        r"(?:20\d{2}|\d{2})\s*(?:年|E)?(?:\s*[-—~～至到]\s*(?:20\d{2}|\d{2})\s*(?:年|E)?)?.{0,20}?((?:-?\d+(?:\.\d+)?\s*[/、,，和及]\s*){1,3}-?\d+(?:\.\d+)?)\s*元?\s*$",
        prefix,
    )
    if before:
        vals = [float(x[0]) for x in NUM.finditer(before[1])]
        if len(vals) == count and all(-50 < x < 500 for x in vals):
            return vals
    tail = _remove_old_values(sentence[key.end() :])
    tail = re.split(
        r"(?:对应|目前|当前|动态)?\s*(?:PE|P/E|PB|市盈率|目标价|估值|股价)|风险提示", tail, maxsplit=1, flags=re.I
    )[0]
    tail = tail[:180]
    # A year immediately after EPS is a year marker, not an EPS observation.
    tail = re.sub(r"^\s*(?:为|分别为|是|预测|:|：)?\s*(?:20\d{2}|\d{2})\s*年\s*", "", tail)
    vals = [float(m[0]) for m in NUM.finditer(tail)]
    return vals[:count] if len(vals) >= count and all(-50 < x < 500 for x in vals[:count]) else []


def parse_text(body: str) -> tuple[dict[int, float], str | None, str | None]:
    """Extract the first explicit whole-company EPS forecast and the final investment rating."""
    sentences = [x.strip() for x in re.split(r"[。；;\n\r]+", body or "") if x.strip()]
    estimates: dict[int, float] = {}
    revision = None
    for i, sentence in enumerate(sentences):
        key = KEY.search(sentence)
        if not key or not FORECAST.search(sentence):
            continue
        if re.search(r"分业务|分部|单项业务|业务板块.{0,15}EPS|可转债.{0,100}EPS摊薄", sentence, re.I):
            continue
        if ACTUAL.search(sentence) and not re.search(
            r"(?:我们)?(?:预计|预测|调整|上调|下调).{0,100}(?:EPS|每股收益|每股盈利)", sentence, re.I
        ):
            continue
        prefix = _remove_old_values(sentence[: key.start()])
        if not re.search(r"预计|预测|估计|测算|调整|上调|下调|维持|分别为|对应|看好|假设", prefix) and not re.match(
            r"\s*(?:分别为|为|是|预测)", sentence[key.end() :]
        ):
            continue
        years = _years(prefix) or _years(sentence)
        if not years:
            for previous in reversed(sentences[max(0, i - 3) : i]):
                years = _years(previous)
                if years:
                    break
        if not years:
            continue
        vals = _eps_values(sentence, key, len(years))
        if not vals:
            continue
        estimates = dict(zip(years, vals, strict=True))
        nearby = " ".join(sentences[max(0, i - 1) : i + 1])
        hit = re.search(r"上调|下调|调高|调低|维持|保持", nearby)
        revision = (
            {"上调": "up", "调高": "up", "下调": "down", "调低": "down", "维持": "maintain", "保持": "maintain"}.get(
                hit[0]
            )
            if hit
            else None
        )
        break
    # Explicit rating phrases suppress incidental mentions of a prior rating where possible.
    rated = []
    for m in RATING_RE.finditer(body or ""):
        nearby = body[max(0, m.start() - 24) : m.end() + 15]
        if "评级" in nearby or re.search(r"给予|维持|上调|下调|投资建议", nearby):
            rated.append(m[0])
    raw_rating = rated[-1] if rated else None
    return estimates, raw_rating, revision


def normalize_report(
    report_id: str, code: str, list_time: str, detail: dict, list_title: str | None = None
) -> list[dict]:
    """Return one row per forecast year, or one nullable-EPS row for rating-only reports."""
    title = (list_title or detail.get("title") or "").strip()
    body = detail.get("detail") or ""
    if not body.strip() or (title.startswith("【") and not is_broker(title)):
        return []
    code = code.replace(".", "")
    title_codes = STOCK_CODE.findall(title)
    detail_codes = STOCK_CODE.findall(detail.get("title") or "")
    opening_codes = STOCK_CODE.findall(body[:150])
    if len(set(title_codes)) > 1 or (title_codes and detail_codes and set(title_codes) != set(detail_codes)):
        return []
    if not title_codes and opening_codes and code[2:] not in opening_codes:
        return []
    if title_codes:
        code = ("sh" if title_codes[0].startswith("6") else "sz") + title_codes[0]
    if not re.fullmatch(r"(?:sh|sz)[036]\d{5}", code):
        return []
    estimates, raw_rating, revision = parse_text(body)
    raw_rating = raw_rating or (detail.get("tzpj") if detail.get("tzpj") in RATING_MAP else None)
    if not estimates and not raw_rating:
        return []
    common = {
        "report_id": str(report_id),
        "code": code[:2] + "." + code[2:],
        "publish_date": str(list_time)[:10],
        "broker": detail.get("jgmc") or None,
        "rating": RATING_MAP.get(raw_rating),
        "rating_raw": raw_rating,
        "is_revision": revision,
        "title": detail.get("title") or title,
    }
    return (
        [{**common, "fiscal_year": year, "eps": eps} for year, eps in estimates.items()]
        if estimates
        else [{**common, "fiscal_year": None, "eps": None}]
    )


def read_raw_sqlite(path: str | Path, *, latest_date: str | None = None) -> pd.DataFrame:
    """Open the active crawler database read-only; no transaction or schema changes."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    rows = []
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        query = "SELECT id, code, list_time, detail_json FROM details WHERE detail_json IS NOT NULL"
        params: tuple[str, ...] = ()
        if latest_date:
            query += " AND substr(list_time, 1, 10) <= ?"
            params = (latest_date,)
        for rid, code, timestamp, payload in conn.execute(query, params):
            try:
                detail = json.loads(payload)
            except (TypeError, ValueError):
                continue
            if isinstance(detail, dict):
                rows.extend(normalize_report(rid, code, timestamp, detail))
    result = pd.DataFrame.from_records(rows, columns=COLUMNS)
    if result.empty:
        return result
    result["fiscal_year"] = pd.array(result["fiscal_year"], dtype="Int64")
    result["eps"] = pd.to_numeric(result["eps"], errors="coerce")
    return (
        result.drop_duplicates(["report_id", "fiscal_year"])
        .sort_values(["publish_date", "code", "report_id", "fiscal_year"], kind="stable")
        .reset_index(drop=True)
    )


def write_parsed(raw_sqlite: str | Path, output: str | Path, *, latest_date: str | None = None) -> pd.DataFrame:
    """Materialize the normalized raw table at the caller-selected path."""
    frame = read_raw_sqlite(raw_sqlite, latest_date=latest_date)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".parsed-", suffix=".parquet", dir=output.parent)
    os.close(fd)
    try:
        frame.to_parquet(temp, index=False)
        os.replace(temp, output)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return frame


parse_sqlite_to_parquet = write_parsed
