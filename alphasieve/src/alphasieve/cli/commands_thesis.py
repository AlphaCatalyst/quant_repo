"""Read-only thesis inspection and deterministic valuation commands."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from alphasieve.cli.registry import CommandResult, command
from alphasieve.errors import validation_error
from alphasieve.thesis import evaluate_scenarios, implied, load_thesis
from alphasieve.thesis.agent_run import DEFAULT_EFFORT, DEFAULT_MODEL, list_runs, run_draft, run_review, show_run
from alphasieve.thesis.formula import FormulaError

ROLES = ("human", "agent", "system")
DEFAULT_DIR = Path(__file__).resolve().parents[3] / "theses"


def _file(p):
    p.add_argument("file", help="thesis YAML file")


def _load(file):
    try:
        return load_thesis(file)
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
        raise validation_error(f"invalid thesis {file}: {exc}") from None


@command("thesis validate", ROLES, configure=_file, needs_state=False, help="validate a thesis YAML file")
def validate(args, ctx) -> CommandResult:
    thesis = _load(args.file)
    return CommandResult(data={"thesis_id": thesis.thesis_id, "valid": True})


@command("thesis scenarios", ROLES, configure=_file, needs_state=False,
         help="evaluate thesis scenarios and sensitivity")
def scenarios(args, ctx) -> CommandResult:
    thesis = _load(args.file)
    try:
        result = evaluate_scenarios(thesis)
    except FormulaError as exc:
        raise validation_error(str(exc)) from None
    return CommandResult(data={"thesis_id": thesis.thesis_id, **result})


def _implied_args(p):
    _file(p)
    p.add_argument("--param", required=True)
    p.add_argument("--target", type=float)


@command("thesis implied", ROLES, configure=_implied_args, needs_state=False,
         help="solve the parameter implied by a target valuation")
def implied_command(args, ctx) -> CommandResult:
    thesis = _load(args.file)
    try:
        result = implied(thesis, args.param, args.target)
    except FormulaError as exc:
        raise validation_error(str(exc)) from None
    return CommandResult(data={"thesis_id": thesis.thesis_id, **result})


@command("thesis show", ROLES, configure=_file, needs_state=False, help="show a validated thesis")
def show(args, ctx) -> CommandResult:
    return CommandResult(data=_load(args.file).model_dump(mode="json"))


def _list_args(p):
    p.add_argument("--dir", default=str(DEFAULT_DIR))


@command("thesis list", ROLES, configure=_list_args, needs_state=False, help="list theses in a directory")
def list_theses(args, ctx) -> CommandResult:
    directory = Path(args.dir)
    if not directory.is_dir():
        raise validation_error(f"thesis directory {directory} not found")
    rows = []
    for path in sorted({*directory.glob("*.yaml"), *directory.glob("*.yml")}):
        thesis = _load(path)
        rows.append({"thesis_id": thesis.thesis_id, "title": thesis.title,
                     "status": thesis.status, "as_of": thesis.as_of.isoformat(), "file": str(path)})
    return CommandResult(data={"theses": rows, "count": len(rows)})


def _run_args(p):
    p.add_argument("--harness", choices=("codex", "claude"), default="codex")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--effort", default=DEFAULT_EFFORT)
    p.add_argument("--timeout-min", type=int, default=30)


def _draft_args(p):
    p.add_argument("--topic", required=True)
    _run_args(p)


def _review_args(p):
    _file(p)
    _run_args(p)


@command("thesis draft", ("human",), configure=_draft_args, help="research a thesis draft")
def draft(args, ctx) -> CommandResult:
    if args.timeout_min <= 0:
        raise validation_error("--timeout-min must be positive")
    return CommandResult(data=run_draft(ctx.settings, args.topic, args.harness, args.model,
                                        args.effort, args.timeout_min * 60))


@command("thesis review", ("human",), configure=_review_args, help="review a thesis")
def review(args, ctx) -> CommandResult:
    if args.timeout_min <= 0:
        raise validation_error("--timeout-min must be positive")
    if not Path(args.file).is_file():
        raise validation_error(f"thesis file {args.file} not found")
    return CommandResult(data=run_review(ctx.settings, args.file, args.harness, args.model,
                                         args.effort, args.timeout_min * 60))


def _runs_args(p):
    p.add_argument("--limit", type=int, default=20)


@command("thesis runs", ("human", "system"), configure=_runs_args, help="list thesis agent runs")
def runs(args, ctx) -> CommandResult:
    if args.limit <= 0:
        raise validation_error("--limit must be positive")
    rows = list_runs(ctx.settings, args.limit)
    return CommandResult(data={"runs": rows, "count": len(rows)})


def _run_show_args(p):
    p.add_argument("run_id")


@command("thesis run-show", ("human", "system"), configure=_run_show_args, help="show a thesis agent run")
def run_show(args, ctx) -> CommandResult:
    try:
        return CommandResult(data=show_run(ctx.settings, args.run_id))
    except ValueError as exc:
        raise validation_error(str(exc)) from None
