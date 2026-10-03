"""Personal holdings commands, restricted to human and system roles."""

from pathlib import Path

from alphasieve.cli.registry import CommandResult, command
from alphasieve.portfolio_book.checkup import build_report, save_report
from alphasieve.portfolio_book.importer import DEFAULT_MAPPING, import_snapshot, list_snapshots, show_snapshot

ROLES = ("human", "system")


def _import_args(parser):
    parser.add_argument("--file", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--mapping")
    parser.add_argument("--cash", type=float)


@command("book import", roles=ROLES, configure=_import_args, needs_store=False)
def book_import(args, ctx):
    snapshot, created = import_snapshot(ctx.conn, Path(args.file), args.account, args.as_of, ctx.settings.user,
                                        Path(args.mapping) if args.mapping else DEFAULT_MAPPING, args.cash)
    return CommandResult(data={"snapshot": snapshot, "created": created})


def _list_args(parser):
    parser.add_argument("--account")


@command("book list", roles=ROLES, configure=_list_args, needs_store=False)
def book_list(args, ctx):
    return CommandResult(data={"snapshots": list_snapshots(ctx.conn, args.account)})


def _id_args(parser):
    parser.add_argument("snapshot_id")


@command("book show", roles=ROLES, configure=_id_args, needs_store=False)
def book_show(args, ctx):
    return CommandResult(data={"snapshot": show_snapshot(ctx.conn, args.snapshot_id)})


def _check_args(parser):
    _id_args(parser)
    parser.add_argument("--benchmark", default="sh.000300")


@command("book check", roles=ROLES, configure=_check_args, needs_store=False)
def book_check(args, ctx):
    report, markdown = build_report(show_snapshot(ctx.conn, args.snapshot_id), ctx.settings, args.benchmark)
    save_report(report, ctx.settings)
    return CommandResult(data={"report": report, "markdown": markdown})
