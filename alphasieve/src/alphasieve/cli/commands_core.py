from pathlib import Path

from alphasieve import __version__
from alphasieve.cli.registry import CommandResult, command
from alphasieve.config import ensure_storage, load_config
from alphasieve.contracts import export_schemas
from alphasieve.errors import AlphaSieveError
from alphasieve.ledger import ledger_stats, verify_ledger

ALL = ("agent", "human", "system")
HUMAN_SYSTEM = ("human", "system")


@command("version", ALL, needs_state=False, help="version information")
def cmd_version(args, ctx) -> CommandResult:
    return CommandResult(data={"version": __version__})


@command("init", HUMAN_SYSTEM, needs_store=True, help="create storage directories and the state database")
def cmd_init(args, ctx) -> CommandResult:
    ensure_storage(ctx.settings, need_store=True)
    ctx.conn  # noqa: B018  (opens and migrates the database)
    configs = {name: load_config(ctx.settings, name)["version"] for name in ("splits", "gate_policy", "costs")}
    return CommandResult(
        data={
            "hot_root": str(ctx.settings.hot_root),
            "store_root": str(ctx.settings.store_root),
            "state_db": str(ctx.settings.state_db),
            "config_versions": configs,
        }
    )


@command("ledger verify", ALL, help="verify the trial ledger hash chain")
def cmd_ledger_verify(args, ctx) -> CommandResult:
    report = verify_ledger(ctx.conn)
    error = None
    if not report["ok"]:
        error = AlphaSieveError("CONFLICT", "ledger verification failed", {"errors": report["errors"][:20]})
    return CommandResult(data=report, error=error)


def _configure_stats(p):
    p.add_argument("--campaign", default=None)
    p.add_argument("--tier", default="dev", choices=["dev", "holdout", "fresh"])


def _configure_void(p):
    p.add_argument("--trial-id", required=True)
    p.add_argument("--seq", type=int, required=True)
    p.add_argument("--reason", required=True)


@command("ledger void-duplicate", ("human",), configure=_configure_void,
         help="void a duplicated result record (append-only; the row stays in the hash chain)")
def cmd_ledger_void(args, ctx) -> CommandResult:
    from alphasieve.errors import validation_error
    from alphasieve.ledger.ledger import void_duplicate_result

    try:
        row = void_duplicate_result(ctx.conn, args.trial_id, args.seq, args.reason, ctx.settings.role)
    except ValueError as exc:
        raise validation_error(str(exc)) from exc
    return CommandResult(data={"seq": row["seq"], "trial_id": args.trial_id, "voids_seq": args.seq})


@command("ledger stats", ALL, configure=_configure_stats, help="trial counts and failure reasons")
def cmd_ledger_stats(args, ctx) -> CommandResult:
    if ctx.settings.role == "agent" and args.tier != "dev":
        raise AlphaSieveError("PERMISSION_DENIED", "agent may only read dev-tier ledger statistics")
    return CommandResult(data=ledger_stats(ctx.conn, args.campaign, args.tier))


def _configure_backup(p):
    p.add_argument("--no-prune", action="store_true", help="keep all existing backups")


@command("state backup", HUMAN_SYSTEM, configure=_configure_backup, needs_store=True,
         help="copy the state database to the store, verify the copy, prune old copies")
def cmd_state_backup(args, ctx) -> CommandResult:
    from alphasieve.state.backup import prune_backups, take_backup

    info = take_backup(ctx.conn, ctx.settings.backups_dir)
    info["pruned"] = [] if args.no_prune else prune_backups(ctx.settings.backups_dir)
    return CommandResult(data=info)


def _configure_schema(p):
    p.add_argument("--out", default="schemas")


@command("schema export", HUMAN_SYSTEM, configure=_configure_schema, needs_state=False, help="export JSON Schemas")
def cmd_schema_export(args, ctx) -> CommandResult:
    paths = export_schemas(Path(args.out))
    return CommandResult(data={"files": [str(p) for p in paths]})
