import json
import os
import sqlite3
import uuid
from pathlib import Path

import pandas as pd
import pyarrow as pa

from alphasieve.audit import record_event
from alphasieve.config import Settings, load_config
from alphasieve.contracts import FactorSpec
from alphasieve.data.access import Panel
from alphasieve.factors.dsl import EvalContext, compile_expression, evaluate
from alphasieve.factors.registry import register
from alphasieve.search_space import load_search_space
from alphasieve.util import canonical_json, utcnow_iso

BASE_FEATURES = {
    "base_reversal_5d": "neg(ts_sum(ret_1d, 5))",
    "base_vol_20d": "neg(ts_std(ret_1d, 20))",
    "base_size": "neg(log(circ_mv))",
    "base_turnover_20d": "neg(ts_mean(turnover_rate, 20))",
}


def cache_path(settings: Settings, tier: str, candidate_hash: str, signature: str) -> Path:
    return settings.cache_dir / "factors" / tier / f"{candidate_hash}-{signature[:12]}.parquet"


_MEMO: dict[str, pd.DataFrame] = {}
_MEMO_LIMIT = 64


def cached_values(settings: Settings, panel: Panel, canonical: str, candidate_hash: str) -> pd.DataFrame:
    path = cache_path(settings, panel.tier, candidate_hash, panel.signature)
    if str(path) in _MEMO:
        return _MEMO[str(path)]
    frame = None
    if path.exists():
        try:
            frame = pd.read_parquet(path)
        except (OSError, ValueError, pa.ArrowException):
            path.unlink(missing_ok=True)
    if frame is not None:
        frame.index = pd.DatetimeIndex(frame.index)
        frame = frame.reindex(index=panel.dates, columns=panel.codes).astype(float)
        if len(_MEMO) >= _MEMO_LIMIT:
            _MEMO.pop(next(iter(_MEMO)))
        _MEMO[str(path)] = frame
        return frame
    compiled = compile_expression(canonical, load_search_space(settings))
    frame = evaluate(compiled, panel)
    store_values(settings, panel, candidate_hash, frame)
    return frame


def store_values(settings: Settings, panel: Panel, candidate_hash: str, frame: pd.DataFrame) -> Path:
    path = cache_path(settings, panel.tier, candidate_hash, panel.signature)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:6]}.tmp")
    frame.astype("float32").to_parquet(tmp)
    os.replace(tmp, path)
    return path


def library_members(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT l.candidate_hash, l.factor_id, l.version, l.name, l.metrics_json, f.canonical_expression, f.spec_json"
        " FROM library l JOIN factor_specs f ON f.factor_id = l.factor_id AND f.version = l.version"
        " ORDER BY l.added_at, l.factor_id"
    ).fetchall()
    out = []
    for r in rows:
        spec = json.loads(r["spec_json"])
        out.append({**dict(r), "direction": spec["direction"], "metrics": json.loads(r["metrics_json"] or "{}")})
    return out


def frames_from_members(settings: Settings, panel: Panel, members: list[dict]) -> dict[str, pd.DataFrame]:
    """Library frames from a member list (name, canonical, candidate_hash, direction); no database needed."""
    return {m["name"]: cached_values(settings, panel, m["canonical"], m["candidate_hash"]) * m["direction"]
            for m in members}


def library_frames(settings: Settings, conn: sqlite3.Connection, panel: Panel) -> dict[str, pd.DataFrame]:
    members = [{"name": m["name"] or m["factor_id"], "canonical": m["canonical_expression"],
                "candidate_hash": m["candidate_hash"], "direction": m["direction"]} for m in library_members(conn)]
    return frames_from_members(settings, panel, members)


def library_icir(conn: sqlite3.Connection) -> dict[str, float]:
    return {m["name"] or m["factor_id"]: m["metrics"].get("icir", float("nan")) for m in library_members(conn)}


def base_feature_frames(settings: Settings, panel: Panel) -> dict[str, pd.DataFrame]:
    space = load_search_space(settings)
    ctx = EvalContext(panel)
    return {name: evaluate(compile_expression(expr, space), panel, ctx) for name, expr in BASE_FEATURES.items()}


def baseline_frames(settings: Settings, conn: sqlite3.Connection, panel: Panel) -> dict[str, pd.DataFrame]:
    return library_frames(settings, conn, panel) or base_feature_frames(settings, panel)


def seed_library(settings: Settings, conn: sqlite3.Connection, panel: Panel) -> list[dict]:
    from alphasieve.evaluation.core import EvalInputs, l1_metrics, public_metrics

    seeds = load_config(settings, "seeds")["seeds"]
    space = load_search_space(settings)
    ctx = EvalContext(panel)
    inputs: dict[int, EvalInputs] = {}
    added = []
    for seed in seeds:
        spec = FactorSpec(**seed)
        compiled = compile_expression(spec.expression, space)
        factor_id, version, _ = register(conn, spec, compiled.canonical, compiled.candidate_hash, "system")
        conn.execute("UPDATE factor_specs SET state = 'seed' WHERE factor_id = ? AND version = ? AND state = 'draft'",
                     (factor_id, version))
        values = evaluate(compiled, panel, ctx)
        store_values(settings, panel, compiled.candidate_hash, values)
        inp = inputs.setdefault(spec.horizon, EvalInputs(panel, spec.horizon))
        metrics = public_metrics(l1_metrics(values * spec.direction, inp, {}))
        metrics.pop("library", None)
        conn.execute(
            "INSERT OR REPLACE INTO library (candidate_hash, factor_id, version, source, added_by, added_at, name,"
            " metrics_json) VALUES (?, ?, ?, 'seed', ?, ?, ?, ?)",
            (compiled.candidate_hash, factor_id, version, settings.role, utcnow_iso(), spec.name,
             canonical_json(metrics)),
        )
        added.append({"name": spec.name, "factor_id": factor_id, "version": version,
                      "ic_mean": metrics["ic_mean"], "icir": metrics["icir"], "coverage": metrics["coverage"]})
    record_event(conn, settings, "library.seeded", object_type="library", payload={"count": len(added),
                                                                                    "panel": panel.signature})
    return added
