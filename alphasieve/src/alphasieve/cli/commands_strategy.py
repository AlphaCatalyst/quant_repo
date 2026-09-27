from alphasieve.cli.registry import CommandResult, command

HUMAN_SYSTEM = ("human", "system")


def _configure_backtest(p):
    p.add_argument("--universe", default="csi800")
    p.add_argument("--horizon", type=int, default=20, choices=[1, 5, 10, 20])
    p.add_argument("--model", default="ridge", choices=["ridge", "lgbm"])
    p.add_argument("--retrain", default="monthly", choices=["monthly", "yearly"])
    p.add_argument("--train-years", type=int, default=5)
    p.add_argument("--warmup-years", type=int, default=2, help="dev years before the first out-of-sample score")
    p.add_argument("--rebalance-every", type=int, default=5)
    p.add_argument("--industry-dev", type=float, default=0.03)
    p.add_argument("--name-cap", type=float, default=0.02)
    p.add_argument("--turnover-cap", type=float, default=0.30)
    p.add_argument("--size-limit", type=float, default=0.3, help="max |active size exposure| in cross-sectional sd")
    p.add_argument("--factors", default=None, help="comma-separated factor ids (default: the library)")
    p.add_argument("--seed-library", action="store_true", help="seed the library first if it is empty")
    p.add_argument("--jobs", type=int, default=8)


@command("strategy backtest", HUMAN_SYSTEM, configure=_configure_backtest, needs_store=True,
         help="model layer + index-enhancement portfolio + simulated execution on the dev tier")
def cmd_strategy_backtest(args, ctx) -> CommandResult:
    from alphasieve.strategy.run import backtest

    refs = [r.strip() for r in args.factors.split(",")] if args.factors else None
    return CommandResult(data=backtest(ctx.settings, ctx.conn, args.universe, args.horizon, args.model, args.retrain,
                                       args.train_years, args.rebalance_every, args.industry_dev, args.name_cap,
                                       args.turnover_cap, refs, args.seed_library, args.jobs, args.warmup_years,
                                       args.size_limit))
