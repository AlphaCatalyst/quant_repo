"""Single turn Researcher and Reviewer thesis runs."""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

import yaml

from alphasieve.agents.executors import PROFILES, TurnContext, TurnResult, make_executor
from alphasieve.agents.integrity import scan_commands
from alphasieve.agents.workspace import _git
from alphasieve.audit import record_event
from alphasieve.config import Settings, ensure_storage
from alphasieve.state import connect
from alphasieve.thesis import Thesis, evaluate_scenarios, load_thesis

SAMPLE = Path(__file__).resolve().parents[3] / "theses" / "hog-cycle-muyuan.yaml"
DEFAULT_MODEL = "gpt-6-sol"
DEFAULT_EFFORT = "high"
DEFAULT_TIMEOUT = 30 * 60


def _schema_summary() -> str:
    schema = Thesis.model_json_schema()
    return "# Thesis YAML schema\n\n```yaml\n" + yaml.safe_dump(schema, sort_keys=False) + "```\n"


def _program(kind: str) -> str:
    role = "Researcher" if kind == "draft" else "Reviewer"
    writable = "drafts/" if kind == "draft" else "reviews/"
    review = ("\nThe thesis under review is `target.yaml`. Write reviews/review.md with a findings table: claim,\n"
              "verdict (holds/fails/unverified), evidence with URL, and impact on valuation. Add a scorecard.\n"
              "Always write reviews/review.md before finishing; when a source cannot be reached, mark the claim\n"
              "unverified and say which source was tried.\n"
              if kind == "review" else
              "\nAlways write at least one drafts/<thesis_id>.yaml before finishing; unverifiable figures\n"
              "get grade D.\n")
    tools = ("\nTools: use WebSearch and WebFetch for sources (shell network tools are not available). Do not\n"
             "compute valuations by hand or with scripts; run `alphasieve thesis scenarios <file>` and\n"
             "`alphasieve thesis implied <file> ...` so the backend computes every number.\n")
    return (f"# Thesis research SOP ({role})\n\n"
            "Build the argument structure, then verify every parameter against primary sources.\n"
            "Grade evidence A/B/C/D: D means searched-and-not-found and must never carry an invented number.\n"
            "Cite URL and access date for every figure. Derive the market-implied value, list falsifiers,\n"
            "and propose 2–3 settleable forecasts. Never fabricate.\n\n"
            f"Write only under `{writable}`. Do not edit program.md, brief.md, schema.md, example.yaml,\n"
            "target.yaml, or files outside your writable directory.\n" + tools + review)


def _git_paths(ws: Path, baseline: str) -> list[str]:
    out = _git(ws, "diff", "--name-only", baseline, "HEAD").stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def prepare_workspace(settings: Settings, kind: str, run_id: str, *, topic: str | None = None,
                      thesis_file: str | Path | None = None) -> Path:
    if kind not in {"draft", "review"}:
        raise ValueError(f"unknown thesis run kind: {kind}")
    ws = settings.hot_root / "thesis_workspaces" / run_id
    ws.mkdir(parents=True, exist_ok=True)
    for sub in ("drafts", "reviews"):
        (ws / sub).mkdir(exist_ok=True)
    if not (ws / ".git").exists():
        _git(ws, "init", "-q")
    (ws / "program.md").write_text(_program(kind), encoding="utf-8")
    target = "target.yaml in this workspace" if thesis_file else ""
    origin = f"Original repository path (not present here): {thesis_file}\n" if thesis_file else ""
    brief = f"# Thesis brief\n\nTopic: {topic or ''}\nTarget: {target}\n{origin}"
    (ws / "brief.md").write_text(brief, encoding="utf-8")
    (ws / "schema.md").write_text(_schema_summary(), encoding="utf-8")
    shutil.copyfile(SAMPLE, ws / "example.yaml")
    if kind == "review" and thesis_file:
        shutil.copyfile(thesis_file, ws / "target.yaml")
    for name in ("program.md", "brief.md", "schema.md", "example.yaml", "target.yaml"):
        path = ws / name
        if path.exists():
            path.chmod(0o444)
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "prepare thesis workspace")
    return ws


def _run(settings: Settings, kind: str, run_id: str, prompt: str, harness: str, model: str,
         effort: str, timeout: int, *, fake_script: Callable | None = None,
         topic: str | None = None, thesis_file: str | Path | None = None) -> dict:
    ws = prepare_workspace(settings, kind, run_id, topic=topic, thesis_file=thesis_file)
    baseline = _git(ws, "rev-parse", "HEAD").stdout.strip()
    transcript = settings.store_root / "transcripts" / "thesis" / f"{run_id}.jsonl"
    ctx = TurnContext(campaign_id=run_id, turn_id=run_id, turn_index=0, workspace=ws, prompt=prompt,
                      model=model, effort=effort, timeout_s=timeout, transcript_path=transcript,
                      trial_allowance=0, profile=PROFILES["researcher" if kind == "draft" else "reviewer"])
    executor = make_executor(settings, harness, fake_script)
    result: TurnResult = executor.run(ctx)
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", f"{kind} thesis run")
    changed = _git_paths(ws, baseline)
    writable = "drafts/" if kind == "draft" else "reviews/"
    violations = [p for p in changed if not p.startswith(writable) or (ws / p).is_symlink()]
    violations.extend(scan_commands(result.commands, result.reads, ws))
    errors: list[str] = []
    drafts: list[dict] = []
    for path in sorted((ws / "drafts").glob("*.yaml")):
        try:
            thesis = load_thesis(path)
            drafts.append({"file": str(path), "thesis_id": thesis.thesis_id,
                           "scenarios": evaluate_scenarios(thesis)})
        except Exception as exc:  # validation and formula errors are result data
            errors.append(f"{path.name}: {exc}")
    error = result.error
    produced = any(p.startswith(writable) for p in changed)
    if result.status == "completed" and not produced and not violations and not errors:
        error = f"agent finished without writing under {writable}"
    status = ("rejected" if violations or errors else
              ("failed" if result.status != "completed" or not produced else "completed"))
    data = {"run_id": run_id, "kind": kind, "harness": harness, "model": model, "status": status,
            "usage": result.usage, "summary": result.summary, "error": error,
            "transcript_path": str(transcript), "workspace": str(ws), "changed_files": changed,
            "violations": violations, "validation_errors": errors, "drafts": drafts}
    ensure_storage(settings)
    conn = connect(settings.state_db)
    try:
        record_event(conn, settings, "thesis.run", status="error" if status in {"rejected", "failed"} else "ok",
                     command=f"thesis {kind}", object_type="thesis_run", object_id=run_id, payload=data)
        conn.commit()
    finally:
        conn.close()
    return data


def run_draft(settings: Settings, topic: str, harness: str = "codex", model: str = DEFAULT_MODEL,
              effort: str = DEFAULT_EFFORT, timeout: int = DEFAULT_TIMEOUT, *, run_id: str | None = None,
              fake_script: Callable | None = None) -> dict:
    rid = run_id or f"thesis-{uuid.uuid4().hex[:12]}"
    return _run(settings, "draft", rid, f"Research thesis topic: {topic}", harness, model, effort, timeout,
                fake_script=fake_script, topic=topic)


def run_review(settings: Settings, thesis_file: str | Path, harness: str = "codex", model: str = DEFAULT_MODEL,
               effort: str = DEFAULT_EFFORT, timeout: int = DEFAULT_TIMEOUT, *, run_id: str | None = None,
               fake_script: Callable | None = None) -> dict:
    rid = run_id or f"thesis-{uuid.uuid4().hex[:12]}"
    return _run(settings, "review", rid, "Review the thesis in target.yaml", harness, model, effort, timeout,
                fake_script=fake_script, thesis_file=thesis_file)


def list_runs(settings: Settings, limit: int = 20) -> list[dict]:
    ensure_storage(settings, need_store=False)
    conn = connect(settings.state_db)
    try:
        rows = conn.execute(
            "SELECT object_id, payload_json FROM events WHERE event_type='thesis.run' ORDER BY seq DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(json.loads(row["payload_json"])) for row in rows]
    finally:
        conn.close()


def show_run(settings: Settings, run_id: str) -> dict:
    rows = [r for r in list_runs(settings, 10000) if r.get("run_id") == run_id]
    if not rows:
        raise ValueError(f"thesis run {run_id} not found")
    return rows[0]
