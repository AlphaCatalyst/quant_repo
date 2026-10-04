"""Personal holdings commands, restricted to human and system roles."""

import csv
import uuid
from datetime import date
from pathlib import Path

from alphasieve.cli.registry import CommandResult, command
from alphasieve.errors import validation_error
from alphasieve.portfolio_book import history
from alphasieve.portfolio_book.checkup import build_report, save_report
from alphasieve.portfolio_book.importer import DEFAULT_MAPPING, import_snapshot, list_snapshots, show_snapshot
from alphasieve.util import utcnow_iso

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


def _cashflow_args(parser):
    parser.add_argument("action", choices=("add", "list"))
    parser.add_argument("--account")
    parser.add_argument("--file", help="CSV with account,as_of,amount[,note]")
    parser.add_argument("--as-of")
    parser.add_argument("--amount", type=float)
    parser.add_argument("--note", default="")


@command("book cashflow", roles=ROLES, configure=_cashflow_args, needs_store=False)
def book_cashflow(args, ctx):
    if args.action == "list":
        query = "SELECT * FROM book_cashflows"
        params = ()
        if args.account:
            query += " WHERE account=?"
            params = (args.account,)
        rows = ctx.conn.execute(query + " ORDER BY as_of, created_at", params).fetchall()
        return CommandResult(data={"cashflows": [dict(row) for row in rows]})
    if args.file:
        with Path(args.file).open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if not rows:
            raise validation_error("cash-flow CSV is empty")
    else:
        rows = [{"account": args.account, "as_of": args.as_of, "amount": args.amount, "note": args.note}]
    inserted = []
    for row in rows:
        account, as_of = str(row.get("account") or "").strip(), str(row.get("as_of") or "").strip()
        try:
            date.fromisoformat(as_of)
            amount = float(row["amount"])
        except (TypeError, ValueError) as exc:
            raise validation_error("cash flow needs YYYY-MM-DD and finite amount") from exc
        if not account or not (-float("inf") < amount < float("inf")):
            raise validation_error("cash flow needs account and finite amount")
        inserted.append((uuid.uuid4().hex, account, as_of, amount, str(row.get("note") or ""),
                         ctx.settings.user, utcnow_iso()))
    with ctx.conn:
        ctx.conn.executemany("INSERT INTO book_cashflows VALUES (?,?,?,?,?,?,?)", inserted)
    return CommandResult(data={"cashflows": [dict(zip(("cashflow_id", "account", "as_of", "amount", "note",
                                                       "created_by", "created_at"), row, strict=True))
                                              for row in inserted]})


def _history_args(parser):
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--account")
    parser.add_argument("--benchmark", default="sh.000300")


@command("book history", roles=ROLES, configure=_history_args, needs_store=False)
def book_history(args, ctx):
    report = history.build_history(ctx.conn, ctx.settings, args.start, args.end, args.account, args.benchmark)
    path = history.save_analysis_report(report, ctx.settings, "history")
    return CommandResult(data={"report": report, "report_path": str(path)})


def _attribution_args(parser):
    _history_args(parser)
    parser.add_argument("--by", choices=("position", "industry", "thesis"), default="position")


@command("book attribution", roles=ROLES, configure=_attribution_args, needs_store=False)
def book_attribution(args, ctx):
    from alphasieve.portfolio_book import attribution
    if not args.start or not args.end:
        raise validation_error("attribution requires --start and --end")
    report = attribution.build_attribution(ctx.conn, ctx.settings, args.start, args.end, args.by,
                                           args.account, args.benchmark)
    path = history.save_analysis_report(report, ctx.settings, f"attribution-{args.by}")
    return CommandResult(data={"report": report, "report_path": str(path)})


def _rebalance_args(parser):
    parser.add_argument("--snapshot")


@command("book rebalance", roles=ROLES, configure=_rebalance_args, needs_store=False)
def book_rebalance(args, ctx):
    from alphasieve.portfolio_book import rebalance
    report = rebalance.build_rebalance(ctx.conn, ctx.settings, args.snapshot)
    path = history.save_analysis_report(report, ctx.settings, "rebalance")
    return CommandResult(data={"report": report, "report_path": str(path)})
