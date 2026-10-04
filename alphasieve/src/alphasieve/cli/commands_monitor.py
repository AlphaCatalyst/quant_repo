"""Monitoring and alert commands."""

from alphasieve.cli.registry import CommandResult, command
from alphasieve.errors import validation_error
from alphasieve.monitor import acknowledge, list_alerts, run


def _run_args(parser):
    parser.add_argument("--asof", help="evaluation date YYYY-MM-DD")


def _list_args(parser):
    parser.add_argument("--open", action="store_true", dest="open_only")
    parser.add_argument("--kind")


def _ack_args(parser):
    parser.add_argument("id")
    parser.add_argument("--note", required=True)


@command("monitor run", ("human", "system"), configure=_run_args)
def monitor_run(args, ctx):
    result = run(ctx.settings, ctx.conn, args.asof)
    return CommandResult(data={k: v for k, v in result.items() if k != "warnings"}, warnings=result["warnings"])


@command("monitor list", ("human", "system"), configure=_list_args)
def monitor_list(args, ctx):
    rows = list_alerts(ctx.conn, open_only=args.open_only, kind=args.kind)
    return CommandResult(data={"count": len(rows), "alerts": rows})


@command("monitor ack", ("human",), configure=_ack_args)
def monitor_ack(args, ctx):
    try:
        ack_id = acknowledge(ctx.conn, args.id, ctx.settings.user, args.note)
    except ValueError as exc:
        raise validation_error(str(exc)) from exc
    return CommandResult(data={"ack_id": ack_id, "alert_id": args.id})
