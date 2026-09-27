import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from alphasieve.campaigns import lifecycle, memory, service, stats
from alphasieve.cli.registry import CommandResult, command
from alphasieve.contracts import Campaign
from alphasieve.errors import AlphaSieveError, validation_error

ALL = ("agent", "human", "system")
HUMAN = ("human",)
HUMAN_SYSTEM = ("human", "system")
AGENT_HIDDEN_STATUS = {"awaiting_holdout_approval": "concluding", "holdout_evaluated": "concluding"}


def _campaign_arg(p, required: bool = False):
    if required:
        p.add_argument("campaign")
    else:
        p.add_argument("campaign", nargs="?", default=None)


def _resolve(args, ctx) -> str:
    campaign_id = getattr(args, "campaign", None) or ctx.settings.campaign
    if ctx.settings.role == "agent" and campaign_id != ctx.settings.campaign:
        raise AlphaSieveError("PERMISSION_DENIED", "agent may only read its assigned campaign")
    if not campaign_id:
        raise validation_error("campaign id required (argument or ALPHASIEVE_CAMPAIGN)")
    return campaign_id


def _configure_create(p):
    p.add_argument("spec", help="path to a Campaign YAML file")


@command("campaign create", HUMAN, configure=_configure_create, help="create a campaign from a YAML spec")
def cmd_campaign_create(args, ctx) -> CommandResult:
    path = Path(args.spec)
    if not path.exists():
        raise validation_error(f"spec file {args.spec} not found")
    try:
        spec = Campaign(**yaml.safe_load(path.read_text(encoding="utf-8")))
    except (yaml.YAMLError, TypeError, ValidationError) as exc:
        raise validation_error(f"invalid campaign spec: {exc}") from None
    campaign = service.create_campaign(ctx.conn, ctx.settings, spec)
    return CommandResult(data=_public(campaign, ctx.settings.role))


def _public(campaign: dict, role: str) -> dict:
    out = {**campaign, "spec": campaign["spec"].model_dump()}
    if role == "agent":
        out["status"] = AGENT_HIDDEN_STATUS.get(out["status"], out["status"])
    return out


@command("campaign list", ALL, help="list campaigns")
def cmd_campaign_list(args, ctx) -> CommandResult:
    rows = service.list_campaigns(ctx.conn)
    if ctx.settings.role == "agent":
        rows = [{**r, "status": AGENT_HIDDEN_STATUS.get(r["status"], r["status"])} for r in rows
                if r["campaign_id"] == ctx.settings.campaign]
    return CommandResult(data={"campaigns": rows, "count": len(rows)})


@command("campaign status", ALL, configure=_campaign_arg, help="campaign status, budgets and funnel")
def cmd_campaign_status(args, ctx) -> CommandResult:
    campaign_id = _resolve(args, ctx)
    campaign = service.get_campaign(ctx.conn, campaign_id)
    agent = ctx.settings.role == "agent"
    data = {
        "campaign": _public(campaign, ctx.settings.role),
        "budgets": stats.budget_status(ctx.conn, campaign_id),
        "funnel": stats.funnel(ctx.conn, campaign_id, include_holdout=not agent),
        "recent": stats.recent_outcomes(ctx.conn, campaign_id),
        "pending_directives": len(service.directives(ctx.conn, campaign_id, "pending")),
    }
    if not agent:
        data["open_requests"] = [dict(r) for r in ctx.conn.execute(
            "SELECT request_id, kind, content, created_at FROM agent_requests"
            " WHERE campaign_id = ? AND status = 'open'",
            (campaign_id,))]
        data["holdout_requests"] = [dict(r) for r in ctx.conn.execute(
            "SELECT request_id, shortlist_id, status, created_at, decided_at FROM holdout_requests"
            " WHERE campaign_id = ?", (campaign_id,))]
    return CommandResult(data=data)


@command("campaign intensity", ALL, configure=_campaign_arg,
         help="best dev ICIR versus the expected maximum under the null, by trial count")
def cmd_campaign_intensity(args, ctx) -> CommandResult:
    points = stats.search_intensity(ctx.conn, _resolve(args, ctx))
    return CommandResult(data={"points": points})


def _configure_transition(p):
    _campaign_arg(p, required=True)
    p.add_argument("--reason", default=None)


def _transition(target: str):
    def handler(args, ctx) -> CommandResult:
        return CommandResult(data=_public(service.set_status(ctx.conn, ctx.settings, args.campaign, target,
                                                             args.reason), ctx.settings.role))
    return handler


command("campaign start", HUMAN_SYSTEM, configure=_configure_transition, help="start a draft campaign")(
    _transition("running"))
command("campaign pause", HUMAN_SYSTEM, configure=_configure_transition, help="pause a running campaign")(
    _transition("paused"))
command("campaign resume", HUMAN_SYSTEM, configure=_configure_transition, help="resume a paused campaign")(
    _transition("running"))


@command("campaign stop", HUMAN, configure=_configure_transition, help="end a campaign without a holdout read")
def cmd_campaign_stop(args, ctx) -> CommandResult:
    campaign = service.get_campaign(ctx.conn, args.campaign)
    if campaign["status"] in ("running", "paused"):
        service.set_status(ctx.conn, ctx.settings, args.campaign, "concluding", args.reason)
    campaign = service.set_status(ctx.conn, ctx.settings, args.campaign, "concluded", args.reason or "stopped")
    return CommandResult(data=_public(campaign, ctx.settings.role))


@command("campaign conclude", HUMAN_SYSTEM, configure=_configure_transition, needs_store=True,
         help="run L3, lock the shortlist, freeze memory and create a holdout request")
def cmd_campaign_conclude(args, ctx) -> CommandResult:
    return CommandResult(data=lifecycle.conclude(ctx.conn, ctx.settings, args.campaign, args.reason or "manual"))


@command("campaign turns", HUMAN_SYSTEM, configure=_campaign_arg, help="turn history")
def cmd_campaign_turns(args, ctx) -> CommandResult:
    rows = [dict(r) for r in ctx.conn.execute("SELECT * FROM turns WHERE campaign_id = ? ORDER BY turn_index",
                                               (_resolve(args, ctx),))]
    for r in rows:
        r["usage"] = json.loads(r.pop("usage_json") or "{}")
    return CommandResult(data={"turns": rows, "count": len(rows)})


def _configure_directive(p):
    _campaign_arg(p, required=True)
    p.add_argument("--kind", required=True, choices=["prioritize", "forbid", "hint", "answer"])
    p.add_argument("--content", required=True)


@command("directive add", HUMAN, configure=_configure_directive, help="steer the next agent turn")
def cmd_directive_add(args, ctx) -> CommandResult:
    return CommandResult(data=service.add_directive(ctx.conn, ctx.settings, args.campaign, args.kind, args.content))


def _configure_directive_list(p):
    _campaign_arg(p, required=True)
    p.add_argument("--status", default=None, choices=["pending", "consumed"])


@command("directive list", HUMAN_SYSTEM, configure=_configure_directive_list, help="list directives")
def cmd_directive_list(args, ctx) -> CommandResult:
    rows = service.directives(ctx.conn, args.campaign, args.status)
    return CommandResult(data={"directives": rows, "count": len(rows)})


def _configure_request(p):
    p.add_argument("--kind", required=True, choices=["data", "question", "scope"])
    p.add_argument("--content", required=True)


@command("request create", ("agent",), configure=_configure_request, help="ask the researcher for data or scope")
def cmd_request_create(args, ctx) -> CommandResult:
    return CommandResult(data=service.create_request(ctx.conn, ctx.settings, _resolve(args, ctx), args.kind,
                                                     args.content))


def _configure_request_list(p):
    p.add_argument("--campaign", default=None)
    p.add_argument("--status", default="open")


@command("request list", HUMAN_SYSTEM, configure=_configure_request_list, help="researcher inbox")
def cmd_request_list(args, ctx) -> CommandResult:
    query, params = "SELECT * FROM agent_requests WHERE 1 = 1", []
    if args.campaign:
        query += " AND campaign_id = ?"
        params.append(args.campaign)
    if args.status != "all":
        query += " AND status = ?"
        params.append(args.status)
    rows = [dict(r) for r in ctx.conn.execute(query + " ORDER BY created_at", params)]
    return CommandResult(data={"requests": rows, "count": len(rows)})


def _configure_respond(p):
    p.add_argument("request_id")
    p.add_argument("--decision", required=True, choices=["approved", "rejected", "answered"])
    p.add_argument("--response", required=True)


@command("request respond", HUMAN, configure=_configure_respond, help="answer an agent request")
def cmd_request_respond(args, ctx) -> CommandResult:
    return CommandResult(data=service.respond_request(ctx.conn, ctx.settings, args.request_id, args.decision,
                                                      args.response))


@command("memory show", ("agent", "human"), configure=_campaign_arg, help="campaign memory (dev evidence only)")
def cmd_memory_show(args, ctx) -> CommandResult:
    campaign_id = _resolve(args, ctx)
    return CommandResult(data={"markdown": memory.render(ctx.conn, campaign_id),
                               "derived": memory.derive(ctx.conn, campaign_id),
                               "insights": memory.insights(ctx.conn, campaign_id)})


@command("holdout list", HUMAN, help="holdout requests")
def cmd_holdout_list(args, ctx) -> CommandResult:
    rows = [dict(r) for r in ctx.conn.execute("SELECT * FROM holdout_requests ORDER BY created_at")]
    for r in rows:
        r["result"] = json.loads(r.pop("result_json") or "null")
        shortlist = ctx.conn.execute("SELECT members_json FROM shortlists WHERE shortlist_id = ?",
                                     (r["shortlist_id"],)).fetchone()
        r["members"] = json.loads(shortlist["members_json"]) if shortlist else []
    return CommandResult(data={"requests": rows, "count": len(rows)})


def _configure_holdout_decide(p):
    p.add_argument("request_id")
    p.add_argument("--reason", required=True)


@command("holdout approve", HUMAN, configure=_configure_holdout_decide, needs_store=True,
         help="approve one holdout read for a locked shortlist (human only)")
def cmd_holdout_approve(args, ctx) -> CommandResult:
    return CommandResult(data=lifecycle.approve_holdout(ctx.conn, ctx.settings, args.request_id, args.reason))


@command("holdout reject", HUMAN, configure=_configure_holdout_decide, help="reject a holdout request")
def cmd_holdout_reject(args, ctx) -> CommandResult:
    return CommandResult(data=lifecycle.reject_holdout(ctx.conn, ctx.settings, args.request_id, args.reason))


@command("review list", HUMAN, help="review packets")
def cmd_review_list(args, ctx) -> CommandResult:
    rows = [dict(r) for r in ctx.conn.execute("SELECT * FROM review_packets ORDER BY created_at")]
    return CommandResult(data={"packets": rows, "count": len(rows)})


def _configure_packet(p):
    p.add_argument("packet_id")


@command("review show", HUMAN, configure=_configure_packet, needs_store=True, help="show a review packet")
def cmd_review_show(args, ctx) -> CommandResult:
    row = ctx.conn.execute("SELECT * FROM review_packets WHERE packet_id = ?", (args.packet_id,)).fetchone()
    if row is None:
        raise AlphaSieveError("NOT_FOUND", f"review packet {args.packet_id} not found")
    packet = json.loads((ctx.settings.artifacts_dir / row["artifact_id"] / "metrics.json").read_text(encoding="utf-8"))
    return CommandResult(data={**dict(row), "packet": packet})


def _configure_review_decide(p):
    p.add_argument("packet_id")
    p.add_argument("--decision", required=True, choices=sorted(lifecycle.REVIEW_DECISIONS))
    p.add_argument("--reason", required=True)


@command("review decide", HUMAN, configure=_configure_review_decide, needs_store=True,
         help="record a review decision (human only)")
def cmd_review_decide(args, ctx) -> CommandResult:
    return CommandResult(data=lifecycle.decide_review(ctx.conn, ctx.settings, args.packet_id, args.decision,
                                                      args.reason))


def _configure_orchestrator(p):
    _campaign_arg(p, required=True)
    p.add_argument("--max-turns", type=int, default=None, help="stop this run after N turns (campaign continues)")


@command("orchestrator run", HUMAN_SYSTEM, configure=_configure_orchestrator, needs_store=True,
         help="run agent turns until a stop condition, then conclude the campaign")
def cmd_orchestrator_run(args, ctx) -> CommandResult:
    from alphasieve.agents.orchestrator import run_campaign

    return CommandResult(data=run_campaign(ctx.settings, args.campaign, args.max_turns))


@command("orchestrator report", HUMAN_SYSTEM, configure=_configure_transition, needs_store=True,
         help="write the campaign status report")
def cmd_orchestrator_report(args, ctx) -> CommandResult:
    from alphasieve.agents.orchestrator import write_report

    return CommandResult(data={"report": write_report(ctx.settings, ctx.conn, args.campaign)})


def _configure_search(p):
    _campaign_arg(p, required=True)
    p.add_argument("--trials", type=int, default=100)
    p.add_argument("--method", default="random", choices=["random", "evolve"])
    p.add_argument("--population", type=int, default=24)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--seed", type=int, default=0)


@command("search run", HUMAN_SYSTEM, configure=_configure_search, needs_store=True,
         help="programmatic search in a 'program' campaign; evaluations go through the evaluation queue")
def cmd_search_run(args, ctx) -> CommandResult:
    from alphasieve.evaluation.service import queue_executor
    from alphasieve.search.generator import run_search

    return CommandResult(data=run_search(ctx.settings, args.campaign, args.trials, args.method, args.population,
                                         args.concurrency, args.seed, executor=queue_executor(ctx.settings)))
