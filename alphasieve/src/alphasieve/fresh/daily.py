"""System-only coordinator for a sealed forward observation day."""
import json
import os
import pickle
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from alphasieve.config import Settings
from alphasieve.contracts.forward import ForwardConfig
from alphasieve.data.access import Panel
from alphasieve.data.fresh import _verify_day, append_day, load_asof, seal_raw_inputs
from alphasieve.errors import AlphaSieveError, permission_denied, validation_error
from alphasieve.fresh.paper import record_target, settle_day
from alphasieve.fresh.service import active_cohorts, append_ledger
from alphasieve.fresh.statistics import hac, verdict
from alphasieve.strategy.portfolio import build_weights
from alphasieve.training.forward import fit_asof, score_asof
from alphasieve.training.run import _frames
from alphasieve.training.samples import benchmark_returns, build_cross_sectional, rolling_beta
from alphasieve.training.task import parse_task
from alphasieve.util import canonical_json, file_sha256, sha256_hex, utcnow_iso


def _run(conn, cid, asof, status, details, model_id=None, input_hash=None):
    rid = f"FRUN-{uuid.uuid4().hex[:12]}"
    conn.execute("INSERT INTO forward_runs VALUES (?,?,?,?,?,?,?,?,?)",
                 (rid, cid, asof, "daily", status, input_hash, model_id, canonical_json(details), utcnow_iso()))
    append_ledger(conn, status, "system", details, cid, asof, run_id=rid)
    return {"cohort_id": cid, "date": asof, "status": status, "run_id": rid, **details}


def _source_frames(settings, asof, cutoff, sources):
    if sources is None:
        root = settings.raw_dir / "forward"
        sources = {k: root / f"{k}.parquet" for k in ("panel", "benchmark")}
    if set(sources) != {"panel", "benchmark"} or not all(Path(p).is_file() for p in sources.values()):
        raise validation_error("PIT forward panel and benchmark raw sources are required")
    frames, evidence = seal_raw_inputs(settings, asof, cutoff, {k: Path(v) for k, v in sources.items()})
    for kind, frame in frames.items():
        if frame.empty or "event_date" not in frame:
            raise validation_error(f"{kind} has no PIT rows")
        frames[kind] = frame.rename(columns={"event_date": "date"}).assign(
            date=lambda x: pd.to_datetime(x["date"]))
    return frames, evidence


def _panel(settings, conn, asof, raw):
    fresh = load_asof(settings, conn, asof)
    fresh_start = pd.Timestamp(fresh.meta["window"]["start"])
    history = raw["panel"][raw["panel"]["date"] < fresh_start]
    bench_history = raw["benchmark"][raw["benchmark"]["date"] < fresh_start]
    long = pd.concat([history, fresh.long], ignore_index=True)
    bench = pd.concat([bench_history, fresh.benchmark], ignore_index=True)
    if long.duplicated(["date", "code"]).any() or bench.duplicated(["date"]).any():
        raise validation_error("PIT context has duplicate dates or codes")
    return Panel(long, {**fresh.meta, "window": {"start": str(fresh_start.date()), "end": asof}}, bench), fresh


def _prior_model(settings, conn, cid):
    row = conn.execute("SELECT * FROM model_snapshots WHERE cohort_id=? ORDER BY date DESC LIMIT 1", (cid,)).fetchone()
    if row is None:
        return None
    info = json.loads(row["manifest_json"])
    path = settings.hot_root / "fresh_models" / info["file"]
    if path.name != info["file"] or file_sha256(path) != info["sha256"]:
        raise AlphaSieveError("CONFLICT", "forward model snapshot digest mismatch")
    with path.open("rb") as handle:
        return pickle.load(handle)


def _save_model(settings, conn, cid, asof, snapshot):
    root = settings.hot_root / "fresh_models"
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    payload = pickle.dumps(snapshot, protocol=4)
    digest = sha256_hex(payload)
    path = root / f"{digest}.pkl"
    if not path.exists():
        path.write_bytes(payload)
        os.chmod(path, 0o600)
    if file_sha256(path) != digest:
        raise AlphaSieveError("CONFLICT", "forward model file conflict")
    model_id = f"FM-{sha256_hex(cid + asof + digest)[:16]}"
    manifest = {"file": path.name, "sha256": digest, "model_digest": snapshot["model_digest"],
                "max_train_date": snapshot["max_train_date"], "max_label_end": snapshot["max_label_end"],
                "cutoff": asof}
    conn.execute("INSERT INTO model_snapshots VALUES (?,?,?,?,?)",
                 (model_id, cid, asof, snapshot["model_digest"], canonical_json(manifest)))
    append_ledger(conn, "model_refit", "system", manifest, cid, asof)
    return model_id


def _score_and_target(settings, conn, cohort, asof, panel, fresh, sealed_at):
    cid = cohort["cohort_id"]
    config = ForwardConfig.model_validate_json(cohort["config_json"])
    task = parse_task(config.bundle["task"])
    if config.bundle.get("features", {}).get("score_source") or task.score_source is not None:
        raise validation_error("score_source cannot enter forward")
    if config.train_window == "rolling":
        context_start = pd.Timestamp(asof) - pd.DateOffset(years=config.train_years + 2)
        prior_dates = panel.dates[panel.dates < pd.Timestamp(asof)]
        if len(prior_dates) < int((config.train_years + 2) * 200) or \
                not len(prior_dates) or prior_dates[0] > context_start:
            raise validation_error("rolling forward requires five training years plus two years of warmup")
    frames = _frames(settings, panel, config.bundle)
    benchmark = benchmark_returns(panel, config.benchmark)
    beta = rolling_beta(panel, benchmark) if benchmark is not None else None
    table = build_cross_sectional(panel, frames, task, beta, stride=task.sample.train_stride,
                                  train_before_window=True)
    if table.feature_names != config.feature_names:
        raise validation_error("forward feature order/coverage differs from locked source")
    previous = _prior_model(settings, conn, cid)
    model = fit_asof(table, panel.dates, asof, task, config.chosen, config.feature_names,
                     bundle=config.bundle, previous=previous, threads=8)
    if previous is None or model is not previous:
        model_id = _save_model(settings, conn, cid, asof, model)
    else:
        row = conn.execute("SELECT model_id FROM model_snapshots WHERE cohort_id=? ORDER BY date DESC LIMIT 1",
                           (cid,)).fetchone()
        model_id = row["model_id"]
    scores = score_asof(model, table, asof, bundle=config.bundle, task=task, dates=panel.dates)
    loc = panel.dates.get_loc(pd.Timestamp(asof))
    daily = {panel.codes[int(table.code_pos[i])]: float(scores[i]) for i in np.flatnonzero(
        (table.date_pos == loc) & np.isfinite(scores))}
    if not daily:
        raise validation_error("no valid forward scores for current day")
    prior_scores = {}
    for row in conn.execute("SELECT date,details_json FROM forward_runs WHERE cohort_id=? AND kind='daily'"
                            " AND status='complete' ORDER BY date", (cid,)):
        details = json.loads(row["details_json"])
        prior_scores[row["date"]] = details["scores"]
    prior_scores[asof] = daily
    if (len(prior_scores) - 1) % task.portfolio.rebalance_every:
        return model_id, daily, None
    matrix = pd.DataFrame.from_dict(prior_scores, orient="index").reindex(
        index=list(prior_scores), columns=fresh.codes).ffill()
    matrix.index = pd.to_datetime(matrix.index)
    weights = build_weights(matrix, fresh, task.portfolio.rebalance_every,
                            task.portfolio.industry_dev, task.portfolio.name_cap,
                            task.portfolio.turnover_cap, task.portfolio.size_limit)
    if pd.Timestamp(asof) not in weights.index:
        raise validation_error("locked portfolio produced no target")
    goal = {k: float(v) for k, v in weights.loc[pd.Timestamp(asof)].items() if v > 0}
    digest = record_target(conn, f"B-{cid}", asof, goal, sealed_at=sealed_at)
    return model_id, daily, digest


def _observe_and_verdict(conn, cohort, asof):
    cid = cohort["cohort_id"]
    book_id = f"B-{cid}"
    rows = conn.execute("SELECT date,ret,benchmark_ret,status,row_hash FROM paper_days WHERE book_id=? ORDER BY date",
                        (book_id,)).fetchall()
    if rows:
        last = rows[-1]
        if last["ret"] is not None and last["benchmark_ret"] is not None:
            signal = conn.execute("SELECT date,row_hash FROM fresh_days WHERE date<? ORDER BY date DESC LIMIT 1",
                                  (last["date"],)).fetchone()
            target = conn.execute("SELECT digest FROM paper_targets WHERE book_id=? AND date=?",
                                  (book_id, signal["date"])).fetchone() if signal else None
            evidence = {"signal_day_hash": signal["row_hash"] if signal else None,
                        "maturity_day_hash": conn.execute("SELECT row_hash FROM fresh_days WHERE date=?",
                                                          (last["date"],)).fetchone()[0],
                        "paper_row_hash": last["row_hash"],
                        "target_hash": target["digest"] if target else None}
            conn.execute("INSERT OR IGNORE INTO fresh_observations VALUES (?,?,?,?,?,?,?,?)",
                         (cid, cohort["trial_id"] or cid, signal["date"], 1, asof,
                          last["ret"] - last["benchmark_ret"], None,
                          canonical_json(evidence)))
    if conn.execute("SELECT 1 FROM fresh_verdicts WHERE cohort_id=?", (cid,)).fetchone():
        return None
    pol = json.loads(cohort["policy_json"])
    required = pol["strategy_a"]["valid_return_days_min"]
    valid = [r for r in rows if r["ret"] is not None and r["benchmark_ret"] is not None]
    if len(valid) < required:
        return None
    series = [(i, r["ret"] - r["benchmark_ret"] if r["ret"] is not None and
               r["benchmark_ret"] is not None else None) for i, r in enumerate(rows)]
    task = parse_task(json.loads(cohort["config_json"])["bundle"]["task"])
    span = task.portfolio.rebalance_every
    excess = np.array([value for _, value in series if value is not None])
    curve = np.cumprod(1 + excess)
    drawdown = float((curve / np.maximum.accumulate(np.r_[1.0, curve])[1:] - 1).min())
    te = float(excess.std(ddof=1) * np.sqrt(252))
    pvalue = hac(series, span)["p_one_sided"]
    result = verdict(kind="strategy", mode=cohort["mode"], series=series, horizon=span,
                     eligible_days=len(rows), required_days=required,
                     coverage_min=pol["strategy_a"]["coverage_min"],
                     bh_pass=pvalue is not None and pvalue <= pol["statistics"]["bh_q_max"],
                     excess_drawdown=drawdown, tracking_error=te,
                     drawdown_floor=pol["strategy_a"]["excess_drawdown_floor"],
                     te_min=pol["strategy_a"]["tracking_error_annual_min"],
                     te_max=pol["strategy_a"]["tracking_error_annual_max"],
                     endpoint_reached=True, p_threshold=pol["statistics"]["hac_one_sided_p_max"])
    state = result["verdict"] or result["status"]
    conn.execute("INSERT INTO fresh_verdicts VALUES (?,?,?,?,?)",
                 (cid, state, canonical_json(result), cohort["policy_hash"], utcnow_iso()))
    append_ledger(conn, "verdict", "system", result, cid, asof)
    return state


def run_day(settings: Settings, conn: sqlite3.Connection, cohort: dict, asof: str, *,
            cutoff: str | None = None, calendar: list[str] | None = None,
            sources: dict[str, Path] | None = None, sealed_at: str | None = None) -> dict:
    """Run one approved cohort; every committed stage is idempotent on retry."""
    if settings.role != "system":
        raise permission_denied("forward daily is system-only")
    cid = cohort["cohort_id"]
    config = ForwardConfig.model_validate_json(cohort["config_json"])
    if config.bundle.get("features", {}).get("score_source") or \
            parse_task(config.bundle["task"]).score_source is not None:
        return _run(conn, cid, asof, "blocked", {"reason": "score_source cannot enter forward"})
    if cohort["object_kind"] != "strategy":
        return _run(conn, cid, asof, "blocked", {"reason": "factor forward scorer is not configured"})
    if cid not in {r["cohort_id"] for r in active_cohorts(conn, asof)}:
        return _run(conn, cid, asof, "blocked", {"reason": "cohort is not approved or active"})
    prior = conn.execute("SELECT * FROM forward_runs WHERE cohort_id=? AND date=? AND kind='daily'"
                         " AND status='complete' ORDER BY created_at DESC LIMIT 1", (cid, asof)).fetchone()
    if prior:
        day = conn.execute("SELECT * FROM fresh_days WHERE universe='csi800' AND date=?", (asof,)).fetchone()
        details = json.loads(prior["details_json"])
        if day is None or day["row_hash"] != details["fresh_day"]:
            return _run(conn, cid, asof, "blocked", {"reason": "completed fresh day reference changed"})
        try:
            _verify_day(settings, dict(day))
        except (AlphaSieveError, OSError) as exc:
            return _run(conn, cid, asof, "blocked", {"reason": str(exc)})
        return {"cohort_id": cid, "date": asof, "status": "complete", "run_id": prior["run_id"], **details}
    if cutoff is None:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        if now.date().isoformat() != asof:
            return _run(conn, cid, asof, "blocked", {"reason": "historical forward backfill is forbidden"})
        cutoff = now.isoformat()
    if calendar is None:
        path = settings.raw_dir / "forward" / "calendar.json"
        calendar = json.loads(path.read_text()) if path.is_file() else []
    try:
        existing = conn.execute("SELECT * FROM fresh_days WHERE universe='csi800' AND date=?", (asof,)).fetchone()
        if existing:
            fresh_record = dict(existing)
            _verify_day(settings, fresh_record)
            manifest = json.loads(existing["manifest_json"])
            raw_hash = manifest["raw_snapshot_hash"]
            raw = {}
            for kind, item in manifest["raw_evidence"].items():
                path = Path(item["snapshot"])
                if path.parent != settings.hot_root / "fresh_raw_snapshots" or file_sha256(path) != item["sha256"]:
                    raise AlphaSieveError("CONFLICT", "sealed raw snapshot is missing or changed")
                raw[kind] = pd.read_parquet(path).rename(columns={"event_date": "date"})
                raw[kind]["date"] = pd.to_datetime(raw[kind]["date"])
            if set(raw) != {"panel", "benchmark"}:
                raise validation_error("sealed raw evidence is incomplete")
        else:
            raw, evidence = _source_frames(settings, asof, cutoff, sources)
            day_panel = raw["panel"][raw["panel"]["date"] == pd.Timestamp(asof)].copy()
            day_bench = raw["benchmark"][raw["benchmark"]["date"] == pd.Timestamp(asof)].copy()
            raw_hash = sha256_hex(canonical_json(evidence))
            fresh_record = append_day(settings, conn, asof, panel=day_panel, benchmark=day_bench,
                                      cutoff=cutoff, raw_snapshot_hash=raw_hash, calendar=calendar,
                                      raw_evidence=evidence)
        panel, fresh = _panel(settings, conn, asof, raw)
        book = f"B-{cid}"
        has_prior_day = conn.execute("SELECT 1 FROM fresh_days WHERE universe=? AND date<? LIMIT 1",
                                     ("csi800", asof)).fetchone()
        paper = settle_day(conn, book, asof, fresh,
                           manifest={"fresh_day": fresh_record["row_hash"], "raw_snapshot": raw_hash}) \
            if has_prior_day else None
        if paper is not None and paper["status"] == "data_gap":
            _observe_and_verdict(conn, cohort, asof)
            last_two = conn.execute("SELECT status FROM paper_days WHERE book_id=? ORDER BY date DESC LIMIT 2",
                                    (book,)).fetchall()
            if len(last_two) == 2 and all(r["status"] == "data_gap" for r in last_two):
                append_ledger(conn, "paused", "system", {"reason": "two consecutive data gaps"}, cid, asof)
            return _run(conn, cid, asof, "complete",
                        {"fresh_day": fresh_record["row_hash"], "paper": paper["row_hash"],
                         "scores": {}, "target": None, "data_gap": True}, input_hash=raw_hash)
        model_id, scores, target = _score_and_target(settings, conn, cohort, asof, panel, fresh, sealed_at)
        adjudication = _observe_and_verdict(conn, cohort, asof)
        return _run(conn, cid, asof, "complete",
                    {"fresh_day": fresh_record["row_hash"], "paper": paper["row_hash"] if paper else None,
                     "scores": scores, "target": target, "verdict": adjudication},
                    model_id=model_id, input_hash=raw_hash)
    except (AlphaSieveError, ValueError, KeyError, OSError) as exc:
        return _run(conn, cid, asof, "blocked", {"reason": str(exc)})
