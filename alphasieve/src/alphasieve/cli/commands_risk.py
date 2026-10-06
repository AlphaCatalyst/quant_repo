"""Report-only rm1 CLI commands. No strategy execution or research trial is started."""

import os
from pathlib import Path

from alphasieve.cli.registry import CommandResult, command
from alphasieve.errors import validation_error

ALL = ("agent", "human", "system")
REMOTE_ROOT = Path(os.environ.get("ALPHASIEVE_REMOTE_ROOT", "/taijifs_zw35/r2/felixjjiang/alphasieve"))


def _report_args(p):
    p.add_argument("--trial", required=True)
    p.add_argument("--model", default="rm1")
    p.add_argument("--tier", default="dev")


@command(
    "risk report",
    ALL,
    configure=_report_args,
    needs_store=True,
    help="build an independent dev risk report for saved trial outputs",
)
def cmd_risk_report(args, ctx) -> CommandResult:
    from alphasieve.training.risk_report import build_report, write_risk_artifact

    if args.tier != "dev":
        raise validation_error("risk report only accepts --tier dev")
    manifest, tables, validation = build_report(ctx.settings, args.trial, tier=args.tier, model=args.model)
    aid = write_risk_artifact(ctx.settings, manifest, tables, validation)
    return CommandResult(
        data={
            "risk_artifact_id": aid,
            "parent_trial_id": args.trial,
            "validation": validation,
            "industry_source": "sw1_pit",
            "risk_spec_hash": manifest["risk_spec_hash"],
        },
        artifacts=[{"artifact_id": aid, "kind": "risk_report"}],
        warnings=validation["warnings"],
    )


def _validate_args(p):
    p.add_argument("--risk-artifact", required=True)


@command(
    "risk validate",
    ALL,
    configure=_validate_args,
    needs_store=True,
    help="verify a saved risk artifact and its fixed bias evidence",
)
def cmd_risk_validate(args, ctx) -> CommandResult:
    from alphasieve.training.risk_report import read_risk_artifact

    manifest, validation = read_risk_artifact(ctx.settings, args.risk_artifact)
    return CommandResult(
        data={
            "risk_artifact_id": args.risk_artifact,
            "model": manifest["model"],
            "verdict": validation["status"],
            "validation": validation,
        },
        warnings=validation.get("warnings", []),
    )


def _show_args(p):
    p.add_argument("--artifact", required=True)


@command("risk show", ALL, configure=_show_args, needs_store=True, help="show a verified risk artifact summary")
def cmd_risk_show(args, ctx) -> CommandResult:
    from alphasieve.training.risk_report import read_risk_artifact

    manifest, validation = read_risk_artifact(ctx.settings, args.artifact)
    return CommandResult(
        data={"risk_artifact_id": args.artifact, "manifest": manifest, "validation": validation},
        warnings=validation.get("warnings", []),
    )
