"""Financial red-flag screening commands."""

import json

from alphasieve.cli.registry import CommandResult, command
from alphasieve.errors import validation_error
from alphasieve.redflag import explain, flags_for, rule_catalog, scan
from alphasieve.redflag.service import universe_codes

READ_ROLES = ("human", "agent", "system")


def _scan_args(p):
    p.add_argument("--asof", required=True)
    p.add_argument("--jobs", type=int, default=2, help="maximum native compute threads (default: 2)")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--codes", help="comma-separated BaoStock-style codes")
    group.add_argument("--universe", choices=("csi800", "all"), default="csi800")


def _show_args(p):
    p.add_argument("code")
    p.add_argument("--asof", required=True)


def _explain_args(p):
    p.add_argument("code")
    p.add_argument("--asof", help="scan date; latest saved scan if omitted")


def _saved(settings, code, asof=None):
    root = settings.hot_root / "redflag"
    folders = [root / asof] if asof else sorted(root.glob("????-??-??"), reverse=True)
    for folder in folders:
        path = folder / "results.json"
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            for row in payload.get("results", []):
                if row["code"] == code:
                    return row
    return None


@command("redflag scan", READ_ROLES, configure=_scan_args, needs_state=False)
def redflag_scan(args, ctx):
    from threadpoolctl import threadpool_limits

    if args.jobs < 1 or args.jobs > 32:
        raise validation_error("--jobs must be between 1 and 32")
    codes = (
        [x.strip() for x in args.codes.split(",") if x.strip()]
        if args.codes
        else universe_codes(ctx.settings, args.universe, args.asof)
    )
    if not codes:
        raise validation_error("no codes to screen")
    with threadpool_limits(limits=args.jobs):
        result = scan(ctx.settings, codes, args.asof, "codes" if args.codes else args.universe)
    return CommandResult(data=result)


@command("redflag show", READ_ROLES, configure=_show_args, needs_state=False)
def redflag_show(args, ctx):
    row = _saved(ctx.settings, args.code, args.asof)
    if row is None:
        row = flags_for(ctx.settings, [args.code], args.asof)[0]
    return CommandResult(data=row)


@command("redflag explain", READ_ROLES, configure=_explain_args, needs_state=False)
def redflag_explain(args, ctx):
    row = _saved(ctx.settings, args.code, args.asof)
    if row is None and args.asof:
        row = flags_for(ctx.settings, [args.code], args.asof)[0]
    if row is None:
        raise validation_error(f"no saved redflag scan for {args.code}; supply --asof")
    return CommandResult(data={"code": args.code, "asof": row["asof"], "markdown": explain(row)})


@command("redflag rules", READ_ROLES, needs_state=False)
def redflag_rules(args, ctx):
    return CommandResult(data={"rules": rule_catalog()})
