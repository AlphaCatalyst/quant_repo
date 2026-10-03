import hashlib
import io
import struct
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from alphasieve.data.gpcw_sync import sync_gpcw
from alphasieve.data.providers import gpcw
from alphasieve.state import connect


def _package(day: str, eps: float = 0.43) -> bytes:
    values = [0.0] * 315
    values[0] = eps
    values[5] = 3.245
    values[313] = 160421.0
    header = struct.pack("<hIHIII", 1, int(day), 1, 0, len(values) * 4, 0)
    offset = len(header) + gpcw.ITEM.size
    data = header + struct.pack("<6scI", b"000001", b"\0", offset) + struct.pack(f"<{len(values)}f", *values)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(f"gpcw{day}.dat", data)
    return output.getvalue()


def test_parse_preserves_chinese_fields_and_dates(tmp_path):
    path = tmp_path / "gpcw20160331.zip"
    path.write_bytes(_package("20160331"))
    frame = gpcw.parse(path)
    assert frame.loc[0, "code"] == "000001"
    assert abs(frame.loc[0, "基本每股收益"] - 0.43) < 1e-6
    assert frame.loc[0, "财报公告日期"] == 160421.0
    assert "业绩预告公告日期 " in frame.columns
    assert len(frame.columns) == len(set(frame.columns))


def test_daily_sync_only_checks_latest_quarters_and_changed_hash(settings, monkeypatch):
    conn = connect(settings.state_db)
    packages = {day: _package(day) for day in ("20160331", "20160630", "20160930")}
    hashes = {day: hashlib.md5(data).hexdigest() for day, data in packages.items()}
    calls = []

    def catalog():
        return [{"filename": f"gpcw{day}.zip", "hash": hashes[day], "filesize": len(packages[day]),
                 "quarter": day} for day in packages]

    def fetch(name, target, expected_hash):
        calls.append(name)
        target.mkdir(parents=True, exist_ok=True)
        path = target / name
        path.write_bytes(packages[name[4:12]])
        return path

    monkeypatch.setattr(gpcw, "files", catalog)
    monkeypatch.setattr(gpcw, "fetch", fetch)
    first = sync_gpcw(settings, conn, "2016-12-31", daily=True)
    assert len(first["updated"]) == 2 and not first["errors"]
    assert calls == ["gpcw20160630.zip", "gpcw20160930.zip"]
    assert not sync_gpcw(settings, conn, "2016-12-31", daily=True)["updated"]
    packages["20160930"] = _package("20160930", 0.51)
    hashes["20160930"] = hashlib.md5(packages["20160930"]).hexdigest()
    changed = sync_gpcw(settings, conn, "2016-12-31", daily=True)
    assert [x["quarter"] for x in changed["updated"]] == ["20160930"]
    assert abs(pd.read_parquet(settings.raw_dir / "gpcw" / "parsed" / "20160930.parquet")
               .loc[0, "基本每股收益"] - 0.51) < 1e-6
    assert conn.execute("SELECT count(*) FROM data_snapshots WHERE dataset='gpcw:quarterly'").fetchone()[0] == 3


def test_hash_mismatch_does_not_replace_existing_archive(tmp_path, monkeypatch):
    root = tmp_path / "zip"
    root.mkdir()
    old = root / "gpcw20160331.zip"
    old.write_bytes(_package("20160331"))

    monkeypatch.setattr(gpcw.Affair, "fetch", lambda dirname, name: (Path(dirname) / name).write_bytes(b"bad"))
    original = old.read_bytes()
    with pytest.raises(ValueError, match="hash mismatch"):
        gpcw.fetch(old.name, root, "0" * 32)
    assert old.read_bytes() == original
