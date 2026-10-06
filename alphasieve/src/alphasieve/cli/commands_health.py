"""System health and compute resource commands."""

from alphasieve.cli.registry import CommandResult, command
from alphasieve.control import health, resources


@command("health show", ("human", "system"))
def health_show(args, ctx):
    return CommandResult(data=health.collect(ctx.settings, ctx.conn))


def _resource_args(parser):
    parser.add_argument("--probe", action="store_true", help="run live probes")


@command("resources show", ("human", "system"), configure=_resource_args, needs_state=False)
def resources_show(args, ctx):
    return CommandResult(data=resources.snapshot(ctx.settings) if args.probe else
                         (resources.read_snapshot(ctx.settings) or {}))
