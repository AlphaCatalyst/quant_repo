import json
import os
import re
import subprocess
from pathlib import Path

from alphasieve.cli.registry import CommandResult, command
from alphasieve.errors import AlphaSieveError, validation_error

ALL = ("agent", "human", "system")
HUMAN = ("human",)
HUMAN_SYSTEM = ("human", "system")
REMOTE_ROOT = os.environ.get("ALPHASIEVE_REMOTE_ROOT", "/taijifs_zw35/r2/felixjjiang/alphasieve")


def _progress(stage, done, total):
    import sys

    print(f"[{stage}] {done}/{total}", file=sys.stderr, flush=True)


@command("train list", ALL, help="training tasks defined under configs/training_tasks")
def cmd_train_list(args, ctx) -> CommandResult:
    from alphasieve.training.task import list_tasks

    tasks = list_tasks(ctx.settings)
    return CommandResult(data={"tasks": [{"task_id": t.task_id, "mandate": t.mandate, "label": t.label.kind,
                                          "horizons": t.label.horizons, "candidates": t.candidate_count(),
                                          "config_hash": t.config_hash} for t in tasks]})


def _configure_task(p):
    p.add_argument("task", help="task id under configs/training_tasks or a path to a task YAML")


@command("train validate", ALL, configure=_configure_task, help="validate a training task (no trial is recorded)")
def cmd_train_validate(args, ctx) -> CommandResult:
    from alphasieve.training.task import load_task

    task = load_task(ctx.settings, args.task)
    return CommandResult(data={"task_id": task.task_id, "config_hash": task.config_hash,
                               "candidates": task.candidates(), "task": task.model_dump(mode="json")})


@command("train screen-derived", HUMAN_SYSTEM, configure=_configure_task, needs_store=True,
         help="outcome-free screen of the derived factor candidates against a task's existing features")
def cmd_train_screen_derived(args, ctx) -> CommandResult:
    from alphasieve.data.access import load_panel
    from alphasieve.training import run as tr
    from alphasieve.training.derived import screen
    from alphasieve.training.samples import benchmark_returns, labels, rolling_beta, universe_mask
    from alphasieve.training.task import load_task
    from alphasieve.util import pretty_json

    task = load_task(ctx.settings, args.task)
    panel = load_panel(ctx.settings, "dev", role="system", universe=tr.panel_universe(task))
    base = {"factors": tr.resolve_features(ctx.conn, task), "panel_fields": task.features.panel_fields}
    existing = tr._frames(ctx.settings, panel, {"features": base})
    train = universe_mask(panel, task.universe_train)
    predict = universe_mask(panel, task.universe_predict)
    lab = task.label
    index_ret = benchmark_returns(panel, task.portfolio.benchmark or "zz500")
    beta = rolling_beta(panel, index_ret) if index_ret is not None else None
    ys, _ = labels(panel, lab.horizons, train, lab.kind, lab.neutralize, lab.winsorize, lab.standardize, beta,
                   task.sample.min_names_per_date)
    report = {"task_id": task.task_id, "panel_signature": panel.signature, "existing": sorted(existing),
              **screen(panel, existing, train | predict, predict, ys)}
    out = ctx.settings.store_root / "models" / "_screens" / f"derived_{task.task_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(pretty_json(report), encoding="utf-8")
    return CommandResult(data={"path": str(out), "kept": report["kept"],
                               "decisions": {k: v["decision"] for k, v in report["candidates"].items()}})


def _configure_run(p):
    p.add_argument("--task", default=None, help="task id or YAML path (local run: records a strategy trial)")
    p.add_argument("--bundle", default=None, help="frozen bundle from 'train submit' (platform run, no ledger)")
    p.add_argument("--processes", type=int, default=None)
    p.add_argument("--threads", type=int, default=None)


@command("train run", HUMAN_SYSTEM, configure=_configure_run, needs_store=True,
         help="run a training task on the dev tier: walk-forward scores, mandate portfolio, backtest")
def cmd_train_run(args, ctx) -> CommandResult:
    from alphasieve.training import run as tr
    from alphasieve.training.task import load_task, parse_task

    if bool(args.task) == bool(args.bundle):
        raise validation_error("pass exactly one of --task or --bundle")
    if args.bundle:
        bundle = tr.load_bundle(args.bundle)
        task = parse_task(bundle["task"])
        result, outputs = tr.execute(ctx.settings, bundle, args.processes, args.threads, _progress)
        run_id = os.environ.get("ALPHASIEVE_JOB_ID") or bundle["trial_id"]
        result["outputs"] = str(tr.write_outputs(ctx.settings, task.task_id, run_id, result, outputs, bundle))
        return CommandResult(data=result)
    task = load_task(ctx.settings, args.task)
    trial_id = tr.new_trial_id()
    bundle = tr.make_bundle(task, tr.resolve_features(ctx.conn, task), trial_id, ctx.settings)
    tr.start_trial(ctx.conn, ctx.settings, task, trial_id)
    try:
        result, outputs = tr.execute(ctx.settings, bundle, args.processes, args.threads, _progress)
    except Exception as exc:
        tr.fail_trial(ctx.conn, ctx.settings, task, trial_id, repr(exc))
        raise
    result["outputs"] = str(tr.write_outputs(ctx.settings, task.task_id, trial_id, result, outputs, bundle))
    result["artifact_id"] = tr.complete_trial(ctx.conn, ctx.settings, task, trial_id, result)
    result["trial_id"] = trial_id
    return CommandResult(data=result)


def _configure_submit(p):
    p.add_argument("--task", required=True)
    p.add_argument("--cluster", default=None, help="Ray address (default: the task's platform.cluster)")
    p.add_argument("--cpus", type=int, default=None, help="entrypoint CPUs reserved on one node")
    p.add_argument("--processes", type=int, default=None)
    p.add_argument("--threads", type=int, default=4)


@command("train submit", HUMAN_SYSTEM, configure=_configure_submit,
         help="record a strategy trial locally and run the task as a Ray job on the platform")
def cmd_train_submit(args, ctx) -> CommandResult:
    from alphasieve.training import run as tr
    from alphasieve.training.task import load_task

    task = load_task(ctx.settings, args.task)
    trial_id = tr.new_trial_id()
    bundle = tr.make_bundle(task, tr.resolve_features(ctx.conn, task), trial_id, ctx.settings)
    bundle_dir = Path(REMOTE_ROOT) / "runs" / "bundles"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = bundle_dir / f"{trial_id}.json"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    processes = args.processes or task.platform.processes
    cpus = args.cpus or processes * args.threads
    tr.start_trial(ctx.conn, ctx.settings, task, trial_id)
    script = Path(__file__).resolve().parents[3] / "deploy" / "ray" / "submit.sh"
    env = {**os.environ, "RAY_ADDRESS": args.cluster or task.platform.cluster,
           "ALPHASIEVE_ENTRYPOINT_CPUS": str(cpus), "ALPHASIEVE_NO_WAIT": "1"}
    cmd = [str(script), f"train-{trial_id}", "train", "run", "--bundle", str(bundle_path),
           "--processes", str(processes), "--threads", str(args.threads)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
    match = re.search(r"job (alphasieve-\S+);", proc.stdout)
    if proc.returncode != 0 or not match:
        detail = f"submit failed: {proc.stdout[-300:]}{proc.stderr[-300:]}"
        tr.fail_trial(ctx.conn, ctx.settings, task, trial_id, detail)
        raise AlphaSieveError("INTERNAL", "Ray submission failed", details={"stderr": proc.stderr[-500:]})
    job_id = match.group(1)
    return CommandResult(data={"trial_id": trial_id, "job_id": job_id, "bundle": str(bundle_path),
                               "result": f"{REMOTE_ROOT}/runs/{job_id}/result.json", "cpus": cpus})


def _configure_collect(p):
    p.add_argument("--trial-id", required=True)
    p.add_argument("--result", required=True, help="result.json written by the platform job")


@command("train collect", HUMAN_SYSTEM, configure=_configure_collect, needs_store=True,
         help="record the result of a platform training run in the local ledger")
def cmd_train_collect(args, ctx) -> CommandResult:
    from alphasieve.training import run as tr
    from alphasieve.training.task import parse_task

    text = Path(args.result).read_text(encoding="utf-8")
    envelope = json.loads(text[text.find("{"):])
    if envelope.get("status") != "ok":
        raise AlphaSieveError("INTERNAL", "the platform run failed", details={"error": envelope.get("error")})
    result = envelope["data"]
    if result["manifest"]["trial_id"] != args.trial_id:
        raise validation_error("result belongs to another trial", found=result["manifest"]["trial_id"])
    started = ctx.conn.execute("SELECT 1 FROM trials WHERE trial_id = ? AND record_kind = 'started'"
                               " AND layer = 'strategy'", (args.trial_id,)).fetchone()
    if started is None:
        raise validation_error(f"trial {args.trial_id} was not started through 'train submit'")
    task = parse_task(result["bundle"]["task"])
    artifact_id = tr.complete_trial(ctx.conn, ctx.settings, task, args.trial_id, result)
    return CommandResult(data={"trial_id": args.trial_id, "artifact_id": artifact_id,
                               "metrics": tr.summary_metrics(result), "acceptance": result.get("acceptance")})


def _configure_trial(p):
    p.add_argument("--trial-id", required=True)


@command("train holdout-request", HUMAN, configure=_configure_trial, needs_store=True,
         help="lock a completed dev training trial for one strategy-layer holdout read")
def cmd_train_holdout_request(args, ctx) -> CommandResult:
    from alphasieve.training.holdout import request_read

    return CommandResult(data=request_read(ctx.conn, ctx.settings, args.trial_id))


def _configure_decide(p):
    p.add_argument("request_id")
    p.add_argument("--reason", required=True)


@command("train holdout-approve", HUMAN, configure=_configure_decide, needs_store=True,
         help="approve and run one strategy-layer holdout read (human only, one per mandate)")
def cmd_train_holdout_approve(args, ctx) -> CommandResult:
    from alphasieve.training.holdout import approve_read

    return CommandResult(data=approve_read(ctx.conn, ctx.settings, args.request_id, args.reason))


@command("train holdout-reject", HUMAN, configure=_configure_decide, help="reject a strategy-layer holdout request")
def cmd_train_holdout_reject(args, ctx) -> CommandResult:
    from alphasieve.training.holdout import reject_read

    return CommandResult(data=reject_read(ctx.conn, ctx.settings, args.request_id, args.reason))


@command("train holdout-list", HUMAN, help="strategy-layer holdout requests")
def cmd_train_holdout_list(args, ctx) -> CommandResult:
    rows = [dict(r) for r in ctx.conn.execute("SELECT * FROM strategy_holdout_requests ORDER BY created_at")]
    for r in rows:
        r["result"] = json.loads(r.pop("result_json") or "null")
    return CommandResult(data={"requests": rows, "count": len(rows)})


def _configure_abandon(p):
    p.add_argument("--trial-id", required=True)
    p.add_argument("--reason", required=True)


@command("train abandon", HUMAN, configure=_configure_abandon,
         help="record a started strategy trial as failed (e.g. a platform run stopped because of a bug)")
def cmd_train_abandon(args, ctx) -> CommandResult:
    from alphasieve.contracts import TrialLedgerEntry
    from alphasieve.ledger.ledger import append_trial

    rows = [dict(r) for r in ctx.conn.execute("SELECT * FROM trials WHERE trial_id = ? AND layer = 'strategy'"
                                              " ORDER BY seq", (args.trial_id,))]
    if not rows or rows[0]["record_kind"] != "started":
        raise validation_error(f"no started strategy trial {args.trial_id}")
    if any(r["record_kind"] in ("completed", "failed") for r in rows):
        raise AlphaSieveError("CONFLICT", f"trial {args.trial_id} already has a result record")
    first = rows[0]
    append_trial(ctx.conn, TrialLedgerEntry(
        trial_id=args.trial_id, record_kind="failed", candidate_hash=first["candidate_hash"],
        evidence_tier=first["evidence_tier"], search_space_version=first["search_space_version"],
        metrics={"reason": args.reason}, outcome="abandoned", created_by=ctx.settings.role, layer="strategy",
        scope=first["scope"]))
    return CommandResult(data={"trial_id": args.trial_id, "outcome": "abandoned"})
