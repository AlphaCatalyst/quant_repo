"""Immutable, system-only fresh day partitions from presealed point-in-time inputs."""

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from alphasieve.config import Settings, load_config
from alphasieve.data.access import Panel, check_tier_access
from alphasieve.errors import AlphaSieveError, not_found, permission_denied, validation_error
from alphasieve.util import file_sha256


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _day_path(settings: Settings, universe: str, asof: str) -> Path:
    return settings.panel_dir("fresh", universe) / "days" / asof


def seal_raw_inputs(settings: Settings, asof: str, cutoff: str, sources: dict[str, Path]) -> tuple[dict, dict]:
    """Read only eligible raw rows, then freeze those rows before any panel conversion.

    Sources are staged PIT parquet tables with ``event_date`` and ``observed_at``.
    A legacy raw file lacking either field is refused rather than assigned a guessed time.
    """
    if settings.role != "system":
        raise permission_denied("only system may seal fresh raw inputs")
    import pyarrow as pa
    import pyarrow.dataset as ds

    cutoff_ts = pd.Timestamp(cutoff)
    if cutoff_ts.tzinfo is None:
        raise validation_error("fresh cutoff must include timezone")
    snapshot_root = settings.hot_root / "fresh_raw_snapshots"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    os.chmod(snapshot_root, 0o700)
    frames, evidence = {}, {}
    for kind, path in sorted(sources.items()):
        source = ds.dataset(path, format="parquet")
        schema = source.schema
        if not {"event_date", "observed_at"}.issubset(schema.names):
            raise validation_error(f"{kind} raw input lacks event_date/observed_at")
        if not (pa.types.is_string(schema.field("event_date").type)
                or pa.types.is_large_string(schema.field("event_date").type)):
            raise validation_error(f"{kind} event_date must be ISO YYYY-MM-DD text")
        cutoff_arrow = pa.scalar(cutoff_ts.to_pydatetime(), type=pa.timestamp("us", tz="UTC"))
        predicate = ((ds.field("event_date") <= asof)
                     & (ds.field("observed_at").cast(pa.timestamp("us", tz="UTC")) <= cutoff_arrow))
        frame = source.to_table(filter=predicate).to_pandas()
        if not frame.empty:
            observed = pd.to_datetime(frame["observed_at"], utc=True, errors="coerce")
            events = pd.to_datetime(frame["event_date"], errors="coerce")
            if observed.isna().any() or events.isna().any() or \
                    (observed > cutoff_ts.tz_convert("UTC")).any() or \
                    (events > pd.Timestamp(asof)).any():
                raise validation_error(f"{kind} raw input violates PIT cutoff")
            key = ["event_date"] + (["code"] if "code" in frame else [])
            same = key + ["observed_at"]
            conflicts = frame[frame.duplicated(same, keep=False)]
            if not conflicts.empty and conflicts.groupby(same, dropna=False).nunique(dropna=False).gt(1).any().any():
                raise validation_error(f"{kind} raw has conflicting duplicate submissions")
            frame = frame.sort_values(same, kind="stable").drop_duplicates(same)
            frame = frame.sort_values("observed_at", kind="stable").drop_duplicates(key, keep="last")
            frame = frame.sort_values(key, kind="stable").reset_index(drop=True)
        with tempfile.NamedTemporaryFile(prefix=".raw-", suffix=".parquet", dir=snapshot_root, delete=False) as tmp:
            staged = Path(tmp.name)
        try:
            frame.to_parquet(staged, index=False)
            digest = file_sha256(staged)
            target = snapshot_root / f"{digest}.parquet"
            if target.exists():
                if file_sha256(target) != digest:
                    raise AlphaSieveError("CONFLICT", "raw snapshot digest mismatch")
                staged.unlink()
            else:
                os.chmod(staged, 0o600)
                os.rename(staged, target)
        finally:
            staged.unlink(missing_ok=True)
        frames[kind] = frame
        evidence[kind] = {"snapshot": str(target), "sha256": digest, "rows": len(frame),
                          "source": str(path), "source_schema": str(schema),
                          "event_date_max": asof, "observed_at_cutoff": cutoff_ts.isoformat()}
    return frames, evidence


def append_from_raw(settings: Settings, conn: sqlite3.Connection, asof: str,
                    universe: str = "csi800") -> dict:
    root = settings.raw_dir / "forward"
    sources = {kind: root / f"{kind}.parquet" for kind in ("panel", "benchmark")}
    calendar_path = root / "calendar.json"
    if not calendar_path.is_file() or not all(path.is_file() for path in sources.values()):
        raise validation_error("staged PIT raw sources and locked trading calendar are required")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if now.date().isoformat() != asof:
        raise validation_error("fresh append cannot backfill an earlier trading day")
    cutoff = now.isoformat()
    frames, evidence = seal_raw_inputs(settings, asof, cutoff, sources)
    today = {}
    for kind, frame in frames.items():
        today[kind] = frame.loc[frame["event_date"] == asof].rename(columns={"event_date": "date"}).copy()
        today[kind]["date"] = pd.to_datetime(today[kind]["date"])
    raw_hash = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return append_day(settings, conn, asof, universe, today["panel"], today["benchmark"], cutoff,
                      raw_snapshot_hash=raw_hash, calendar=json.loads(calendar_path.read_text()),
                      raw_evidence=evidence)


def _validate_frame(frame: pd.DataFrame, asof: str, cutoff: pd.Timestamp, kind: str) -> None:
    required = {"date", "observed_at"} | ({"code"} if kind == "panel" else set())
    if frame.empty or not required.issubset(frame.columns):
        raise validation_error(f"fresh {kind} requires nonempty sealed date and observed_at rows")
    dates = pd.to_datetime(frame["date"], errors="coerce")
    observed = pd.to_datetime(frame["observed_at"], errors="coerce", utc=True)
    if dates.isna().any() or observed.isna().any() or not dates.eq(pd.Timestamp(asof)).all():
        raise validation_error(f"fresh {kind} has missing or out-of-day rows")
    if (observed > cutoff.tz_convert("UTC")).any():
        raise validation_error(f"fresh {kind} includes rows observed after cutoff")
    if any(str(c).startswith("label_") for c in frame.columns):
        raise validation_error("unmatured labels may not be stored in a fresh day")
    if kind == "panel" and frame.duplicated(["date", "code"]).any():
        raise validation_error("duplicate fresh stock/date rows")


def append_day(settings: Settings, conn: sqlite3.Connection, asof: str, universe: str = "csi800",
               panel: pd.DataFrame | None = None, benchmark: pd.DataFrame | None = None,
               cutoff: str | None = None, *, raw_snapshot_hash: str | None = None,
               calendar: list[str] | None = None, raw_evidence: dict | None = None) -> dict:
    """Commit a day from a sealed PIT snapshot; live raw conversion is intentionally unavailable.

    Both frames must have already been constructed from inputs filtered *before reading*
    by event date and observation cutoff. The snapshot digest records that input identity.
    """
    if settings.role != "system":
        raise permission_denied("only system may append fresh days")
    if panel is None or benchmark is None or not raw_snapshot_hash:
        raise validation_error("sealed PIT panel, benchmark, and raw snapshot digest are required")
    if not re.fullmatch(r"[0-9a-f]{64}", raw_snapshot_hash):
        raise validation_error("raw snapshot digest must be SHA-256")
    try:
        day = pd.Timestamp(asof)
        cutoff_ts = pd.Timestamp(cutoff)
    except (ValueError, TypeError):
        raise validation_error("valid asof and cutoff are required") from None
    if day.tzinfo is not None or day.strftime("%Y-%m-%d") != asof or cutoff_ts.tzinfo is None:
        raise validation_error("asof must be YYYY-MM-DD and cutoff must include timezone")
    splits = load_config(settings, "splits")
    if asof < splits["fresh"]["start"] or cutoff_ts.tz_convert("Asia/Shanghai").date() != day.date():
        raise validation_error("fresh date is before start or cutoff is not on asof")
    if calendar is None or asof not in calendar:
        raise validation_error("fresh date must be in the locked trading calendar")
    _validate_frame(panel, asof, cutoff_ts, "panel")
    _validate_frame(benchmark, asof, cutoff_ts, "benchmark")

    root = settings.panel_dir("fresh", universe)
    days = root / "days"
    days.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    os.chmod(days, 0o700)
    with tempfile.TemporaryDirectory(prefix=".fresh-", dir=days) as tmp_name:
        tmp = Path(tmp_name)
        panel.to_parquet(tmp / "panel.parquet", index=False)
        benchmark.to_parquet(tmp / "benchmark.parquet", index=False)
        panel_hash = file_sha256(tmp / "panel.parquet")
        bench_hash = file_sha256(tmp / "benchmark.parquet")
        split_hash = _digest(splits)
        input_hash = _digest({"raw_snapshot_hash": raw_snapshot_hash, "panel": panel_hash,
                              "benchmark": bench_hash, "cutoff": cutoff_ts.isoformat(),
                              "splits": split_hash, "universe": universe, "date": asof})
        manifest = {"tier": "fresh", "universe": universe, "date": asof,
                    "cutoff": cutoff_ts.isoformat(), "raw_snapshot_hash": raw_snapshot_hash,
                    "raw_evidence": raw_evidence or {},
                    "splits_hash": split_hash, "input_hash": input_hash,
                    "partition_hash": panel_hash, "benchmark_hash": bench_hash,
                    "rows": len(panel), "codes": int(panel["code"].nunique()),
                    "warnings": ([] if raw_evidence else [
                        "input was supplied as a sealed PIT snapshot; caller must prove upstream cutoff filtering"])}
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute("SELECT * FROM fresh_days WHERE universe=? AND date=?", (universe, asof)).fetchone()
            if existing is not None:
                if existing["input_hash"] != input_hash:
                    raise AlphaSieveError("CONFLICT", "IMMUTABLE_DAY_CONFLICT", {"date": asof, "universe": universe})
                _verify_day(settings, dict(existing))
                conn.execute("COMMIT")
                return dict(existing)
            prior = conn.execute("SELECT date,row_hash FROM fresh_days WHERE universe=? ORDER BY date DESC LIMIT 1",
                                 (universe,)).fetchone()
            if prior is not None and asof <= prior["date"]:
                raise AlphaSieveError("CONFLICT", "IMMUTABLE_DAY_CONFLICT: historical backfill forbidden",
                                      {"date": asof, "latest": prior["date"]})
            prev_hash = prior["row_hash"] if prior else "0" * 64
            manifest["prev_hash"] = prev_hash
            row_hash = _digest(manifest)
            manifest["row_hash"] = row_hash
            (tmp / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            for path in tmp.iterdir():
                os.chmod(path, 0o600)
            target = _day_path(settings, universe, asof)
            if target.exists():
                try:
                    orphan = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
                    valid_orphan = (orphan == manifest
                                    and file_sha256(target / "panel.parquet") == panel_hash
                                    and file_sha256(target / "benchmark.parquet") == bench_hash)
                except (OSError, ValueError):
                    valid_orphan = False
                if not valid_orphan:
                    raise AlphaSieveError("CONFLICT", "orphan fresh partition requires manual audit", {"date": asof})
            else:
                os.rename(tmp, target)
            conn.execute("INSERT INTO fresh_days(universe,date,cutoff,input_hash,partition_hash,benchmark_hash,"
                         "prev_hash,row_hash,manifest_json) VALUES (?,?,?,?,?,?,?,?,?)",
                         (universe, asof, cutoff_ts.isoformat(), input_hash, panel_hash, bench_hash,
                          prev_hash, row_hash, json.dumps(manifest, sort_keys=True)))
            from alphasieve.fresh.service import append_ledger

            append_ledger(conn, "fresh_day", "system", {"universe": universe, "row_hash": row_hash}, date=asof)
            conn.execute("COMMIT")
            return manifest
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _verify_day(settings: Settings, row: dict) -> None:
    path = _day_path(settings, row["universe"], row["date"])
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        intact = (manifest == json.loads(row["manifest_json"])
                  and file_sha256(path / "panel.parquet") == row["partition_hash"]
                  and file_sha256(path / "benchmark.parquet") == row["benchmark_hash"])
    except (OSError, ValueError):
        intact = False
    if not intact:
        raise AlphaSieveError("CONFLICT", "fresh partition digest mismatch", {"date": row["date"]})


def load_asof(settings: Settings, conn: sqlite3.Connection, asof: str,
              universe: str = "csi800", role: str | None = None) -> Panel:
    check_tier_access(role or settings.role, "fresh")
    rows = conn.execute("SELECT * FROM fresh_days WHERE universe=? AND date<=? ORDER BY date",
                        (universe, asof)).fetchall()
    if not rows:
        raise not_found("no committed fresh day", universe=universe, asof=asof)
    for row in rows:
        _verify_day(settings, dict(row))
    long = pd.concat([pd.read_parquet(_day_path(settings, universe, r["date"]) / "panel.parquet")
                      for r in rows], ignore_index=True)
    bench = pd.concat([pd.read_parquet(_day_path(settings, universe, r["date"]) / "benchmark.parquet")
                       for r in rows], ignore_index=True)
    meta = {"tier": "fresh", "universe": universe, "signature": rows[-1]["row_hash"],
            "window": {"start": rows[0]["date"], "end": rows[-1]["date"]},
            "partition_hashes": [r["partition_hash"] for r in rows]}
    return Panel(long, meta, bench)
