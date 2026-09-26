import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from alphasieve.config import Settings, load_config
from alphasieve.data.sync import HISTORY_START, INDEX_CODES, raw_root
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


def _stock_frame(code: str, root: Path, cal_pos: dict[str, int], ipo: str, end: str) -> pd.DataFrame | None:
    path = root / "daily" / f"{code}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df = df[(df["date"] >= HISTORY_START) & (df["date"] <= end)].sort_values("date").reset_index(drop=True)
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


def _attach_financials(panel: pd.DataFrame, frames: list[pd.DataFrame]) -> pd.DataFrame:
    panel = panel.sort_values(["date", "code"])
    for aligned in frames:
        aligned = aligned.rename(columns={"effective_date": "date"}).sort_values(["date", "code"])
        aligned["date"] = pd.to_datetime(aligned["date"])
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


def build_long_panel(settings: Settings, end: str) -> tuple[pd.DataFrame, list[str], dict]:
    root = raw_root(settings)
    cal_df = pd.read_parquet(root / "trade_dates.parquet")
    calendar = sorted(cal_df.loc[cal_df["is_trading_day"] == 1, "calendar_date"].tolist())
    calendar = [d for d in calendar if HISTORY_START <= d <= end]
    cal_pos = {d: i for i, d in enumerate(calendar)}
    members = pd.read_parquet(root / "members.parquet")
    codes = sorted(members["code"].unique())
    basic = pd.read_parquet(root / "stock_basic.parquet").set_index("code")
    frames, missing = [], []
    for code in codes:
        ipo = basic.at[code, "ipoDate"] if code in basic.index else HISTORY_START
        frame = _stock_frame(code, root, cal_pos, ipo or HISTORY_START, end)
        if frame is None:
            missing.append(code)
        else:
            frames.append(frame)
    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])
    panel = _membership(panel, members)
    industry = pd.read_parquet(root / "industry.parquet")
    ind_map = dict(zip(industry["code"], industry["industry"].replace("", "unknown"), strict=True))
    panel["industry"] = panel["code"].map(ind_map).fillna("unknown")
    fin_frames = _load_financials(root, codes, calendar)
    if fin_frames:
        panel = _attach_financials(panel, fin_frames)
    panel = panel.sort_values(["date", "code"]).reset_index(drop=True)
    panel["in_universe"] = (
        panel["in_csi800"] & ~panel["is_st"] & (panel["days_listed"] >= MIN_DAYS_LISTED) & ~panel["is_suspended"]
    )
    info = {"codes": len(codes), "missing_codes": missing, "has_financials": bool(fin_frames)}
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


def _write_tier(settings: Settings, tier: str, panel: pd.DataFrame, bench: pd.DataFrame, meta: dict) -> dict:
    out_dir = settings.panel_dir(tier)
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


def build_panel(settings: Settings, conn: sqlite3.Connection | None = None, end: str | None = None) -> dict:
    from alphasieve.data.quality import quality_report

    splits = load_config(settings, "splits")
    last_needed = end or splits["holdout"]["end"]
    panel, calendar, info = build_long_panel(settings, last_needed)
    snapshots = []
    if conn is not None:
        snapshots = [dict(r) for r in conn.execute(
            "SELECT snapshot_id, dataset, content_hash FROM data_snapshots ORDER BY fetched_at")]
    results = {}
    for tier in ("dev", "holdout"):
        window = splits[tier]
        window_end = min(window["end"], last_needed)
        labels = embargo_labels(_labels(panel, calendar, window_end), calendar, window_end)
        tier_panel = panel[panel["date"] <= window_end].merge(labels, on=["date", "code"], how="left")
        meta = {
            "panel_version": PANEL_VERSION,
            "tier": tier,
            "window": {"start": window["start"], "end": window_end, "warmup_start": HISTORY_START},
            "splits_version": splits["version"],
            "rows": len(tier_panel),
            "codes": int(tier_panel["code"].nunique()),
            "missing_codes": info["missing_codes"],
            "has_financials": info["has_financials"],
            "horizons": list(HORIZONS),
            "embargo": {f"label_{h}d": 1 + h for h in HORIZONS},
            "fields": sorted(tier_panel.columns),
            "source_snapshots": snapshots,
            "warnings": PANEL_WARNINGS,
            "built_at": utcnow_iso(),
        }
        bench = _benchmark(raw_root(settings), calendar, window_end)
        meta = _write_tier(settings, tier, tier_panel, bench, meta)
        report = quality_report(settings, tier, tier_panel, meta, calendar)
        results[tier] = {"rows": meta["rows"], "codes": meta["codes"], "window": meta["window"],
                         "signature": meta["signature"], "quality_ok": report["ok"]}
    return results
