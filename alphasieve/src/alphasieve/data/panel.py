import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from alphasieve.config import Settings, load_config
from alphasieve.data.sync import INDEX_CODES, history_start, raw_root, universe_codes, westock_root
from alphasieve.data.universe import universe_config
from alphasieve.util import file_sha256, utcnow_iso

PANEL_VERSION = 1
HORIZONS = (1, 5, 10, 20)
NO_LIMIT_DAYS_AFTER_IPO = 5
MIN_DAYS_LISTED = 60
CHINEXT_REFORM_DATE = "2020-08-24"
FINANCIAL_FIELDS = {
    "profit": {"roeAvg": "roe_avg", "npMargin": "np_margin", "epsTTM": "eps_ttm"},
    "growth": {"YOYNI": "yoy_ni", "YOYEquity": "yoy_equity", "YOYAsset": "yoy_asset"},
}
WESTOCK_WARNING = ("ws_* statement fields: announcement dates are first publication, but about a third of annual "
                   "balance sheets hold later restated values (D-30)")
PANEL_WARNINGS = [
    "industry classification is a current snapshot (CSRC), not point-in-time",
    "universe is CSI 800 (HS300 + ZZ500) because free historical constituents are only available for these indices",
    "limit-up / limit-down prices are derived from board rules, not provided by the data source",
    "circ_mv is derived as close * volume / turnover rate",
]


def _round_half_up(values: np.ndarray) -> np.ndarray:
    return np.floor(values * 100 + 0.5) / 100


def limit_ratios(codes: pd.Series, dates: pd.Series, is_st: pd.Series) -> np.ndarray:
    star = codes.str.startswith("sh.688").to_numpy()
    chinext = codes.str.startswith(("sz.300", "sz.301")).to_numpy()
    bse = codes.str.startswith("bj.").to_numpy()
    reform = (dates >= CHINEXT_REFORM_DATE).to_numpy()
    st = is_st.to_numpy()
    ratio = np.where(st, 0.05, 0.10)
    ratio = np.where(chinext & reform, 0.20, ratio)
    ratio = np.where(star, 0.20, ratio)
    ratio = np.where(bse, 0.30, ratio)
    return ratio


def _stock_frame(code: str, root: Path, cal_pos: dict[str, int], ipo: str, end: str,
                 start: str = "2011-01-01") -> pd.DataFrame | None:
    path = root / "daily" / f"{code}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df = df[(df["date"] >= start) & (df["date"] <= end)].sort_values("date").reset_index(drop=True)
    if df.empty:
        return None
    adj_path = root / "adj" / f"{code}.parquet"
    factor = np.ones(len(df))
    if adj_path.exists():
        adj = pd.read_parquet(adj_path).dropna(subset=["backAdjustFactor"]).sort_values("dividOperateDate")
        if not adj.empty:
            idx = np.searchsorted(adj["dividOperateDate"].to_numpy(), df["date"].to_numpy(), side="right") - 1
            values = adj["backAdjustFactor"].to_numpy()
            factor = np.where(idx >= 0, values[np.clip(idx, 0, None)], values[0])
    df["adj_factor"] = factor
    for col in ("open", "high", "low", "close"):
        df[f"{col}_raw"] = df[col]
        df[col] = df[col] * factor
    df["vwap_raw"] = np.where(df["volume"] > 0, df["amount"] / df["volume"].where(df["volume"] > 0), np.nan)
    df["vwap"] = df["vwap_raw"] * factor
    float_shares = (df["volume"] / (df["turn"] / 100)).where((df["turn"] > 0) & (df["volume"] > 0))
    df["float_shares"] = float_shares.ffill()
    df["circ_mv"] = df["close_raw"] * df["float_shares"]
    df["is_suspended"] = (df["tradestatus"] == 0) | (df["volume"] <= 0)
    df["is_st"] = df["isST"].fillna(0).astype(int) == 1
    ipo_pos = cal_pos.get(ipo)
    if ipo_pos is None:
        later = [p for d, p in cal_pos.items() if d >= ipo]
        ipo_pos = min(later) if later else 0
    positions = df["date"].map(cal_pos)
    df["days_listed"] = (positions - ipo_pos).astype(float)
    ratio = limit_ratios(df["code"], df["date"], df["is_st"])
    df["limit_up"] = _round_half_up(df["preclose"].to_numpy() * (1 + ratio))
    df["limit_down"] = _round_half_up(df["preclose"].to_numpy() * (1 - ratio))
    limited = df["days_listed"] >= NO_LIMIT_DAYS_AFTER_IPO
    df["is_limit_up_open"] = limited & (df["open_raw"] >= df["limit_up"] - 1e-6) & ~df["is_suspended"]
    df["is_limit_down_open"] = limited & (df["open_raw"] <= df["limit_down"] + 1e-6) & ~df["is_suspended"]
    df["tradable_buy"] = ~df["is_suspended"] & ~df["is_limit_up_open"]
    df["tradable_sell"] = ~df["is_suspended"] & ~df["is_limit_down_open"]
    df = df.rename(columns={"turn": "turnover_rate", "peTTM": "pe_ttm", "pbMRQ": "pb_mrq", "psTTM": "ps_ttm"})
    return df.drop(columns=["isST", "tradestatus", "pctChg"])


def align_financials_pit(fin: pd.DataFrame, calendar: list[str], fields: dict[str, str]) -> pd.DataFrame:
    """Return rows keyed by (code, effective_date) with the latest first-published statement.

    A statement becomes usable on the first trading day strictly after its announcement date.
    Only the first publication of each statDate is used; an older statDate published after a
    newer one is ignored so that data never moves backwards in time.
    """
    if fin.empty:
        return pd.DataFrame(columns=["code", "effective_date", "stat_date", *fields.values()])
    fin = fin.dropna(subset=["pubDate", "statDate"]).copy()
    fin = fin[(fin["pubDate"] != "") & (fin["statDate"] != "")]
    fin = fin.sort_values(["code", "statDate", "pubDate"]).drop_duplicates(["code", "statDate"], keep="first")
    cal = np.array(calendar)
    idx = np.searchsorted(cal, fin["pubDate"].to_numpy(), side="right")
    fin = fin[idx < len(cal)].copy()
    fin["effective_date"] = cal[idx[idx < len(cal)]]
    fin = fin.sort_values(["code", "effective_date", "statDate"])
    fin["stat_ts"] = pd.to_datetime(fin["statDate"])
    fin["stat_max"] = fin.groupby("code")["stat_ts"].cummax()
    fin = fin[fin["stat_ts"] >= fin["stat_max"]]
    fin = fin.drop_duplicates(["code", "effective_date"], keep="last")
    out = fin.rename(columns={"statDate": "stat_date", **fields})
    return out[["code", "effective_date", "stat_date", *fields.values()]]


def _load_financials(root: Path, codes: list[str], calendar: list[str]) -> pd.DataFrame | None:
    frames = []
    for table, fields in FINANCIAL_FIELDS.items():
        parts = [pd.read_parquet(p) for c in codes if (p := root / "financials" / table / f"{c}.parquet").exists()]
        if not parts:
            continue
        aligned = align_financials_pit(pd.concat(parts, ignore_index=True), calendar, fields)
        aligned = aligned.rename(columns={"stat_date": f"{table}_stat_date"})
        frames.append(aligned)
    if not frames:
        return None
    return frames


def _attach_event_data(root: Path, codes: list[str], calendar: list[str]):
    from alphasieve.data.events import align_events, attach_events, express_rows, forecast_rows

    fc_parts = [pd.read_parquet(p) for c in codes if (p := root / "events" / "forecast" / f"{c}.parquet").exists()]
    ex_parts = [pd.read_parquet(p) for c in codes if (p := root / "events" / "express" / f"{c}.parquet").exists()]
    if not fc_parts and not ex_parts:
        return None
    fc = align_events(forecast_rows(pd.concat(fc_parts, ignore_index=True) if fc_parts else pd.DataFrame()),
                      calendar, ["fc_chg_mid", "fc_positive"])
    ex = align_events(express_rows(pd.concat(ex_parts, ignore_index=True) if ex_parts else pd.DataFrame()),
                      calendar, ["ex_eps_chg", "ex_roe", "ex_gr_yoy"])

    def apply(panel: pd.DataFrame) -> pd.DataFrame:
        panel = attach_events(panel, fc, calendar, ["fc_chg_mid", "fc_positive"], age_field="fc_age")
        return attach_events(panel, ex, calendar, ["ex_eps_chg", "ex_roe", "ex_gr_yoy"])

    return apply


def _load_intraday(root: Path, codes: list[str]) -> pd.DataFrame | None:
    parts = [pd.read_parquet(p) for c in codes if (p := root / "intraday" / f"{c}.parquet").exists()]
    if not parts:
        return None
    out = pd.concat(parts, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    return out


def _load_westock(root: Path, codes: list[str], calendar: list[str]):
    from alphasieve.data.fundamentals import STATEMENT_FIELDS, statement_rows

    def read(kind: str) -> pd.DataFrame:
        parts = [pd.read_parquet(p) for c in codes if (p := root / "financials" / kind / f"{c}.parquet").exists()]
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    statements = {k: read(k) for k in ("lrb", "zcfz", "xjll")}
    financials = None
    if any(not df.empty for df in statements.values()):
        rows = statement_rows(statements["lrb"], statements["zcfz"], statements["xjll"])
        financials = align_financials_pit(rows, calendar, {f: f for f in STATEMENT_FIELDS})
        financials = financials.rename(columns={"stat_date": "ws_stat_date"})
    flow_parts = [pd.read_parquet(p) for c in codes if (p := root / "fund_flow" / f"{c}.parquet").exists()]
    flow = pd.concat(flow_parts, ignore_index=True) if flow_parts else None
    wanted = set(codes)
    margin_parts = [df[df["code"].isin(wanted)] for p in sorted((root / "margin").glob("*.parquet"))
                    if not (df := pd.read_parquet(p)).empty]
    margin = pd.concat(margin_parts, ignore_index=True) if margin_parts else None
    margin = None if margin is None or margin.empty else margin
    return financials, flow, margin


def _attach_financials(panel: pd.DataFrame, frames: list[pd.DataFrame]) -> pd.DataFrame:
    panel = panel.sort_values(["date", "code"])
    for aligned in frames:
        aligned = aligned.rename(columns={"effective_date": "date"}).sort_values(["date", "code"])
        aligned["date"] = pd.to_datetime(aligned["date"]).astype(panel["date"].dtype)
        panel = pd.merge_asof(panel, aligned, on="date", by="code", direction="backward")
    return panel


def _membership(panel: pd.DataFrame, members: pd.DataFrame) -> pd.DataFrame:
    month_start = panel["date"].dt.to_period("M").dt.to_timestamp().dt.strftime("%Y-%m-%d")
    snapshots = set(members["snapshot_date"].unique())
    for index in ("hs300", "zz500"):
        keys = set(zip(members.loc[members["index"] == index, "snapshot_date"],
                       members.loc[members["index"] == index, "code"], strict=True))
        panel[f"in_{index}"] = [(m, c) in keys for m, c in zip(month_start, panel["code"], strict=True)]
    panel["in_csi800"] = panel["in_hs300"] | panel["in_zz500"]
    panel["has_member_snapshot"] = month_start.isin(snapshots).to_numpy()
    return panel


def _labels(panel: pd.DataFrame, calendar: list[str], window_end: str) -> pd.DataFrame:
    dates = pd.to_datetime(pd.Index([d for d in calendar if d <= window_end]))
    open_w = panel.pivot(index="date", columns="code", values="open").reindex(dates)
    close_w = panel.pivot(index="date", columns="code", values="close").reindex(dates)
    buy_w = panel.pivot(index="date", columns="code", values="tradable_buy").reindex(dates).astype("boolean")
    buy_w = buy_w.fillna(False).astype(bool)
    out = {"ret_1d": close_w / close_w.shift(1) - 1}
    entry = open_w.shift(-1)
    buyable = buy_w.shift(-1, fill_value=False)
    for h in HORIZONS:
        label = open_w.shift(-1 - h) / entry - 1
        out[f"label_{h}d"] = label.where(buyable)
    long = pd.concat({k: v.stack(future_stack=True) for k, v in out.items()}, axis=1)
    long.index.names = ["date", "code"]
    return long.reset_index()


def embargo_labels(df: pd.DataFrame, calendar: list[str], window_end: str) -> pd.DataFrame:
    days = [d for d in calendar if d <= window_end]
    positions = {pd.Timestamp(d): i for i, d in enumerate(days)}
    last = len(days) - 1
    pos = df["date"].map(positions)
    for h in HORIZONS:
        df.loc[pos + 1 + h > last, f"label_{h}d"] = np.nan
    return df


def _rule_universe(panel: pd.DataFrame, rule: dict) -> pd.Series:
    panel = panel.sort_values(["code", "date"])
    w = rule["liquidity_window"]
    amount_ma = panel.groupby("code", sort=False)["amount"].rolling(w, min_periods=w).mean()
    amount_ma = amount_ma.reset_index(level=0, drop=True).reindex(panel.index)
    eligible = (panel["days_listed"] >= rule["min_days_listed"]) & amount_ma.notna()
    if rule.get("exclude_st", True):
        eligible &= ~panel["is_st"]
    if rule.get("exclude_suspended", True):
        eligible &= ~panel["is_suspended"]
    rank = amount_ma.where(eligible).groupby(panel["date"]).rank(pct=True)
    return (eligible & (rank > rule["liquidity_drop_fraction"])).reindex(panel.index)


def build_long_panel(settings: Settings, end: str, universe: str | None = None,
                     warmup_start: str | None = None) -> tuple[pd.DataFrame, list[str], dict]:
    cfg = universe_config(settings, universe)
    start = max(history_start(settings, universe), warmup_start or "")
    root = raw_root(settings, universe)
    cal_df = pd.read_parquet(root / "trade_dates.parquet")
    calendar = sorted(cal_df.loc[cal_df["is_trading_day"] == 1, "calendar_date"].tolist())
    calendar = [d for d in calendar if start <= d <= end]
    cal_pos = {d: i for i, d in enumerate(calendar)}
    codes = universe_codes(settings, universe)
    basic = pd.read_parquet(root / "stock_basic.parquet").set_index("code")
    frames, missing = [], []
    for code in codes:
        ipo = basic.at[code, "ipoDate"] if code in basic.index else start
        frame = _stock_frame(code, root, cal_pos, ipo or start, end, start)
        if frame is None:
            missing.append(code)
        else:
            frames.append(frame)
    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])
    if cfg["membership"] in ("csi800", "hs300"):
        panel = _membership(panel, pd.read_parquet(root / "members.parquet"))
    industry = pd.read_parquet(root / "industry.parquet")
    ind_map = dict(zip(industry["code"], industry["industry"].replace("", "unknown"), strict=True))
    panel["industry"] = panel["code"].map(ind_map).fillna("unknown")
    fin_frames = _load_financials(root, codes, calendar)
    if fin_frames:
        panel = _attach_financials(panel, fin_frames)
    has_events = _attach_event_data(root, codes, calendar)
    if has_events is not None:
        panel = has_events(panel)
    intraday = _load_intraday(root, codes)
    if intraday is not None:
        panel = panel.merge(intraday, on=["date", "code"], how="left")
    ws_financials, ws_flow, ws_margin = _load_westock(westock_root(settings), codes, calendar)
    if ws_financials is not None:
        panel = _attach_financials(panel, [ws_financials.astype({"code": panel["code"].dtype})])
    if ws_flow is not None:
        from alphasieve.data.fundamentals import attach_fund_flow

        panel = attach_fund_flow(panel, ws_flow)
    if ws_margin is not None:
        from alphasieve.data.fundamentals import attach_margin

        panel = attach_margin(panel, ws_margin.astype({"code": panel["code"].dtype}), calendar)
    reports_path = westock_root(settings) / "reports" / "parsed.parquet"
    if reports_path.exists():
        from alphasieve.data.report_features import attach_report_features

        reports = pd.read_parquet(reports_path, filters=[("publish_date", "<=", end)])
        panel = attach_report_features(panel, reports[reports["code"].isin(codes)], calendar)
    panel = panel.sort_values(["date", "code"]).reset_index(drop=True)
    if cfg["membership"] == "hs300":
        panel["in_universe"] = (
            panel["in_hs300"] & ~panel["is_st"] & (panel["days_listed"] >= MIN_DAYS_LISTED) & ~panel["is_suspended"]
        )
    elif cfg["membership"] == "csi800":
        panel["in_universe"] = (
            panel["in_csi800"] & ~panel["is_st"] & (panel["days_listed"] >= MIN_DAYS_LISTED) & ~panel["is_suspended"]
        )
    else:
        panel["in_universe"] = _rule_universe(panel, cfg["rule"]).fillna(False).astype(bool)
    info = {"codes": len(codes), "missing_codes": missing, "has_financials": bool(fin_frames), "universe": cfg["name"],
            "has_events": has_events is not None, "has_intraday": intraday is not None,
            "has_westock_financials": ws_financials is not None, "has_fund_flow": ws_flow is not None,
            "has_margin": ws_margin is not None}
    return panel, calendar, info


def _benchmark(root: Path, calendar: list[str], window_end: str) -> pd.DataFrame:
    frames = []
    for name, code in INDEX_CODES.items():
        path = root / "index_daily" / f"{code}.parquet"
        if path.exists():
            df = pd.read_parquet(path)[["date", "open", "close"]]
            df = df[df["date"] <= window_end].rename(columns={"open": f"{name}_open", "close": f"{name}_close"})
            frames.append(df.set_index("date"))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1).reset_index()


def _write_tier(settings: Settings, tier: str, panel: pd.DataFrame, bench: pd.DataFrame, meta: dict,
                universe: str | None = None) -> dict:
    if tier == "fresh":
        raise ValueError("fresh panel is append-only; use data.fresh.append_day")
    out_dir = settings.panel_dir(tier, universe)
    out_dir.mkdir(parents=True, exist_ok=True)
    if tier != "dev":
        os.chmod(out_dir, 0o700)
    tmp = out_dir / "panel.parquet.tmp"
    panel.to_parquet(tmp, index=False)
    os.replace(tmp, out_dir / "panel.parquet")
    if not bench.empty:
        bench.to_parquet(out_dir / "benchmark.parquet", index=False)
    meta = {**meta, "signature": file_sha256(out_dir / "panel.parquet")}
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    if tier != "dev":
        for path in out_dir.iterdir():
            os.chmod(path, 0o600)
    return meta


def _universe_note(cfg: dict) -> str:
    if cfg["membership"] == "rule":
        return f"universe is rule-based ({cfg['rule']}); delisted names are included to avoid survivorship bias"
    return f"universe: {cfg['description']}"


def build_panel(settings: Settings, conn: sqlite3.Connection | None = None, end: str | None = None,
                universe: str | None = None, tiers: tuple[str, ...] = ("dev", "holdout"),
                warmup_start: str | None = None) -> dict:
    from alphasieve.data.quality import quality_report

    if "fresh" in tiers:
        raise ValueError("fresh panel is append-only; use data.fresh.append_day")

    splits = load_config(settings, "splits")
    cfg = universe_config(settings, universe)
    last_needed = end or max(splits[t]["end"] for t in tiers)
    if warmup_start and "dev" in tiers:
        raise ValueError("--warmup-start only applies to holdout / fresh builds (the dev window needs full history)")
    panel, calendar, info = build_long_panel(settings, last_needed, universe, warmup_start)
    snapshots = []
    if conn is not None:
        snapshots = [dict(r) for r in conn.execute(
            "SELECT snapshot_id, dataset, content_hash FROM data_snapshots ORDER BY fetched_at")]
    results = {}
    for tier in tiers:
        window = dict(splits[tier])
        if tier == "dev" and cfg["name"] != "csi800":
            window["start"] = cfg["dev_start"]
        window_end = min(window["end"], last_needed)
        labels = embargo_labels(_labels(panel, calendar, window_end), calendar, window_end)
        tier_panel = panel[panel["date"] <= window_end].merge(labels, on=["date", "code"], how="left")
        meta = {
            "panel_version": PANEL_VERSION,
            "tier": tier,
            "window": {"start": window["start"], "end": window_end,
                       "warmup_start": max(history_start(settings, universe), warmup_start or "")},
            "universe": cfg["name"],
            "splits_version": splits["version"],
            "rows": len(tier_panel),
            "codes": int(tier_panel["code"].nunique()),
            "missing_codes": info["missing_codes"],
            "has_financials": info["has_financials"],
            "has_westock_financials": info["has_westock_financials"],
            "has_fund_flow": info["has_fund_flow"],
            "has_margin": info["has_margin"],
            "horizons": list(HORIZONS),
            "embargo": {f"label_{h}d": 1 + h for h in HORIZONS},
            "fields": sorted(tier_panel.columns),
            "source_snapshots": snapshots,
            "warnings": (PANEL_WARNINGS if cfg["name"] == "csi800" else [
                w for w in PANEL_WARNINGS if not w.startswith("universe is CSI 800")] + [_universe_note(cfg)])
            + ([WESTOCK_WARNING] if info["has_westock_financials"] else []),
            "built_at": utcnow_iso(),
        }
        bench = _benchmark(raw_root(settings, universe), calendar, window_end)
        meta = _write_tier(settings, tier, tier_panel, bench, meta, universe)
        report = quality_report(settings, tier, tier_panel, meta, calendar, universe)
        results[tier] = {"rows": meta["rows"], "codes": meta["codes"], "window": meta["window"],
                         "signature": meta["signature"], "quality_ok": report["ok"]}
    return results
