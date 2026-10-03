"""Forecast ledger and decision journal commands."""

from pathlib import Path

import yaml

from alphasieve.cli.registry import CommandResult, command
from alphasieve.errors import validation_error

READ_ROLES = ("human", "agent", "system")


def _load_yaml(path: str) -> dict:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise validation_error(f"cannot read YAML file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise validation_error(f"YAML file {path} must contain a mapping")
    return data


def _file(p):
    p.add_argument("--file", required=True)


def _id(p):
    p.add_argument("id")


def _settle(p):
    _id(p)
    p.add_argument("--value", type=float)
    p.add_argument("--source")


def _void(p):
    _id(p)
    p.add_argument("--reason", required=True)


def _forecast_list(p):
    p.add_argument("--thesis")
    p.add_argument("--status", choices=("open", "settled", "voided"))


def _thesis(p):
    p.add_argument("--thesis")


@command("forecast add", ("human",), configure=_file)
def forecast_add(args, ctx):
    from alphasieve.forecasts.service import register_forecast

    return CommandResult(data=register_forecast(ctx.conn, _load_yaml(args.file), ctx.settings.role))


@command("forecast settle", ("human", "system"), configure=_settle)
def forecast_settle(args, ctx):
    from alphasieve.forecasts.service import settle_forecast

    return CommandResult(data=settle_forecast(ctx.conn, args.id, ctx.settings.role,
                                               value=args.value, source=args.source))


@command("forecast void", ("human",), configure=_void)
def forecast_void(args, ctx):
    from alphasieve.forecasts.service import void_forecast

    return CommandResult(data=void_forecast(ctx.conn, args.id, args.reason, ctx.settings.role))


@command("forecast list", READ_ROLES, configure=_forecast_list)
def forecast_list(args, ctx):
    from alphasieve.forecasts.service import list_forecasts

    return CommandResult(data=list_forecasts(ctx.conn, thesis_id=args.thesis, status=args.status))


@command("forecast show", READ_ROLES, configure=_id)
def forecast_show(args, ctx):
    from alphasieve.forecasts.service import show_forecast

    return CommandResult(data=show_forecast(ctx.conn, args.id))


@command("forecast score", READ_ROLES, configure=_thesis)
def forecast_score(args, ctx):
    from alphasieve.forecasts.service import score_forecasts

    return CommandResult(data=score_forecasts(ctx.conn, thesis_id=args.thesis))


@command("forecast verify", READ_ROLES)
def forecast_verify(args, ctx):
    from alphasieve.forecasts.service import verify_forecasts

    return CommandResult(data=verify_forecasts(ctx.conn))


@command("journal add", ("human",), configure=_file)
def journal_add(args, ctx):
    from alphasieve.journal import add_entry

    return CommandResult(data=add_entry(ctx.conn, _load_yaml(args.file), ctx.settings.role))


@command("journal list", READ_ROLES, configure=_thesis)
def journal_list(args, ctx):
    from alphasieve.journal import list_entries

    entries = list_entries(ctx.conn, thesis_id=args.thesis)
    return CommandResult(data={"entries": entries, "count": len(entries)})


@command("journal show", READ_ROLES, configure=_id)
def journal_show(args, ctx):
    from alphasieve.journal import show_entry

    return CommandResult(data=show_entry(ctx.conn, args.id))


@command("journal verify", READ_ROLES)
def journal_verify(args, ctx):
    from alphasieve.journal import verify_entries

    return CommandResult(data=verify_entries(ctx.conn))
