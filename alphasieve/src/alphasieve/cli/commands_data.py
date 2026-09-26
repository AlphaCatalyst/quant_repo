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
SYNC_DATASETS = ("reference", "members", "daily", "financials", "mirror", "core")


def _progress(label):
    def report(done, total):
        print(f"[{label}] {done}/{total}", file=sys.stderr, flush=True)

    return report


def _configure_sync(p):
    p.add_argument("--dataset", choices=SYNC_DATASETS, default="core",
                   help="core = reference + members + daily + mirror")
    p.add_argument("--end", default=None, help="last date to fetch (default: latest trading day)")
    p.add_argument("--workers", type=int, default=6)


@command("data sync", HUMAN_SYSTEM, configure=_configure_sync, needs_store=True, help="fetch raw data (resumable)")
def cmd_data_sync(args, ctx) -> CommandResult:
    settings, conn = ctx.settings, ctx.conn
    from datetime import date

    end = args.end or date.today().isoformat()
    out: dict = {}
    datasets = ["reference", "members", "daily", "mirror"] if args.dataset == "core" else [args.dataset]
    if "reference" in datasets or not (sync.raw_root(settings) / "trade_dates.parquet").exists():
        out["reference"] = sync.sync_reference(settings, conn, end)
    end = sync.latest_trading_day(settings, end)
    out["end"] = end
    if "members" in datasets:
        out["members"] = sync.sync_members(settings, conn, end)
    if "daily" in datasets:
        out["daily"] = sync.sync_daily(settings, conn, end, args.workers, _progress("daily"))
    if "financials" in datasets:
        out["financials"] = sync.sync_financials(settings, conn, end, args.workers, _progress("financials"))
    if "mirror" in datasets or "financials" in datasets:
        out["mirror"] = sync.mirror_to_store(settings)
    warnings = []
    for key in ("daily", "financials"):
        if out.get(key, {}).get("errors"):
            warnings.append(f"{key}: {len(out[key]['errors'])} items failed; rerun to resume")
    return CommandResult(data=out, warnings=warnings)


def _configure_build(p):
    p.add_argument("--end", default=None, help="last date to include (default: holdout end)")


@command("data build-panel", HUMAN_SYSTEM, configure=_configure_build, help="build dev / holdout panels from raw data")
def cmd_build_panel(args, ctx) -> CommandResult:
    from alphasieve.data.panel import build_panel

    results = build_panel(ctx.settings, ctx.conn, args.end)
    warnings = [f"{tier}: quality checks failed" for tier, r in results.items() if not r["quality_ok"]]
    return CommandResult(data=results, warnings=warnings)


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


@command("data describe", ALL, configure=_configure_describe, help="describe a dev-panel field")
def cmd_data_describe(args, ctx) -> CommandResult:
    panel = load_panel(ctx.settings, "dev")
    if not panel.has(args.field):
        raise validation_error(f"unknown field {args.field}", available=sorted(panel.long.columns))
    start, end = panel.window
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


@command("data sample", ("agent", "human"), configure=_configure_sample, help="sample dev-panel rows")
def cmd_data_sample(args, ctx) -> CommandResult:
    panel = load_panel(ctx.settings, "dev")
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
