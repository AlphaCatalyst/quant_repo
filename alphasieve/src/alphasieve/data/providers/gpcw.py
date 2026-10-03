"""Tongdaxin quarterly professional financial archives."""

import hashlib
import re
import struct
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
from mootdx.affair import Affair

from alphasieve.data.providers.gpcw_columns import columns

NAME = re.compile(r"gpcw(\d{8})\.zip\Z")
HEADER = struct.Struct("<hIHIII")
ITEM = struct.Struct("<6scI")

# These names are copied from mootdx 0.11.7 (MIT). Duplicate source labels get
# a position suffix when written to Parquet, which requires unique columns.
ALIASES = {
    "基本每股收益": "eps_basic",
    "净资产收益率": "roe",
    "股东人数(户)": "shareholder_count",
    "基金机构数": "fund_institution_count",
    "基金持股量": "fund_shares",
    "QFII机构数": "qfii_institution_count",
    "QFII持股量": "qfii_shares",
    "社保机构数": "social_security_institution_count",
    "社保持股量": "social_security_shares",
    "业绩预告公告日期 ": "forecast_announce_date",
    "业绩快报公告日期": "express_announce_date",
    "财报公告日期": "report_announce_date",
}


def files() -> list[dict]:
    out = []
    for item in Affair.files() or []:
        name = str(item.get("filename", ""))
        if not NAME.fullmatch(name):
            continue
        digest = str(item.get("hash", "")).lower()
        size = int(item.get("filesize", 0))
        if not re.fullmatch(r"[0-9a-f]{32}", digest) or size <= 0:
            raise ValueError(f"invalid gpcw catalog entry: {name}")
        out.append({"filename": name, "hash": digest, "filesize": size, "quarter": NAME.fullmatch(name)[1]})
    return sorted(out, key=lambda x: x["quarter"])


def fetch(name: str, target_dir: Path, expected_hash: str) -> Path:
    if not NAME.fullmatch(name):
        raise ValueError(f"invalid gpcw filename: {name}")
    target_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target_dir) as temp:
        # Affair uses the TDX file protocol. The public HTTPS zip URL currently
        # serves a JavaScript bot challenge, whereas this endpoint returns bytes.
        Affair.fetch(temp, name)
        downloaded = Path(temp) / name
        if hashlib.md5(downloaded.read_bytes()).hexdigest() != expected_hash:  # noqa: S324
            raise ValueError(f"gpcw catalog hash mismatch: {name}")
        downloaded.replace(target_dir / name)
        return target_dir / name


def parse(path: Path) -> pd.DataFrame:
    match = NAME.fullmatch(path.name)
    if not match:
        raise ValueError(f"invalid gpcw filename: {path.name}")
    with zipfile.ZipFile(path) as archive:
        members = [n for n in archive.namelist() if n.endswith(".dat") and "/" not in n]
        if len(members) != 1:
            raise ValueError("gpcw archive must contain one dat file")
        data = archive.read(members[0])
    if len(data) < HEADER.size:
        raise ValueError("short gpcw header")
    _, report_date, count, _, report_size, _ = HEADER.unpack_from(data)
    if str(report_date) != match[1] or report_size % 4 or report_size <= 0:
        raise ValueError("invalid gpcw report date or record size")
    if HEADER.size + count * ITEM.size > len(data):
        raise ValueError("short gpcw stock index")
    values = []
    for i in range(count):
        code, _, offset = ITEM.unpack_from(data, HEADER.size + i * ITEM.size)
        if offset < HEADER.size + count * ITEM.size or offset + report_size > len(data):
            raise ValueError("invalid gpcw record offset")
        values.append((code.decode("ascii").rstrip("\x00"), report_date,
                       *struct.unpack_from(f"<{report_size // 4}f", data, offset)))
    names = ["code", "report_date"]
    seen = set(names)
    for pos in range(1, report_size // 4 + 1):
        name = columns[pos] if pos < len(columns) else f"col{pos}"
        if name in seen:
            name = f"{name}__col{pos}"
        names.append(name)
        seen.add(name)
    return pd.DataFrame(values, columns=names)
