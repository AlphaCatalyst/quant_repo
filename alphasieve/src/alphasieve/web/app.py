"""Read-only web API and static frontend. Every write (approvals, directives, decisions) stays in the CLI."""

import hmac
import json
import os
import re
import secrets
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from alphasieve import __version__
from alphasieve.campaigns import memory, service, stats
from alphasieve.config import Settings, get_settings, load_config
from alphasieve.data.access import read_meta
from alphasieve.errors import AlphaSieveError
from alphasieve.factors import library as lib
from alphasieve.ledger import ledger_stats, verify_ledger
from alphasieve.state.backup import list_backups

DIST = Path(__file__).resolve().parent / "dist"
ARTIFACT_ID = re.compile(r"^[0-9a-f]{24}$")
security = HTTPBasic(auto_error=False)


def credentials_path(settings: Settings) -> Path:
    return settings.hot_root / "web.credentials"


def ensure_credentials(settings: Settings) -> Path:
    path = credentials_path(settings)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"username=alphasieve\npassword={secrets.token_urlsafe(18)}\n", encoding="utf-8")
        path.chmod(0o600)
    return path


def _load_credentials(settings: Settings) -> tuple[str, str]:
    values = dict(line.split("=", 1) for line in credentials_path(settings).read_text().splitlines() if "=" in line)
    return values["username"].strip(), values["password"].strip()


def _ro_connect(settings: Settings) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{settings.state_db}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _loads(value, default=None):
    return json.loads(value) if value else default


def _transcript_events(path: Path, limit: int = 400) -> list[dict]:
    events = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = e.get("item") or {}
        if e.get("type") == "item.completed" and item.get("type") == "agent_message":
            events.append({"kind": "message", "text": item.get("text", "")})
        elif e.get("type") == "item.completed" and item.get("type") == "command_execution":
            events.append({"kind": "command", "text": item.get("command", ""), "exit_code": item.get("exit_code"),
                           "output": (item.get("aggregated_output") or "")[-1500:]})
        elif e.get("type") == "assistant":
            for block in (e.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    events.append({"kind": "message", "text": block["text"]})
                elif block.get("type") == "tool_use":
                    inp = block.get("input") or {}
                    text = inp.get("command") or inp.get("file_path") or json.dumps(inp)[:300]
                    events.append({"kind": "command", "text": f"{block.get('name')}: {text}"})
        elif e.get("type") == "user":
            content = (e.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result" and events and events[-1]["kind"] == "command":
                    out = block.get("content")
                    out = out if isinstance(out, str) else json.dumps(out)
                    events[-1]["output"] = out[-1500:]
                    events[-1]["exit_code"] = 1 if block.get("is_error") else 0
        elif e.get("cmd"):
            events.append({"kind": "command", "text": "alphasieve " + " ".join(e["cmd"]), "exit_code": e.get("code"),
                           "output": e.get("stdout", "")[-1500:]})
    return events[-limit:]


def create_app(settings: Settings | None = None, require_auth: bool | None = None) -> FastAPI:
    settings = replace(settings or get_settings(), role="human")
    if require_auth is None:
        require_auth = os.environ.get("ALPHASIEVE_WEB_AUTH", "basic") != "none"
    if require_auth:
        ensure_credentials(settings)
    app = FastAPI(title="AlphaSieve", version=__version__, docs_url=None, redoc_url=None)

    def auth(creds: HTTPBasicCredentials | None = Depends(security)) -> str:
        if not require_auth:
            return "anonymous"
        if creds is None:
            raise HTTPException(401, "login required", headers={"WWW-Authenticate": "Basic"})
        user, password = _load_credentials(settings)
        ok = hmac.compare_digest(creds.username.encode(), user.encode()) and \
            hmac.compare_digest(creds.password.encode(), password.encode())
        if not ok:
            raise HTTPException(401, "invalid credentials", headers={"WWW-Authenticate": "Basic"})
        return creds.username

    def db():
        conn = _ro_connect(settings)
        try:
            yield conn
        finally:
            conn.close()

    @app.exception_handler(AlphaSieveError)
    async def _err(request, exc: AlphaSieveError):
        from fastapi.responses import JSONResponse
        status = {"NOT_FOUND": 404, "PERMISSION_DENIED": 403, "VALIDATION_ERROR": 400}.get(exc.code, 500)
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=status)

    @app.get("/api/overview")
    def overview(user: str = Depends(auth), conn=Depends(db)):
        campaigns = []
        for c in service.list_campaigns(conn):
            campaigns.append({**c, "budgets": stats.budget_status(conn, c["campaign_id"]),
                              "funnel": stats.funnel(conn, c["campaign_id"])["counts"]})
        count = lambda q: conn.execute(q).fetchone()[0]  # noqa: E731
        return {
            "version": __version__, "user": user, "campaigns": campaigns,
            "ledger": ledger_stats(conn), "library_size": len(lib.library_members(conn)),
            "inbox": {"open_requests": count("SELECT COUNT(*) FROM agent_requests WHERE status = 'open'"),
                      "pending_holdout": count("SELECT COUNT(*) FROM holdout_requests WHERE status = 'pending'"),
                      "open_reviews": count("SELECT COUNT(*) FROM review_packets WHERE status = 'open'")},
            "data": _data_status(),
        }

    @app.get("/api/status")
    def status(user: str = Depends(auth), conn=Depends(db)):
        latest_backup = None
        backups = list_backups(settings.backups_dir)
        for path in reversed(backups):
            try:
                taken_at = datetime.strptime(path.stem.removeprefix("alphasieve-"), "%Y%m%dT%H%M%SZ")
            except ValueError:
                continue
            ledger_rows = None
            try:
                info = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
                if isinstance(info.get("ledger_rows"), int):
                    ledger_rows = info["ledger_rows"]
            except (OSError, ValueError, TypeError):
                pass
            latest_backup = {"created_at": taken_at.replace(tzinfo=UTC).isoformat(),
                             "size_bytes": path.stat().st_size, "ledger_rows": ledger_rows}
            break
        last_update = _last_daily_update()
        return {
            "running_campaigns": conn.execute("SELECT COUNT(*) FROM campaigns WHERE status = 'running'").fetchone()[0],
            "latest_trade_date": last_update.get("end") if last_update else None,
            "latest_backup": latest_backup,
            "inbox": {
                "open_requests": conn.execute("SELECT COUNT(*) FROM agent_requests WHERE status = 'open'").fetchone()[0],
                "request_campaigns": [r[0] for r in conn.execute(
                    "SELECT DISTINCT campaign_id FROM agent_requests WHERE status = 'open' ORDER BY campaign_id")],
                "pending_factor_holdout": conn.execute(
                    "SELECT COUNT(*) FROM holdout_requests WHERE status = 'pending'").fetchone()[0],
                "factor_holdout_campaigns": [r[0] for r in conn.execute(
                    "SELECT DISTINCT campaign_id FROM holdout_requests WHERE status = 'pending' ORDER BY campaign_id")],
                "pending_strategy_holdout": conn.execute(
                    "SELECT COUNT(*) FROM strategy_holdout_requests WHERE status = 'pending'").fetchone()[0],
                "open_reviews": conn.execute("SELECT COUNT(*) FROM review_packets WHERE status = 'open'").fetchone()[0],
                "review_campaigns": [r[0] for r in conn.execute(
                    "SELECT DISTINCT campaign_id FROM review_packets WHERE status = 'open' ORDER BY campaign_id")],
            },
            "data_last_daily_update": last_update,
        }

    def _last_daily_update() -> dict | None:
        log = settings.hot_root / "logs" / "daily-update.jsonl"
        if not log.exists():
            return None
        lines = [ln for ln in log.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        if not lines:
            return None
        try:
            last = json.loads(lines[-1])
        except json.JSONDecodeError:
            return None
        return {"status": last.get("status"), "end": (last.get("data") or {}).get("end"),
                "new_rows": ((last.get("data") or {}).get("daily") or {}).get("new_rows"),
                "logged_at": log.stat().st_mtime}

    def _data_status() -> dict:
        splits = load_config(settings, "splits")
        tiers = {}
        for tier in ("dev", "holdout"):
            meta = read_meta(settings, tier)
            tiers[tier] = None if meta is None else {k: meta.get(k) for k in ("window", "rows", "codes", "built_at",
                                                                               "signature")}
        return {"splits": {k: splits.get(k) for k in ("dev", "holdout", "fresh")}, "panels": tiers,
                "last_daily_update": _last_daily_update()}

    @app.get("/api/data")
    def data(user: str = Depends(auth)):
        return _data_status()

    @app.get("/api/campaigns/{campaign_id}")
    def campaign(campaign_id: str, user: str = Depends(auth), conn=Depends(db)):
        c = service.get_campaign(conn, campaign_id)
        turns = [dict(r) for r in conn.execute("SELECT * FROM turns WHERE campaign_id = ? ORDER BY turn_index",
                                                (campaign_id,))]
        for t in turns:
            t["usage"] = _loads(t.pop("usage_json"), {})
        shortlists = [dict(r) for r in conn.execute("SELECT * FROM shortlists WHERE campaign_id = ?", (campaign_id,))]
        for s in shortlists:
            s["members"] = _loads(s.pop("members_json"), [])
            s["l3"] = _loads(s.pop("l3_json"), {})
        holdout = [dict(r) for r in conn.execute("SELECT * FROM holdout_requests WHERE campaign_id = ?",
                                                  (campaign_id,))]
        for h in holdout:
            h["result"] = _loads(h.pop("result_json"))
        return {
            "campaign": {**c, "spec": c["spec"].model_dump()},
            "budgets": stats.budget_status(conn, campaign_id),
            "funnel": stats.funnel(conn, campaign_id),
            "intensity": stats.search_intensity(conn, campaign_id),
            "recent": stats.recent_outcomes(conn, campaign_id, limit=30),
            "turns": turns,
            "memory": {"derived": memory.derive(conn, campaign_id), "insights": memory.insights(conn, campaign_id)},
            "directives": service.directives(conn, campaign_id),
            "requests": [dict(r) for r in conn.execute("SELECT * FROM agent_requests WHERE campaign_id = ?"
                                                       " ORDER BY created_at", (campaign_id,))],
            "shortlists": shortlists, "holdout_requests": holdout,
            "review_packets": [dict(r) for r in conn.execute("SELECT * FROM review_packets WHERE campaign_id = ?",
                                                             (campaign_id,))],
        }

    @app.get("/api/campaigns/{campaign_id}/turns/{turn_id}")
    def turn(campaign_id: str, turn_id: str, user: str = Depends(auth), conn=Depends(db)):
        row = conn.execute("SELECT * FROM turns WHERE campaign_id = ? AND turn_id = ?",
                           (campaign_id, turn_id)).fetchone()
        if row is None:
            raise HTTPException(404, "turn not found")
        out = dict(row)
        out["usage"] = _loads(out.pop("usage_json"), {})
        out["events"] = _transcript_events(Path(out["transcript_path"])) if out["transcript_path"] else []
        return out

    @app.get("/api/factors")
    def factors(user: str = Depends(auth), conn=Depends(db), state: str | None = None,
                campaign: str | None = None, limit: int = Query(500, le=5000)):
        query = ("SELECT f.factor_id, f.version, f.name, f.state, f.canonical_expression, f.created_by, f.created_at,"
                 " f.spec_json, (SELECT metrics_json FROM trials t WHERE t.factor_id = f.factor_id AND t.version ="
                 " f.version AND t.record_kind = 'completed' AND t.evidence_tier = 'dev' ORDER BY seq DESC LIMIT 1)"
                 " AS metrics_json, (SELECT campaign_id FROM trials t WHERE t.factor_id = f.factor_id AND t.version ="
                 " f.version AND t.record_kind = 'completed' ORDER BY seq LIMIT 1) AS campaign_id"
                 " FROM factor_specs f")
        rows = []
        for r in conn.execute(query + " ORDER BY f.created_at DESC LIMIT ?", (limit,)):
            d = dict(r)
            spec = _loads(d.pop("spec_json"), {})
            d["cell"] = spec.get("cell")
            d["direction"] = spec.get("direction")
            d["metrics"] = _loads(d.pop("metrics_json"), {})
            if (state and d["state"] != state) or (campaign and d["campaign_id"] != campaign):
                continue
            rows.append(d)
        library = {m["factor_id"] for m in lib.library_members(conn)}
        for d in rows:
            d["in_library"] = d["factor_id"] in library
        return {"factors": rows, "count": len(rows)}

    @app.get("/api/factors/{factor_id}")
    def factor(factor_id: str, user: str = Depends(auth), conn=Depends(db)):
        from alphasieve.factors.registry import get_factor
        f = get_factor(conn, factor_id)
        trials = [dict(r) for r in conn.execute(
            "SELECT trial_id, campaign_id, evidence_tier, outcome, metrics_json, gate_results_json, artifact_id,"
            " created_by, created_at FROM trials WHERE factor_id = ? AND version = ? AND record_kind = 'completed'"
            " ORDER BY seq", (f["factor_id"], f["version"]))]
        for t in trials:
            t["metrics"] = _loads(t.pop("metrics_json"), {})
            t["gates"] = _loads(t.pop("gate_results_json"), {})
        events = [dict(r) for r in conn.execute(
            "SELECT ts, role, payload_json FROM events WHERE event_type = 'factor.state_changed' AND object_id = ?"
            " ORDER BY seq", (f"{f['factor_id']}@{f['version']}",))]
        for e in events:
            e.update(_loads(e.pop("payload_json"), {}))
        return {**f, "trials": trials, "state_history": events}

    @app.get("/api/ledger")
    def ledger(user: str = Depends(auth), conn=Depends(db), campaign: str | None = None,
               limit: int = Query(200, le=2000), offset: int = 0):
        query, params = "SELECT * FROM trials WHERE record_kind != 'started'", []
        if campaign:
            query += " AND campaign_id = ?"
            params.append(campaign)
        rows = [dict(r) for r in conn.execute(query + " ORDER BY seq DESC LIMIT ? OFFSET ?", (*params, limit, offset))]
        for r in rows:
            r["metrics"] = _loads(r.pop("metrics_json"), {})
            r["gates"] = _loads(r.pop("gate_results_json"), {})
        return {"trials": rows, "stats": ledger_stats(conn, campaign), "verify": verify_ledger(conn)}

    @app.get("/api/mandates")
    def mandates_view(user: str = Depends(auth), conn=Depends(db)):
        from alphasieve.training.holdout import READS_PER_MANDATE
        from alphasieve.training.mandates import STRATEGY_TRIAL_BUDGET

        trials: dict[str, dict] = {}
        for r in conn.execute("SELECT * FROM trials WHERE layer = 'strategy' AND record_kind != 'void' ORDER BY seq"):
            metrics = _loads(r["metrics_json"], {})
            t = trials.setdefault(r["trial_id"], {"trial_id": r["trial_id"], "mandate": r["scope"],
                                                  "config_hash": r["candidate_hash"], "status": "open"})
            if r["record_kind"] == "started":
                t.update(task_id=metrics.get("task_id"), started_at=r["created_at"])
            else:
                t.update(status=r["record_kind"], tier=r["evidence_tier"], outcome=r["outcome"],
                         artifact_id=r["artifact_id"], finished_at=r["created_at"], metrics=metrics)
                if (r["evidence_tier"] == "dev" and not r["trial_id"].endswith("-H")
                        and r["artifact_id"] and r["record_kind"] == "completed"):
                    artifact = settings.artifacts_dir / r["artifact_id"] / "metrics.json"
                    if artifact.is_file():
                        detail = json.loads(artifact.read_text(encoding="utf-8"))
                        t["acceptance"] = detail.get("acceptance")
        requests = [dict(r) for r in conn.execute("SELECT * FROM strategy_holdout_requests ORDER BY created_at")]
        for q in requests:
            q["result"] = _loads(q.pop("result_json"))
        out = []
        for mandate, budget in STRATEGY_TRIAL_BUDGET.items():
            rows = [t for t in trials.values() if t["mandate"] == mandate]
            dev_started = sum(1 for t in rows if not t["trial_id"].endswith("-H"))
            mine = [q for q in requests if q["mandate"] == mandate]
            out.append({"mandate": mandate, "budget": budget, "dev_trials": dev_started,
                        "holdout_reads": {"used": sum(q["status"] == "approved" for q in mine),
                                          "budget": READS_PER_MANDATE},
                        "trials": rows[::-1], "holdout_requests": mine})
        return {"mandates": out}

    @app.get("/api/strategy/{trial_id}")
    def strategy_trial(trial_id: str, user: str = Depends(auth), conn=Depends(db)):
        if not re.match(r"^S-[0-9a-f]{12}(-H)?$", trial_id):
            raise HTTPException(400, "bad trial id")
        rows = [dict(r) for r in conn.execute("SELECT * FROM trials WHERE trial_id = ? AND layer = 'strategy'"
                                              " ORDER BY seq", (trial_id,))]
        if not rows:
            raise HTTPException(404, "trial not found")
        for r in rows:
            r["metrics"] = _loads(r.pop("metrics_json"), {})
            r["gates"] = _loads(r.pop("gate_results_json"), {})
        result = next((r for r in rows if r["record_kind"] in ("completed", "failed")), None)
        detail = None
        if result and result.get("artifact_id"):
            path = settings.artifacts_dir / result["artifact_id"] / "metrics.json"
            if path.exists():
                detail = json.loads(path.read_text(encoding="utf-8"))
                bundle = detail.pop("bundle", None) or {}
                detail["task"] = bundle.get("task")
                detail["feature_summary"] = {k: (len(v) if isinstance(v, list) else v)
                                             for k, v in (bundle.get("features") or {}).items()}
        requests = [dict(r) for r in conn.execute("SELECT * FROM strategy_holdout_requests WHERE trial_id = ?",
                                                  (trial_id.removesuffix("-H"),))]
        for q in requests:
            q["result"] = _loads(q.pop("result_json"))
        return {"trial_id": trial_id, "records": rows, "detail": detail, "holdout_requests": requests}

    @app.get("/api/artifacts/{artifact_id}/report", response_class=PlainTextResponse)
    def artifact_report(artifact_id: str, user: str = Depends(auth)):
        if not ARTIFACT_ID.match(artifact_id):
            raise HTTPException(400, "bad artifact id")
        path = settings.artifacts_dir / artifact_id / "report.md"
        if not path.exists():
            raise HTTPException(404, "no report")
        return path.read_text(encoding="utf-8")

    @app.get("/api/campaigns/{campaign_id}/report", response_class=PlainTextResponse)
    def campaign_report(campaign_id: str, user: str = Depends(auth)):
        if not re.match(r"^[a-z0-9][a-z0-9-]{2,63}$", campaign_id):
            raise HTTPException(400, "bad campaign id")
        path = settings.reports_dir / campaign_id / "status.md"
        if not path.exists():
            raise HTTPException(404, "no report yet")
        return path.read_text(encoding="utf-8")

    if DIST.exists():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str, user: str = Depends(auth)):
            if path.startswith("api/"):
                raise HTTPException(404)
            return FileResponse(DIST / "index.html")

    return app
