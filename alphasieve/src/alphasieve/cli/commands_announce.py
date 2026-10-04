"""Company announcement commands over the local cninfo index."""

from alphasieve.announcements import evidence_pack, fetch, get_local, list_local, recent_for
from alphasieve.cli.registry import CommandResult, command
from alphasieve.errors import validation_error

ROLES = ("human", "system")


def _list_args(parser):
    parser.add_argument("--code")
    parser.add_argument("--since", help="inclusive YYYY-MM-DD publication date")
    parser.add_argument("--type", dest="event_type", help="event taxonomy key")
    parser.add_argument("--limit", type=int, default=100)


@command("announce list", ROLES, configure=_list_args, needs_state=False, help="list local cninfo announcements")
def cmd_list(args, ctx):
    rows = list_local(ctx.settings, code=args.code, since=args.since, event_type=args.event_type)
    return CommandResult(data={"count": len(rows), "rows": rows[:args.limit]})


def _id_arg(parser):
    parser.add_argument("id", help="cninfo announcement ID")


@command("announce show", ROLES, configure=_id_arg, needs_state=False,
         help="show announcement metadata and local text status")
def cmd_show(args, ctx):
    try:
        row = get_local(ctx.settings, args.id)
    except (KeyError, ValueError) as exc:
        raise validation_error(str(exc)) from exc
    row["fetched"] = (ctx.settings.raw_dir / "cninfo" / "documents" / args.id / "text.json").exists()
    return CommandResult(data=row)


@command("announce fetch", ROLES, configure=_id_arg, needs_state=False, help="download PDF and extract citeable text")
def cmd_fetch(args, ctx):
    try:
        result = fetch(ctx.settings, args.id)
    except (KeyError, ValueError) as exc:
        raise validation_error(str(exc)) from exc
    return CommandResult(data={"announcement_id": args.id, "pdf_path": result["pdf_path"],
                               "pdf_sha256": result["pdf_sha256"], "pages": len(result["pages"]),
                               "text_length": result["text_length"],
                               "evidence_pack": evidence_pack(ctx.settings, args.id, max_chars=3000)})


def _watch_args(parser):
    parser.add_argument("--codes", required=True, nargs="+", help="six-digit stock codes, space- or comma-separated")
    parser.add_argument("--since", help="inclusive YYYY-MM-DD publication date")


@command("announce watch", ROLES, configure=_watch_args, needs_state=False,
         help="recent important announcements for holdings")
def cmd_watch(args, ctx):
    codes = [part.strip() for group in args.codes for part in group.split(",") if part.strip()]
    rows = recent_for(ctx.settings, codes, args.since)
    return CommandResult(data={"count": len(rows), "rows": rows})
