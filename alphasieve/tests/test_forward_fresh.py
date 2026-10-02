import hashlib
import os

import pandas as pd
import pytest

from alphasieve.config import Settings
from alphasieve.data import fresh
from alphasieve.data.access import load_panel
from alphasieve.data.fresh import append_day, load_asof
from alphasieve.data.panel import build_panel
from alphasieve.errors import AlphaSieveError
from alphasieve.state import connect


def _system(settings):
    return Settings(settings.hot_root, settings.store_root, settings.store_mount,
                    settings.config_dir, "system", "test")


def _frames(day, close=10.0):
    observed = f"{day}T18:00:00+08:00"
    panel = pd.DataFrame({"date": [pd.Timestamp(day)], "code": ["sh.600000"],
                          "close": [close], "observed_at": [observed]})
    bench = pd.DataFrame({"date": [pd.Timestamp(day)], "csi500_close": [100 + close],
                          "observed_at": [observed]})
    return panel, bench


def test_fresh_25_days_immutable_and_access(settings):
    settings = _system(settings)
    conn = connect(settings.state_db)
    days = pd.bdate_range("2022-01-03", periods=25).strftime("%Y-%m-%d").tolist()
    first = None
    for day in days:
        panel, bench = _frames(day)
        record = append_day(settings, conn, day, panel=panel, benchmark=bench,
                            cutoff=f"{day}T18:30:00+08:00", raw_snapshot_hash="a" * 64,
                            calendar=days)
        assert record["date"] == day
        if first is None:
            first = settings.panel_dir("fresh") / "days" / day
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in first.iterdir()}
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in first.iterdir()} == before
    assert len(load_asof(settings, conn, days[-1]).long) == 25
    assert len(load_panel(settings, "fresh", role="system").long) == 25
    with pytest.raises(AlphaSieveError) as exc:
        load_panel(settings, "fresh", role="human")
    assert exc.value.code == "PERMISSION_DENIED"
    panel, bench = _frames(days[0])
    assert append_day(settings, conn, days[0], panel=panel, benchmark=bench,
                      cutoff=f"{days[0]}T18:30:00+08:00", raw_snapshot_hash="a" * 64,
                      calendar=days)["row_hash"]
    panel, bench = _frames(days[0], close=12.0)
    with pytest.raises(AlphaSieveError, match="IMMUTABLE_DAY_CONFLICT"):
        append_day(settings, conn, days[0], panel=panel, benchmark=bench,
                   cutoff=f"{days[0]}T18:30:00+08:00", raw_snapshot_hash="a" * 64,
                   calendar=days)
    conn.close()


def test_fresh_rejects_future_or_unsealed_inputs(settings):
    settings = _system(settings)
    conn = connect(settings.state_db)
    day = "2022-01-03"
    panel, bench = _frames(day)
    with pytest.raises(AlphaSieveError):
        append_day(settings, conn, day, cutoff=f"{day}T18:30:00+08:00", calendar=[day])
    panel["observed_at"] = f"{day}T19:00:00+08:00"
    with pytest.raises(AlphaSieveError):
        append_day(settings, conn, day, panel=panel, benchmark=bench,
                   cutoff=f"{day}T18:30:00+08:00", raw_snapshot_hash="a" * 64,
                   calendar=[day])
    panel, bench = _frames(day)
    panel["label_20d"] = 0.1
    with pytest.raises(AlphaSieveError):
        append_day(settings, conn, day, panel=panel, benchmark=bench,
                   cutoff=f"{day}T18:30:00+08:00", raw_snapshot_hash="a" * 64,
                   calendar=[day])
    with pytest.raises(ValueError, match="append-only"):
        build_panel(settings, conn, tiers=("fresh",))
    conn.close()


def test_fresh_recovers_same_input_after_file_commit(settings, monkeypatch):
    settings = _system(settings)
    conn = connect(settings.state_db)
    day = "2022-01-03"
    panel, bench = _frames(day)
    rename = os.rename

    def fail_after_rename(src, dst):
        rename(src, dst)
        raise RuntimeError("crash between file and sqlite commit")

    monkeypatch.setattr(fresh.os, "rename", fail_after_rename)
    with pytest.raises(RuntimeError):
        append_day(settings, conn, day, panel=panel, benchmark=bench,
                   cutoff=f"{day}T18:30:00+08:00", raw_snapshot_hash="a" * 64,
                   calendar=[day])
    monkeypatch.setattr(fresh.os, "rename", rename)
    assert conn.execute("SELECT COUNT(*) FROM fresh_days").fetchone()[0] == 0
    assert append_day(settings, conn, day, panel=panel, benchmark=bench,
                      cutoff=f"{day}T18:30:00+08:00", raw_snapshot_hash="a" * 64,
                      calendar=[day])["date"] == day
    conn.close()
