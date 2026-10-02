from pathlib import Path

from alphasieve.approvals import create_challenge
from alphasieve.cli.registry import CommandResult, command


def _configure(p):
    p.add_argument("kind", choices=["strategy_holdout", "factor_holdout", "review", "request"])
    p.add_argument("target_id")
    p.add_argument("--decision", required=True)
    p.add_argument("--output", type=Path)


@command(
    "approval challenge",
    ("human",),
    configure=_configure,
    help="write a short-lived SSH signing challenge for one human decision",
)
def cmd_approval_challenge(args, ctx) -> CommandResult:
    return CommandResult(
        data=create_challenge(ctx.conn, ctx.settings, args.kind, args.target_id, args.decision, args.output)
    )
