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


@command("ledger stats", ALL, configure=_configure_stats, help="trial counts and failure reasons")
def cmd_ledger_stats(args, ctx) -> CommandResult:
    if ctx.settings.role == "agent" and args.tier != "dev":
        raise AlphaSieveError("PERMISSION_DENIED", "agent may only read dev-tier ledger statistics")
    return CommandResult(data=ledger_stats(ctx.conn, args.campaign, args.tier))


def _configure_schema(p):
    p.add_argument("--out", default="schemas")


@command("schema export", HUMAN_SYSTEM, configure=_configure_schema, needs_state=False, help="export JSON Schemas")
def cmd_schema_export(args, ctx) -> CommandResult:
    paths = export_schemas(Path(args.out))
    return CommandResult(data={"files": [str(p) for p in paths]})
