import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from alphasieve.campaigns.service import masked_state
from alphasieve.cli.registry import CommandResult, command
from alphasieve.contracts import FactorSpec
from alphasieve.data.access import load_panel
from alphasieve.errors import AlphaSieveError, validation_error
from alphasieve.factors import library as lib
from alphasieve.factors.dsl import DSLError, compile_expression, evaluate
from alphasieve.factors.registry import get_factor, list_factors
from alphasieve.search_space import load_search_space

AGENT_HUMAN = ("agent", "human")
HUMAN_SYSTEM = ("human", "system")


def load_spec(path: str) -> FactorSpec:
    p = Path(path)
    if not p.exists():
        raise validation_error(f"spec file {path} not found")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        return FactorSpec(**data)
    except (yaml.YAMLError, TypeError, ValidationError) as exc:
        raise validation_error(f"invalid factor spec: {exc}") from None


def _configure_spec(p):
    p.add_argument("spec", help="path to a FactorSpec YAML file")


@command("factor validate", AGENT_HUMAN, configure=_configure_spec, help="run L0 only (no trial is recorded)")
def cmd_factor_validate(args, ctx) -> CommandResult:
    spec = load_spec(args.spec)
    space = load_search_space(ctx.settings)
    try:
        compiled = compile_expression(spec.expression, space)
    except DSLError as exc:
        raise AlphaSieveError("VALIDATION_ERROR", "L0 structural validation failed",
                              {"issues": [{"name": i.name, "message": i.message} for i in exc.issues]}) from None
    cell = space.check_cell(spec.cell.model_dump(), compiled.terminals, compiled.max_window)
    return CommandResult(
        data={"canonical": compiled.canonical, "candidate_hash": compiled.candidate_hash, "nodes": compiled.nodes,
              "depth": compiled.depth, "terminals": sorted(compiled.terminals), "windows": compiled.windows,
              "cell": cell},
        warnings=cell["warnings"],
    )


def _configure_eval(p):
    _configure_spec(p)
    p.add_argument("--campaign", default=None)


@command("factor eval", AGENT_HUMAN, configure=_configure_eval, needs_store=True,
         help="evaluate a candidate through L0-L2 and record the trial")
def cmd_factor_eval(args, ctx) -> CommandResult:
    from alphasieve.evaluation.evaluate import evaluate_spec

    spec = load_spec(args.spec)
    campaign_id = args.campaign or ctx.settings.campaign
    if ctx.settings.role == "agent" and campaign_id != ctx.settings.campaign:
        raise AlphaSieveError("PERMISSION_DENIED", "agent may only evaluate inside its assigned campaign")
    result = evaluate_spec(ctx.settings, ctx.conn, spec, campaign_id=campaign_id)
    error = None
    if result["outcome"] != "robust_passed":
        failed = [f"{lvl}.{c['name']}" for lvl, g in result["gates"].items() for c in g["checks"] if not c["passed"]]
        error = AlphaSieveError("GATE_FAILED", f"candidate did not pass: {result['outcome']}", {"failed": failed})
    artifacts = [{"artifact_id": result["artifact_id"], "kind": "factor_eval"}] if result.get("artifact_id") else []
    return CommandResult(data=result, artifacts=artifacts, warnings=result["cell"].get("warnings", []), error=error)


def _configure_show(p):
    p.add_argument("ref", help="factor id, optionally with @version")


@command("factor show", AGENT_HUMAN, configure=_configure_show, help="factor definition, state and dev evidence")
def cmd_factor_show(args, ctx) -> CommandResult:
    factor = get_factor(ctx.conn, args.ref)
    trials = [dict(r) for r in ctx.conn.execute(
        "SELECT trial_id, outcome, metrics_json, artifact_id, created_at, campaign_id FROM trials"
        " WHERE factor_id = ? AND version = ? AND record_kind = 'completed' AND evidence_tier = 'dev' ORDER BY seq",
        (factor["factor_id"], factor["version"]))]
    for t in trials:
        t["metrics"] = json.loads(t.pop("metrics_json"))
    factor["state"] = masked_state(factor["state"], ctx.settings.role)
    return CommandResult(data={**factor, "dev_trials": trials, "trial_count": len(trials)})


def _configure_list(p):
    p.add_argument("--state", default=None)
    p.add_argument("--limit", type=int, default=100)


@command("factor list", AGENT_HUMAN, configure=_configure_list, help="list factors")
def cmd_factor_list(args, ctx) -> CommandResult:
    limit = min(args.limit, 1000)
    if ctx.settings.role == "agent":
        rows = [{**r, "state": masked_state(r["state"], "agent")} for r in list_factors(ctx.conn, None, 100_000)]
        rows = [r for r in rows if args.state is None or r["state"] == args.state][:limit]
    else:
        rows = list_factors(ctx.conn, args.state, limit)
    return CommandResult(data={"factors": rows, "count": len(rows)})


@command("library seed", HUMAN_SYSTEM, needs_store=True, help="load the seed factors as the baseline library")
def cmd_library_seed(args, ctx) -> CommandResult:
    panel = load_panel(ctx.settings, "dev")
    added = lib.seed_library(ctx.settings, ctx.conn, panel)
    return CommandResult(data={"seeded": added, "count": len(added)})


@command("library list", AGENT_HUMAN, help="list library members")
def cmd_library_list(args, ctx) -> CommandResult:
    members = lib.library_members(ctx.conn)
    rows = [{"name": m["name"], "factor_id": m["factor_id"], "version": m["version"],
             "expression": m["canonical_expression"], "direction": m["direction"],
             "ic_mean": m["metrics"].get("ic_mean"), "icir": m["metrics"].get("icir")} for m in members]
    return CommandResult(data={"members": rows, "count": len(rows)})


@command("library corr", AGENT_HUMAN, configure=_configure_show, help="correlation of a factor with the library")
def cmd_library_corr(args, ctx) -> CommandResult:
    from alphasieve.evaluation.metrics import library_correlations

    factor = get_factor(ctx.conn, args.ref)
    panel = load_panel(ctx.settings, "dev")
    compiled = compile_expression(factor["canonical_expression"], load_search_space(ctx.settings))
    values = evaluate(compiled, panel) * factor["spec"]["direction"]
    library = {k: v for k, v in lib.library_frames(ctx.settings, ctx.conn, panel).items()}
    start, end = panel.window
    universe = panel.mask("in_universe")
    universe.loc[(universe.index < start) | (universe.index > end)] = False
    corr = library_correlations(values, library, universe)
    return CommandResult(data={"factor": f"{factor['factor_id']}@{factor['version']}", **corr})


def _configure_calibrate(p):
    p.add_argument("--random", type=int, default=200, help="number of random expressions for the null simulation")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--horizon", type=int, default=5, choices=[1, 5, 10, 20])


@command("gate calibrate", HUMAN_SYSTEM, configure=_configure_calibrate, needs_store=True,
         help="null simulation, planted-signal detection and seed-based threshold suggestions")
def cmd_gate_calibrate(args, ctx) -> CommandResult:
    from alphasieve.artifacts import write_artifact
    from alphasieve.config import load_config
    from alphasieve.evaluation import calibration as cal
    from alphasieve.util import code_version, pretty_json

    policy = load_config(ctx.settings, "gate_policy")
    space = load_search_space(ctx.settings)
    panel = load_panel(ctx.settings, "dev")
    library = lib.library_frames(ctx.settings, ctx.conn, panel)
    report = {
        "gate_policy_version": policy["version"],
        "panel_signature": panel.signature,
        "null": cal.null_simulation(panel, space, policy, library, lib.library_icir(ctx.conn), args.random,
                                    args.seed, args.horizon),
        "planted": cal.planted_detection(panel, policy, seed=args.seed, horizon=args.horizon),
        "seeds": cal.seed_suggestions(lib.library_members(ctx.conn)),
    }
    manifest = {"kind": "gate_calibration", "gate_policy_version": policy["version"], "panel": panel.signature,
                "random": args.random, "seed": args.seed, "horizon": args.horizon, "code_version": code_version()}
    artifact_id = write_artifact(ctx.settings, manifest, metrics=report,
                                 report="# Gate calibration\n\n```json\n" + pretty_json(report) + "\n```\n")
    return CommandResult(data=report, artifacts=[{"artifact_id": artifact_id, "kind": "gate_calibration"}])
