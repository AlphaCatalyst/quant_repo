"""Official SW stock classification history and classification names."""

import hashlib
import io
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

from alphasieve.data.providers import free_http

STOCK_URL = "https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls"
CLASS_CODE_URLS = {
    "SW2014": "https://www.swsresearch.com/swindex/pdf/SwClass2021/SwClassCode_2014.xls",
    "SW2021": "https://www.swsresearch.com/swindex/pdf/SwClass2021/SwClassCode_2021.xls",
}


def fetch(url: str) -> bytes:
    if url not in (STOCK_URL, *CLASS_CODE_URLS.values()):
        raise ValueError("unsupported SW download URL")
    try:
        return free_http.get(url, referer="https://www.swsresearch.com/swindex/")
    except free_http.FreeDataError as original:
        # The official host has served an incomplete TLS chain. Restrict this fallback
        # to fixed official URLs and retain raw bytes for later verification.
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                       "Referer": "https://www.swsresearch.com/swindex/"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, context=ssl._create_unverified_context(), timeout=25) as r:
                    data = r.read(free_http.MAX_BYTES + 1)
                if len(data) <= free_http.MAX_BYTES and data.startswith(b"\xd0\xcf\x11\xe0"):
                    return data
            except (OSError, urllib.error.URLError):
                pass
            if attempt < 2:
                time.sleep(2 ** attempt)
        raise original


def parse_names(data: bytes, version: str | None = None) -> pd.DataFrame:
    """Parse an official classification workbook, retaining its version and byte hash."""
    if version is not None and version not in CLASS_CODE_URLS:
        raise ValueError(f"unsupported SW classification version: {version}")
    workbook = pd.ExcelFile(io.BytesIO(data))
    pieces = []
    for sheet in workbook.sheet_names:
        source = pd.read_excel(workbook, sheet_name=sheet, dtype=str)
        source.columns = [str(c).strip().replace(" ", "") for c in source.columns]
        code_col = next((c for c in source if c in ("行业代码", "分类代码", "代码")), None)
        name_col = next((c for c in source if c in ("行业名称", "分类名称", "名称")), None)
        if not code_col:
            continue
        if name_col:
            pieces.append(source[[code_col, name_col]].rename(columns={code_col: "sw_code", name_col: "name"}))
        elif {"一级行业名称", "二级行业名称", "三级行业名称"}.issubset(source.columns):
            part = source.rename(columns={code_col: "sw_code"}).copy()
            part["sw_code"] = part["sw_code"].str.replace(r"\.0$", "", regex=True).str.zfill(6)
            level = part["sw_code"].str[2:].eq("0000").map({True: "一级行业名称", False: "二级行业名称"})
            level = level.mask(~part["sw_code"].str[4:].eq("00"), "三级行业名称")
            part["name"] = [row[col] for (_, row), col in zip(part.iterrows(), level, strict=True)]
            pieces.append(part[["sw_code", "name"]])
    if not pieces:
        raise free_http.FreeDataError("SW name workbook has no code/name columns")
    frame = pd.concat(pieces, ignore_index=True).dropna()
    frame["sw_code"] = frame["sw_code"].str.replace(r"\.0$", "", regex=True).str.zfill(6)
    frame = frame[frame["sw_code"].str.fullmatch(r"\d{6}")]
    frame = frame.drop_duplicates("sw_code", keep="last")
    if frame.empty:
        raise free_http.FreeDataError("SW name workbook contains no six-digit codes")
    if version is not None:
        frame["sw_version"] = version
        frame["source_url"] = CLASS_CODE_URLS[version]
        frame["source_sha256"] = hashlib.sha256(data).hexdigest()
    return frame.sort_values("sw_code").reset_index(drop=True)


def load_official_names(root: Path, stamp: str) -> pd.DataFrame:
    """Read the two unchanged, date-stamped official code books from the raw cache."""
    return pd.concat([
        parse_names((root / f"SwClassCode_{version[2:]}_{stamp}.xls").read_bytes(), version)
        for version in CLASS_CODE_URLS
    ], ignore_index=True)


def parse_history(data: bytes, names: pd.DataFrame) -> pd.DataFrame:
    source = pd.read_excel(io.BytesIO(data))
    required = {"股票代码", "计入日期", "行业代码", "更新日期"}
    if source.empty or not required.issubset(source.columns):
        raise free_http.FreeDataError("SW stock workbook columns/rows missing")
    digits = source["股票代码"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    prefix = digits.str[:2].map(lambda x: "sh" if x in ("60", "68", "90") else
                                 "bj" if x in ("83", "87", "88", "92") else "sz")
    frame = pd.DataFrame({
        "code": prefix + "." + digits,
        "effective_date": pd.to_datetime(source["计入日期"], errors="coerce").dt.strftime("%Y-%m-%d"),
        "sw_code": source["行业代码"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6),
        "updated_at": pd.to_datetime(source["更新日期"], errors="coerce"),
    })
    if frame[["code", "effective_date", "sw_code", "updated_at"]].isna().any().any() or \
            not frame["sw_code"].str.fullmatch(r"\d{6}").all():
        raise free_http.FreeDataError("SW stock workbook contains invalid values")
    frame["history_source_sha256"] = hashlib.sha256(data).hexdigest()
    versioned = "sw_version" in names.columns and names["sw_version"].notna().any()
    if versioned:
        available = set(zip(names["sw_version"], names["sw_code"], strict=True))
        old = frame["sw_code"].map(lambda c: ("SW2014", c) in available)
        new = frame["sw_code"].map(lambda c: ("SW2021", c) in available)
        preferred_new = frame["effective_date"].ge("2021-07-30")
        frame["sw_version"] = "unknown"
        frame.loc[old & (~preferred_new | ~new), "sw_version"] = "SW2014"
        frame.loc[new & (preferred_new | ~old), "sw_version"] = "SW2021"
        labels = names.drop_duplicates(["sw_version", "sw_code"]).set_index(["sw_version", "sw_code"])["name"]
        if "source_sha256" in names:
            hashes = names.drop_duplicates("sw_version").set_index("sw_version")["source_sha256"]
            frame["name_source_sha256"] = frame["sw_version"].map(hashes)
        else:
            frame["name_source_sha256"] = pd.NA
    else:
        labels = names.set_index("sw_code")["name"]
        frame["sw_version"] = "unknown"
        frame["name_source_sha256"] = pd.NA
    for level, width in ((1, 2), (2, 4), (3, 6)):
        key = f"l{level}_code"
        frame[key] = frame["sw_code"].str[:width].str.ljust(6, "0")
        if versioned:
            frame[f"l{level}_name"] = pd.Series(
                labels.reindex(pd.MultiIndex.from_arrays([frame["sw_version"], frame[key]])).to_numpy(),
                index=frame.index)
        else:
            frame[f"l{level}_name"] = frame[key].map(labels)
    return frame.sort_values(["code", "effective_date", "updated_at"]).drop_duplicates(
        ["code", "effective_date"], keep="last").reset_index(drop=True)


def infer_current_names(history: pd.DataFrame, snapshot: pd.DataFrame) -> pd.DataFrame:
    """Map current SW codes from unanimous westock membership; never guess old codes."""
    current = history.sort_values(["code", "effective_date"]).drop_duplicates("code", keep="last")
    rows = current.merge(snapshot[["code", "level", "sector_name"]], on="code")
    found = []
    for level in (1, 2, 3):
        part = rows[rows["level"] == level]
        key = f"l{level}_code"
        counts = part.groupby(key)["sector_name"].nunique()
        valid = counts[counts == 1].index
        labels = part[part[key].isin(valid)].drop_duplicates(key)
        found.extend({"sw_code": row[key], "name": row["sector_name"], "source": "westock_membership_2026"}
                     for _, row in labels.iterrows())
    return pd.DataFrame(found, columns=["sw_code", "name", "source"])
