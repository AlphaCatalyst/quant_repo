import json
import os

from alphasieve.cli.registry import CommandResult, command
from alphasieve.errors import AlphaSieveError, validation_error

ALL = ("agent", "human", "system")
HUMAN = ("human",)
HUMAN_SYSTEM = ("human", "system")


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
    p.add_argument("--bundle", default=None, help="frozen bundle from 'jobs submit train' (platform run, no ledger)")
    p.add_argument("--units-dir", default=None, help="shared checkpoint directory for a platform bundle")
    p.add_argument("--processes", type=int, default=None)
    p.add_argument("--threads", type=int, default=None)


@command("train run", HUMAN_SYSTEM, configure=_configure_run, needs_store=True,
         help="run a training task on the dev tier: walk-forward scores, mandate portfolio, backtest")
def cmd_train_run(args, ctx) -> CommandResult:
    from alphasieve.training import run as tr
    from alphasieve.training.task import load_task, parse_task

    if bool(args.task) == bool(args.bundle):
        raise validation_error("pass exactly one of --task or --bundle")
    if args.units_dir and not args.bundle:
        raise validation_error("--units-dir requires --bundle")
    if args.bundle:
        bundle = tr.load_bundle(args.bundle)
        task = parse_task(bundle["task"])
        result, outputs = tr.execute(ctx.settings, bundle, args.processes, args.threads, _progress,
                                     units_dir=args.units_dir)
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
    p.add_argument("--signature")


@command("train holdout-approve", HUMAN, configure=_configure_decide, needs_store=True,
         help="approve and run one strategy-layer holdout read (human only, one per mandate)")
def cmd_train_holdout_approve(args, ctx) -> CommandResult:
    from alphasieve.approvals import consume_signature
    from alphasieve.training.holdout import approve_read

    consume_signature(ctx.conn, ctx.settings, "strategy_holdout", args.request_id, "approve", args.signature)
    return CommandResult(data=approve_read(ctx.conn, ctx.settings, args.request_id, args.reason))


@command("train holdout-reject", HUMAN, configure=_configure_decide, help="reject a strategy-layer holdout request")
def cmd_train_holdout_reject(args, ctx) -> CommandResult:
    from alphasieve.approvals import consume_signature
    from alphasieve.training.holdout import reject_read

    consume_signature(ctx.conn, ctx.settings, "strategy_holdout", args.request_id, "reject", args.signature)
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
