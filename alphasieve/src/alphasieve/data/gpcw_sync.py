"""Resumable sync for Tongdaxin quarterly financial packages."""

import hashlib
import sqlite3
from pathlib import Path

from alphasieve.config import Settings
from alphasieve.data.providers import gpcw
from alphasieve.data.sync import _write_parquet, record_snapshot


def _md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 -- vendor manifest uses MD5
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sync_gpcw(settings: Settings, conn: sqlite3.Connection, end: str, *, daily: bool = False,
              progress=None) -> dict:
    root = settings.raw_dir / "gpcw"
    zips = root / "zip"
    parsed = root / "parsed"
    updated, errors = [], []
    try:
        catalog = [r for r in gpcw.files() if r["quarter"] <= end.replace("-", "")]
    except Exception as exc:  # noqa: BLE001
        return {"catalog_files": 0, "updated": [], "errors": [{"item": "catalog", "error": str(exc)[:300]}]}
    if daily:
        existing = sorted(p.stem[4:] for p in zips.glob("gpcw????????.zip"))
        newest = existing[-1] if existing else None
        recent = {r["filename"] for r in catalog[-2:]}
        catalog = [r for r in catalog if r["filename"] in recent or (newest and r["quarter"] > newest)]
    for i, item in enumerate(catalog, 1):
        name = item["filename"]
        zip_path = zips / name
        parquet_path = parsed / f"{item['quarter']}.parquet"
        try:
            changed = not zip_path.exists() or _md5(zip_path) != item["hash"]
            if changed:
                gpcw.fetch(name, zips, item["hash"])
            if changed or not parquet_path.exists():
                frame = gpcw.parse(zip_path)
                _write_parquet(frame, parquet_path)
                snapshot = record_snapshot(conn, "gpcw:quarterly", {"quarter": item["quarter"],
                                           "vendor_md5": item["hash"]}, [zip_path, parquet_path], len(frame), "tdx")
                updated.append({"quarter": item["quarter"], "rows": len(frame), "snapshot": snapshot})
        except Exception as exc:  # noqa: BLE001
            errors.append({"item": name, "error": str(exc)[:300]})
        if progress:
            progress(i, len(catalog))
    return {"catalog_files": len(catalog), "updated": updated, "errors": errors}
