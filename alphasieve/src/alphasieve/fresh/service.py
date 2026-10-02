"""Human admission and append-only forward event projection."""
import json
import sqlite3
import uuid

import yaml

from alphasieve.config import Settings
from alphasieve.contracts.forward import ForwardConfig
from alphasieve.errors import AlphaSieveError, permission_denied, validation_error
from alphasieve.util import canonical_json, file_sha256, sha256_hex, utcnow_iso


def policy(settings: Settings) -> tuple[dict, str]:
    path = settings.config_dir / "forward" / "policy_v1.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data, file_sha256(path)


def append_ledger(conn: sqlite3.Connection, kind: str, actor: str, payload: dict,
                  cohort_id: str | None = None, date: str | None = None,
                  book_id: str | None = None, run_id: str | None = None) -> str:
    prior = conn.execute("SELECT row_hash FROM forward_ledger ORDER BY seq DESC LIMIT 1").fetchone()
    prev = prior[0] if prior else "0" * 64
    row = {"kind": kind, "actor": actor, "payload": payload, "cohort_id": cohort_id,
           "date": date, "book_id": book_id, "run_id": run_id, "prev": prev}
    digest = sha256_hex(canonical_json(row))
    conn.execute("INSERT INTO forward_ledger (record_kind,cohort_id,run_id,book_id,date,actor,payload_json,"
                 "created_at,prev_hash,row_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (kind, cohort_id, run_id, book_id, date, actor, canonical_json(payload), utcnow_iso(), prev, digest))
    return digest


def _require_human(settings: Settings) -> None:
    if settings.role != "human":
        raise permission_denied("forward approval is human-only")


def config_from_trial(conn: sqlite3.Connection, settings: Settings, trial_id: str,
                      mode: str, start_date: str) -> ForwardConfig:
    _require_human(settings)
    row = conn.execute("SELECT * FROM trials WHERE trial_id=? AND layer='strategy' AND record_kind='completed'"
                       " AND evidence_tier='dev'", (trial_id,)).fetchone()
    if row is None:
        raise validation_error("completed dev strategy trial required")
    path = settings.artifacts_dir / row["artifact_id"] / "metrics.json"
    metrics = json.loads(path.read_text(encoding="utf-8"))
    bundle = metrics.get("bundle")
    if not bundle or bundle.get("trial_id") != trial_id:
        raise validation_error("source bundle is missing or mismatched")
    from alphasieve.training.task import parse_task

    if parse_task(bundle["task"]).config_hash != bundle.get("config_hash"):
        raise validation_error("source task config hash differs from the locked bundle")
    if bundle["features"].get("score_source") or bundle["task"].get("score_source"):
        raise validation_error("score_source needs explicit source-training provenance mapping")
    task = bundle["task"]
    horizons = task["label"]["horizons"]
    selection = metrics.get("model", {}).get("selection", {})
    chosen = {}
    for h in horizons:
        rows = [(int(k.split("/")[0]), v["chosen"]) for k, v in selection.items()
                if k.endswith(f"/h{h}") and "chosen" in v]
        if not rows:
            raise validation_error(f"source lacks a selected model for h{h}")
        chosen[str(h)] = max(rows)[1]
    names = metrics.get("features", {}).get("names")
    if not names:
        raise validation_error("source lacks locked feature names")
    return ForwardConfig(object_kind="strategy", mode=mode, trial_id=trial_id,
                         source_hash=file_sha256(path), bundle=bundle, feature_names=names, chosen=chosen,
                         seeds=task["search"]["seeds"], horizons=horizons, primary_horizon=horizons[0],
                         train_window=task["split"]["window"], train_years=task["split"]["train_years"],
                         purge_days=task["sample"]["purge_days"], retrain=task["split"]["retrain"],
                         benchmark=task["portfolio"].get("benchmark") or "zz500", capital=task["portfolio"]["aum"],
                         start_date=start_date)


def approve_cohort(conn: sqlite3.Connection, settings: Settings, config: ForwardConfig, reason: str,
                   *, approve_policy: bool, approve_operational_refit: bool,
                   eligibility_hash: str | None = None, parent_id: str | None = None) -> dict:
    """Single human-only approval seam; SSH signature verification can wrap this function."""
    _require_human(settings)
    if not reason.strip() or not approve_policy or not approve_operational_refit:
        raise validation_error("human must explicitly approve policy and operational_refit with a reason")
    if config.start_date <= utcnow_iso()[:10]:
        raise validation_error("forward observation starts on a future trading date; backfill is forbidden")
    if config.mode == "validation" and not eligibility_hash:
        raise validation_error("validation requires dev/holdout/review evidence hash")
    if config.object_kind == "strategy" and config.mode == "validation":
        req = conn.execute("SELECT 1 FROM strategy_holdout_requests WHERE trial_id=? AND status='approved'"
                           " AND result_json IS NOT NULL", (config.trial_id,)).fetchone()
        if not req:
            raise validation_error("approved strategy holdout evidence required")
    if config.bundle.get("features", {}).get("score_source"):
        raise validation_error("frozen dev score_source cannot enter forward")
    if set(config.chosen) != {str(h) for h in config.horizons}:
        raise validation_error("selected model missing for a horizon")
    pol, pol_hash = policy(settings)
    approval_id = f"D-{uuid.uuid4().hex[:10]}"
    cohort_id = f"F-{uuid.uuid4().hex[:12]}"
    now = utcnow_iso()
    payload = {"reason": reason, "policy_hash": pol_hash, "operational_refit": True,
               "eligibility_hash": eligibility_hash, "config_hash": config.digest}
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("INSERT INTO decisions (decision_id,object_type,object_id,decision,reason,decided_by,"
                     "decided_at,evidence_hash) VALUES (?,?,?,?,?,?,?,?)",
                     (approval_id, "fresh_cohort", cohort_id, "approved", reason, settings.user, now,
                      sha256_hex(canonical_json(payload))))
        conn.execute("INSERT INTO fresh_cohorts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (cohort_id, config.object_kind, config.mode, config.trial_id, config.source_hash,
                      canonical_json(config.model_dump(mode="json")), config.digest, canonical_json(pol), pol_hash,
                      config.start_date, approval_id, settings.user, now, parent_id))
        conn.execute("INSERT INTO paper_books VALUES (?,?,?,?,?,?,?,?)",
                     (f"B-{cohort_id}", cohort_id, "observation", None, config.capital,
                      config.benchmark, config.start_date, now))
        append_ledger(conn, "cohort_approved", settings.user, payload, cohort_id)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"cohort_id": cohort_id, "approval_id": approval_id, "config_hash": config.digest,
            "mode": config.mode, "promotion_eligible": config.mode == "validation"}


def active_cohorts(conn: sqlite3.Connection, asof: str) -> list[dict]:
    rows = conn.execute("SELECT c.* FROM fresh_cohorts c JOIN decisions d ON d.decision_id=c.approval_id"
                        " WHERE d.decided_by=c.approval_by AND d.decision='approved' AND c.start_date<=?",
                        (asof,)).fetchall()
    out = []
    for row in rows:
        events = conn.execute("SELECT record_kind FROM forward_ledger WHERE cohort_id=? ORDER BY seq",
                              (row["cohort_id"],)).fetchall()
        if any(e[0] in ("closed", "paused") for e in events):
            continue
        out.append(dict(row))
    return out


def pause_or_close(conn: sqlite3.Connection, settings: Settings, cohort_id: str, kind: str, reason: str) -> None:
    _require_human(settings)
    if kind not in ("paused", "closed") or not reason.strip():
        raise validation_error("reason and paused/closed state required")
    if not conn.execute("SELECT 1 FROM fresh_cohorts WHERE cohort_id=?", (cohort_id,)).fetchone():
        raise AlphaSieveError("NOT_FOUND", f"cohort {cohort_id} not found")
    append_ledger(conn, kind, settings.user, {"reason": reason}, cohort_id)


def approve_paper(conn: sqlite3.Connection, settings: Settings, cohort_id: str,
                  evidence_hash: str, reason: str, start_date: str) -> dict:
    _require_human(settings)
    if not reason.strip() or len(evidence_hash) != 64:
        raise validation_error("paper approval needs a reason and evidence SHA-256")
    row = conn.execute("SELECT * FROM fresh_cohorts WHERE cohort_id=?", (cohort_id,)).fetchone()
    verdict = conn.execute("SELECT verdict,metrics_json FROM fresh_verdicts WHERE cohort_id=?", (cohort_id,)).fetchone()
    if row is None or row["object_kind"] != "strategy" or row["mode"] != "validation" or \
            verdict is None or verdict[0] != "fresh_supported":
        raise validation_error("supported validation strategy cohort required for paper")
    if sha256_hex(verdict["metrics_json"]) != evidence_hash:
        raise validation_error("paper evidence hash does not match the locked verdict")
    if start_date <= utcnow_iso()[:10]:
        raise validation_error("paper book must start on a future date")
    book_id = f"P-{uuid.uuid4().hex[:12]}"
    decision_id = f"D-{uuid.uuid4().hex[:10]}"
    config = json.loads(row["config_json"])
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("INSERT INTO decisions (decision_id,object_type,object_id,decision,reason,decided_by,"
                     "decided_at,evidence_hash) VALUES (?,?,?,?,?,?,?,?)",
                     (decision_id, "paper_book", book_id, "approved", reason, settings.user,
                      utcnow_iso(), evidence_hash))
        conn.execute("INSERT INTO paper_books VALUES (?,?,?,?,?,?,?,?)",
                     (book_id, cohort_id, "approved_paper", decision_id, config["capital"],
                      config["benchmark"], start_date, utcnow_iso()))
        append_ledger(conn, "paper_approved", settings.user,
                      {"decision_id": decision_id, "evidence_hash": evidence_hash}, cohort_id,
                      book_id=book_id)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"book_id": book_id, "approval_id": decision_id, "start_date": start_date}


def read_model(conn: sqlite3.Connection) -> dict:
    cohorts = []
    for row in conn.execute("SELECT c.* FROM fresh_cohorts c JOIN decisions d ON d.decision_id=c.approval_id"
                            " WHERE d.object_type='fresh_cohort' AND d.object_id=c.cohort_id"
                            " AND d.decision='approved' ORDER BY c.approval_at DESC"):
        cid = row["cohort_id"]
        days = conn.execute("SELECT date,status,nav,benchmark_nav,ret,benchmark_ret,metrics_json FROM paper_days"
                            " WHERE book_id=? ORDER BY date", (f"B-{cid}",)).fetchall()
        missing = [{"date": d["date"], "reason": d["status"]} for d in days if d["status"] != "valid"]
        nav = [{"date": d["date"], "nav": d["nav"], "benchmark_nav": d["benchmark_nav"],
                "valid": d["status"] == "valid"} for d in days]
        valid = sum(d["status"] == "valid" for d in days)
        pol = json.loads(row["policy_json"])
        if row["object_kind"] == "strategy":
            required = pol.get("strategy_a", {}).get("valid_return_days_min", 120)
        else:
            required = pol.get("factor", {}).get("mature_valid_days_min", 60)
        vr = conn.execute("SELECT verdict,metrics_json FROM fresh_verdicts WHERE cohort_id=?", (cid,)).fetchone()
        event = conn.execute("SELECT record_kind FROM forward_ledger WHERE cohort_id=? AND record_kind IN"
                             " ('paused','closed') ORDER BY seq DESC LIMIT 1", (cid,)).fetchone()
        cohorts.append({"cohort_id": cid, "object_kind": row["object_kind"], "mode": row["mode"],
                        "trial_id": row["trial_id"], "approved": True, "approved_by": row["approval_by"],
                        "approved_at": row["approval_at"], "start_date": row["start_date"],
                        "last_date": days[-1]["date"] if days else None,
                        "status": event[0] if event else ("observing" if days else "locked"),
                        "observed_days": valid, "required_days": required,
                        "verdict": vr["verdict"] if vr else None, "nav": nav, "missing_days": missing,
                        "metrics": json.loads(vr["metrics_json"]) if vr else {}})
    return {"cohorts": cohorts}
