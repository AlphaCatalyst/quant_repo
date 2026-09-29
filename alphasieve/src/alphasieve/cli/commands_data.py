import sys

import pandas as pd

from alphasieve.cli.registry import CommandResult, command
from alphasieve.config import load_config
from alphasieve.data import sync
from alphasieve.data.access import load_panel, read_meta
from alphasieve.errors import validation_error

ALL = ("agent", "human", "system")
HUMAN_SYSTEM = ("human", "system")
MAX_SAMPLE_ROWS = 200
SYNC_DATASETS = ("reference", "members", "daily", "financials", "events", "intraday", "mirror", "core",
                 "ws_financials", "fund_flow", "margin", "margin_history", "etf")


def _universe_arg(p):
    p.add_argument("--universe", default=None, help="csi800 (default) or another universe in universes.yaml")


def _universe(args, ctx) -> str | None:
    if args.universe:
        return args.universe
    if ctx.settings.campaign:
        from alphasieve.campaigns.service import get_campaign
        return get_campaign(ctx.conn, ctx.settings.campaign)["spec"].universe
    return None


def _progress(label):
    def report(done, total):
        print(f"[{label}] {done}/{total}", file=sys.stderr, flush=True)

    return report


def _configure_sync(p):
    p.add_argument("--dataset", choices=SYNC_DATASETS, default="core",
                   help="core = reference + members + daily + mirror")
    p.add_argument("--end", default=None, help="last date to fetch (default: latest trading day)")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--start", default="2020-01-01", help="first date for intraday bars / margin history")
    _universe_arg(p)


@command("data sync", HUMAN_SYSTEM, configure=_configure_sync, needs_store=True, help="fetch raw data (resumable)")
def cmd_data_sync(args, ctx) -> CommandResult:
    settings, conn = ctx.settings, ctx.conn
    from datetime import date

    end = args.end or date.today().isoformat()
    u = args.universe
    out: dict = {"universe": u or "csi800"}
    default = ["reference", "members", "daily", "mirror"] if u in (None, "csi800") else ["reference", "daily"]
    datasets = default if args.dataset == "core" else [args.dataset]
    if "reference" in datasets or not (sync.raw_root(settings, u) / "trade_dates.parquet").exists():
        out["reference"] = sync.sync_reference(settings, conn, end, u)
    end = sync.latest_trading_day(settings, end, u)
    out["end"] = end
    if "members" in datasets:
        out["members"] = sync.sync_members(settings, conn, end)
    if "daily" in datasets:
        out["daily"] = sync.sync_daily(settings, conn, end, args.workers, _progress("daily"), u)
    if "financials" in datasets:
        out["financials"] = sync.sync_financials(settings, conn, end, args.workers, _progress("financials"), u)
    if "events" in datasets:
        out["events"] = sync.sync_events(settings, conn, end, args.workers, _progress("events"), u)
    if "intraday" in datasets:
        out["intraday"] = sync.sync_intraday(settings, conn, args.start, end, args.workers, _progress("intraday"), u)
    if "ws_financials" in datasets:
        out["ws_financials"] = sync.sync_westock_financials(settings, conn, end, args.workers,
                                                            _progress("ws_financials"), u)
    if "fund_flow" in datasets:
        out["fund_flow"] = sync.sync_fund_flow(settings, conn, end, args.workers, _progress("fund_flow"), u)
    if "margin" in datasets:
        out["margin"] = sync.sync_margin_snapshot(settings, conn, end, args.workers, _progress("margin"), u)
    if "etf" in datasets:
        from alphasieve.data.etf import sync_etf

        out["etf"] = sync_etf(settings, conn, end)
    if "margin_history" in datasets:
        out["margin_history"] = sync.sync_margin_history(settings, conn, args.start, end, args.workers,
                                                         _progress("margin_history"), u)
    if "mirror" in datasets or ("financials" in datasets and u in (None, "csi800")):
        out["mirror"] = sync.mirror_to_store(settings, u)
    warnings = []
    for key in ("daily", "financials", "events", "intraday", "ws_financials", "fund_flow", "margin", "margin_history"):
        if out.get(key, {}).get("errors"):
            warnings.append(f"{key}: {len(out[key]['errors'])} items failed; rerun to resume")
    return CommandResult(data=out, warnings=warnings)


def _configure_build(p):
    p.add_argument("--end", default=None, help="last date to include (default: holdout end)")
    p.add_argument("--tiers", default="dev,holdout", help="which tiers to build (holdout only on the local host)")
    p.add_argument("--warmup-start", default=None, help="shorter history for holdout-only builds (saves memory)")
    _universe_arg(p)


@command("data build-panel", HUMAN_SYSTEM, configure=_configure_build, help="build dev / holdout panels from raw data")
def cmd_build_panel(args, ctx) -> CommandResult:
    from alphasieve.data.panel import build_panel

    tiers = tuple(t.strip() for t in args.tiers.split(",") if t.strip())
    results = build_panel(ctx.settings, ctx.conn, args.end, args.universe, tiers, args.warmup_start)
    warnings = [f"{tier}: quality checks failed" for tier, r in results.items() if not r["quality_ok"]]
    return CommandResult(data=results, warnings=warnings)


def _configure_etf_build(p):
    p.add_argument("--tiers", default="dev,holdout", help="which tiers to build (holdout only on the local host)")


@command("data build-etf-panel", HUMAN_SYSTEM, configure=_configure_etf_build,
         help="build the sector-ETF panels (universe etf_sector) from westock bars")
def cmd_build_etf_panel(args, ctx) -> CommandResult:
    from alphasieve.data.etf import build_etf_panel

    tiers = tuple(t.strip() for t in args.tiers.split(",") if t.strip())
    return CommandResult(data=build_etf_panel(ctx.settings, tiers))


@command("data status", ALL, help="data boundaries, freshness and quality summary")
def cmd_data_status(args, ctx) -> CommandResult:
    import json

    splits = load_config(ctx.settings, "splits")
    tiers = {}
    visible = ("dev",) if ctx.settings.role == "agent" else ("dev", "holdout")
    for tier in visible:
        meta = read_meta(ctx.settings, tier)
        quality_path = ctx.settings.quality_dir / f"{tier}.json"
        quality = json.loads(quality_path.read_text()) if quality_path.exists() else None
        tiers[tier] = None if meta is None else {
            "window": meta["window"], "rows": meta["rows"], "codes": meta["codes"],
            "signature": meta["signature"], "built_at": meta["built_at"], "warnings": meta["warnings"],
            "quality": None if quality is None else {
                "ok": quality["ok"], "last_date": quality["last_date"],
                "checks": {k: {"value": v["value"], "passed": v["passed"]} for k, v in quality["checks"].items()},
            },
        }
    return CommandResult(data={
        "splits": {k: splits[k] for k in ("version", "locked", "universe", "dev", "holdout", "fresh")},
        "tiers": tiers,
    })


def _configure_describe(p):
    p.add_argument("field")
    _universe_arg(p)


@command("data describe", ALL, configure=_configure_describe, help="describe a dev-panel field")
def cmd_data_describe(args, ctx) -> CommandResult:
    from alphasieve.factors.derived import DERIVED, terminal

    panel = load_panel(ctx.settings, "dev", universe=_universe(args, ctx))
    if not panel.has(args.field) and args.field not in DERIVED:
        raise validation_error(f"unknown field {args.field}", available=sorted([*panel.long.columns, *DERIVED]))
    if args.field.startswith("label_") and ctx.settings.role == "agent":
        raise validation_error("labels are not available through data describe")
    start, end = panel.window
    if args.field in DERIVED:
        mask = panel.mask("in_universe") & panel.window_mask().to_numpy()[:, None]
        series = terminal(panel, args.field).where(mask).stack(future_stack=True)
        series = series[mask.stack(future_stack=True)]
    else:
        df = panel.long[(panel.long["date"] >= start) & (panel.long["date"] <= end) & panel.long["in_universe"]]
        series = df[args.field]
    data = {"field": args.field, "dtype": str(series.dtype), "rows": int(len(series)),
            "coverage": float(series.notna().mean()) if len(series) else 0.0}
    if pd.api.types.is_numeric_dtype(series) and series.dtype != bool:
        q = series.quantile([0.01, 0.25, 0.5, 0.75, 0.99])
        data.update({"mean": float(series.mean()), "std": float(series.std()),
                     "quantiles": {str(k): float(v) for k, v in q.items()}})
    else:
        data["top_values"] = {str(k): int(v) for k, v in series.value_counts().head(10).items()}
    return CommandResult(data=data)


def _configure_sample(p):
    p.add_argument("--date", required=True)
    p.add_argument("--fields", default="close,ret_1d,turnover_rate,circ_mv,in_universe")
    p.add_argument("--codes", default=None, help="comma-separated codes")
    p.add_argument("--limit", type=int, default=50)
    _universe_arg(p)


@command("data sample", ("agent", "human"), configure=_configure_sample, help="sample dev-panel rows")
def cmd_data_sample(args, ctx) -> CommandResult:
    panel = load_panel(ctx.settings, "dev", universe=_universe(args, ctx))
    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    unknown = [f for f in fields if not panel.has(f)]
    if unknown:
        raise validation_error(f"unknown fields {unknown}")
    if any(f.startswith("label_") for f in fields) and ctx.settings.role == "agent":
        raise validation_error("labels are not available through data sample")
    df = panel.long[panel.long["date"] == pd.Timestamp(args.date)]
    if args.codes:
        df = df[df["code"].isin(args.codes.split(","))]
    rows = df[["date", "code", *fields]].head(min(args.limit, MAX_SAMPLE_ROWS))
    rows = rows.assign(date=rows["date"].dt.strftime("%Y-%m-%d"))
    return CommandResult(data={"rows": rows.to_dict(orient="records"), "count": int(len(rows))})


@command("data daily-update", HUMAN_SYSTEM, needs_store=True,
         help="incremental update: core data every run, financials on Saturdays")
def cmd_data_daily_update(args, ctx) -> CommandResult:
    from datetime import date

    settings, conn = ctx.settings, ctx.conn
    today = date.today()
    out = {"reference": sync.sync_reference(settings, conn, today.isoformat())}
    end = sync.latest_trading_day(settings, today.isoformat())
    out["end"] = end
    out["members"] = sync.sync_members(settings, conn, end)
    out["daily"] = sync.sync_daily(settings, conn, end, 6)
    out["fund_flow"] = sync.sync_fund_flow(settings, conn, end, 4, universe="ashare_all")
    out["margin"] = sync.sync_margin_snapshot(settings, conn, end, 8, universe="ashare_all")
    if today.weekday() == 5:
        out["financials"] = sync.sync_financials(settings, conn, end, 4)
        out["ws_financials"] = sync.sync_westock_financials(settings, conn, end, 4, universe="ashare_all")
    out["mirror"] = sync.mirror_to_store(settings)
    keys = ("daily", "financials", "fund_flow", "margin", "ws_financials")
    warnings = [f"{k}: {len(out[k]['errors'])} items failed; rerun to resume"
                for k in keys if out.get(k, {}).get("errors")]
    return CommandResult(data=out, warnings=warnings)
