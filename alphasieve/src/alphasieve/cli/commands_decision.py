from alphasieve.cli.registry import CommandResult, command

ALL = ("agent", "human", "system")


def _p3_args(parser):
    parser.add_argument("--smoke", action="store_true",
                        help="1000 accounts, 5 random draws, not recorded against the trial budget")


@command("decision p3", ALL, configure=_p3_args, help="P3 rebalance-band grid on dev synthetic accounts")
def cmd_decision_p3(args, ctx) -> CommandResult:
    from alphasieve.decisions import p3

    overrides = {"accounts.accounts": 1000, "random_reps": 5} if args.smoke else None
    report = p3.run(ctx.settings, ctx.conn, ctx.settings.user, overrides, record=not args.smoke)
    summary = [{"pool": p["pool"], "selected": p["selected"], "accepted": p["selected_acceptance"]["pass"],
                "no_action_ce4": p["no_action"]["ce_gamma4"],
                "selected_ce4": next(r["ce_gamma4"] for r in p["rules"] if r["rule_id"] == p["selected"])}
               for p in report["pools"]]
    return CommandResult(data={"run_id": report["run_id"], "report_path": report["report_path"], "pools": summary})


@command("decision trials", ALL, help="decision-layer trials recorded per task")
def cmd_decision_trials(args, ctx) -> CommandResult:
    from alphasieve.decisions import ledger

    trials = ledger.list_trials(ctx.conn)
    counts: dict[str, int] = {}
    for t in trials:
        counts[t["task"]] = counts.get(t["task"], 0) + 1
    return CommandResult(data={"counts": counts, "trials": trials})


def _probe_args(parser):
    parser.add_argument("name", choices=("p1", "p2", "p4", "p6", "p7"))


@command("decision probe", ALL, configure=_probe_args, help="dev feasibility probe for a decision task")
def cmd_decision_probe(args, ctx) -> CommandResult:
    import json

    from alphasieve.decisions.probes import run_probe

    report = {"probe": args.name, "tier": "dev", **run_probe(ctx.settings, args.name)}
    path = ctx.settings.hot_root / "reports" / "decisions" / f"probe-{args.name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, default=str), encoding="utf-8")
    return CommandResult(data={**report, "report_path": str(path)})
