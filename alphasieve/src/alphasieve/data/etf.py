"""Sector-ETF data for mandate B (mandate-specs §5, training-tasks §4): westock daily bars and an ETF panel.

westock returns at most 250 bars per request, so bars are fetched one calendar year at a time. Its ETF volumes and
amounts are zero before mid-2018, so the liquidity screen applies only once amounts exist; before that an ETF
enters the universe after one year of listed prices. Industry is the ETF itself (every ETF is its own group).
"""

import json
import time

import numpy as np
import pandas as pd

from alphasieve.config import Settings, load_config
from alphasieve.data.sync import _write_parquet, record_snapshot, westock_root

ETF_UNIVERSE = {
    "sh512880": "证券", "sh512800": "银行", "sh512010": "医药", "sh512170": "医疗", "sh512690": "酒",
    "sh515170": "食品饮料", "sh510150": "消费", "sh512480": "半导体", "sz159995": "芯片", "sh515030": "新能源车",
    "sh515790": "光伏", "sh512660": "军工", "sh512400": "有色金属", "sh515220": "煤炭", "sh515210": "钢铁",
    "sh512200": "房地产", "sh512980": "传媒", "sh515880": "通信", "sh512720": "计算机", "sh516120": "化工",
    "sz159996": "家电", "sz159611": "电力", "sz159825": "农业", "sz159865": "养殖", "sh512580": "环保",
    "sh516970": "基建", "sh510230": "金融", "sz159997": "电子", "sz159869": "游戏", "sz159766": "旅游",
    "sz159745": "建材", "sh512070": "证券保险", "sh512290": "生物医药", "sh516840": "汽车",
}
MIN_LISTED_DAYS = 250
MIN_AMOUNT_20D = 2e7
HORIZONS = (1, 5, 10, 20)
FETCH_RETRIES = 6
INDEX_START = "2011-01-01"
CALL_GAP_S = 1.5          # the kline endpoint throttles after a handful of quick calls
THROTTLE_BACKOFF_S = 20


def etf_root(settings: Settings):
    return westock_root(settings) / "etf"


def _fetch(code: str, start: str, end: str) -> pd.DataFrame:
    from alphasieve.data.providers.westock import WestockError, _call

    frames = []
    for year in range(int(start[:4]), int(end[:4]) + 1):
        lo, hi = max(start, f"{year}-01-01"), min(end, f"{year}-12-31")
        for attempt in range(FETCH_RETRIES):
            time.sleep(CALL_GAP_S)
            try:
                rows = _call(["kline", code, "--start", lo, "--end", hi])
            except WestockError:
                rows = None
            if isinstance(rows, list) and all(isinstance(r, dict) and "date" in r for r in rows):
                break
            time.sleep(THROTTLE_BACKOFF_S * (attempt + 1))
        else:
            raise RuntimeError(f"{code} {year}: westock kline kept failing (throttled)")
        if rows:
            frames.append(pd.DataFrame(rows))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True).rename(columns={"last": "close"})
    df = df[["date", "open", "high", "low", "close", "volume", "amount"]].drop_duplicates("date")
    for c in ("open", "high", "low", "close", "volume", "amount"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


def _track_index(code: str) -> str | None:
    from alphasieve.data.providers.westock import WestockError, _call

    for attempt in range(FETCH_RETRIES):
        time.sleep(CALL_GAP_S)
        try:
            data = _call(["etf", "detail", code])
        except WestockError:
            data = None
        if isinstance(data, dict) and data.get("sections"):
            first = data["sections"][0][0] if data["sections"][0] else {}
            return first.get("trackIndexCode")
        time.sleep(THROTTLE_BACKOFF_S * (attempt + 1))
    return None


def sync_etf_indices(settings: Settings, end: str, start: str = INDEX_START) -> dict:
    """Tracked index of every ETF (westock ``etf detail``) and its daily bars, for the pre-listing proxy."""
    root = etf_root(settings)
    meta_path = root / "_track_index.json"
    mapping = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    for code in ETF_UNIVERSE:
        if not mapping.get(code):
            mapping[code] = _track_index(code)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=1))
    out, errors = {}, []
    for index in sorted({v for v in mapping.values() if v}):
        path = root.parent / "etf_index" / f"{index}.parquet"
        if path.exists():
            out[index] = {"rows": int(len(pd.read_parquet(path, columns=["date"]))), "cached": True}
            continue
        try:
            df = _fetch(index, start, end)
        except Exception as exc:  # noqa: BLE001
            errors.append({"index": index, "error": str(exc)[:200]})
            continue
        if not df.empty:
            _write_parquet(df, path)
        out[index] = {"rows": int(len(df)), "first": df["date"].min() if len(df) else None}
    return {"mapping": mapping, "indices": out, "errors": errors}


def sync_etf_holdings(settings: Settings, end: str) -> dict:
    """Top-20 holdings of every ETF as of ``end`` (westock only serves the latest list; ``--date`` is ignored), kept
    as one dated snapshot so that later refreshes build a history instead of overwriting it."""
    from alphasieve.data.providers.westock import WestockError, _call

    path = etf_root(settings) / "holdings" / f"{end}.json"
    snap = json.loads(path.read_text()) if path.exists() else {}
    errors = []
    for code in ETF_UNIVERSE:
        if snap.get(code):
            continue
        for attempt in range(FETCH_RETRIES):
            time.sleep(CALL_GAP_S)
            try:
                rows = _call(["etf", "holdings", code])
            except WestockError:
                rows = None
            if isinstance(rows, list) and rows and all("ratio" in r for r in rows):
                snap[code] = [{"code": r["code"], "name": r.get("name"), "weight": float(r["ratio"]) / 100}
                              for r in rows]
                break
            time.sleep(THROTTLE_BACKOFF_S * (attempt + 1))
        else:
            errors.append(code)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=1))
    return {"path": str(path), "etfs": len(snap), "errors": errors,
            "top20_weight_median": float(np.median([sum(r["weight"] for r in v) for v in snap.values()]))
            if snap else None}


def sync_etf(settings: Settings, conn, end: str, start: str = "2013-01-01") -> dict:
    root = etf_root(settings)
    out, errors = {}, []
    for code in ETF_UNIVERSE:
        path = root / f"{code}.parquet"
        if path.exists():
            have = pd.read_parquet(path, columns=["date"])["date"]
            if len(have) and have.max() >= (pd.Timestamp(end) - pd.Timedelta(days=10)).strftime("%Y-%m-%d"):
                out[code] = {"rows": int(len(have)), "first": have.min(), "cached": True}
                continue
        try:
            df = _fetch(code, start, end)
        except Exception as exc:  # noqa: BLE001
            errors.append({"code": code, "error": str(exc)[:200]})
            continue
        if not df.empty:
            _write_parquet(df, root / f"{code}.parquet")
        out[code] = {"rows": int(len(df)), "first": df["date"].min() if len(df) else None}
    indices = sync_etf_indices(settings, end)
    paths = sorted(root.glob("*.parquet")) + sorted((root.parent / "etf_index").glob("*.parquet"))
    snap = record_snapshot(conn, "westock:etf", {"end": end}, paths, sum(v["rows"] for v in out.values()), "westock")
    return {"etfs": out, "errors": errors, "indices": indices, "snapshot": snap}


def _with_proxy(df: pd.DataFrame, index: pd.DataFrame | None) -> pd.DataFrame:
    """Prepend the tracked index, rescaled to the ETF's first close, for the dates before the ETF existed."""
    df = df.assign(is_proxy=False)
    if index is None or index.empty or df.empty:
        return df
    first = df["date"].min()
    ref = index[index["date"] == first]
    if ref.empty or not ref["close"].iloc[0]:
        return df
    scale = float(df.loc[df["date"] == first, "close"].iloc[0]) / float(ref["close"].iloc[0])
    pre = index[index["date"] < first].copy()
    for c in ("open", "high", "low", "close"):
        pre[c] = pre[c] * scale
    pre["volume"] = np.nan
    pre["amount"] = np.nan
    pre["is_proxy"] = True
    return pd.concat([pre[df.columns], df], ignore_index=True)


def _long_panel(settings: Settings, end: str) -> pd.DataFrame:
    root = etf_root(settings)
    meta_path = root / "_track_index.json"
    mapping = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    frames = []
    for code in ETF_UNIVERSE:
        path = root / f"{code}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        index_path = root.parent / "etf_index" / f"{mapping.get(code)}.parquet"
        index = pd.read_parquet(index_path) if mapping.get(code) and index_path.exists() else None
        df = _with_proxy(df, index)
        df = df[df["date"] <= end].copy()
        df["code"] = f"{code[:2]}.{code[2:]}"
        frames.append(df)
    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])
    return panel


def build_etf_panel(settings: Settings, tiers: tuple[str, ...] = ("dev", "holdout")) -> dict:
    from alphasieve.data.panel import PANEL_VERSION, _write_tier
    from alphasieve.util import utcnow_iso

    splits = load_config(settings, "splits")
    results = {}
    for tier in tiers:
        window = splits[tier]
        long = _long_panel(settings, window["end"])
        dates = pd.DatetimeIndex(sorted(long["date"].unique()))
        codes = sorted(long["code"].unique())
        wide = {f: long.pivot(index="date", columns="code", values=f).reindex(index=dates, columns=codes)
                for f in ("open", "high", "low", "close", "volume", "amount", "is_proxy")}
        is_proxy = wide.pop("is_proxy").astype(float).fillna(0).astype(bool)
        close, open_ = wide["close"], wide["open"]
        listed = close.notna().cumsum()
        amount20 = wide["amount"].where(wide["amount"] > 0).rolling(20, min_periods=10).mean()
        liquid = amount20.isna() | (amount20 >= MIN_AMOUNT_20D)
        has_bar = close.notna() & open_.notna()
        in_universe = has_bar & (listed >= MIN_LISTED_DAYS) & liquid
        window_end = pd.Timestamp(window["end"])
        last = int(dates.searchsorted(window_end, side="right")) - 1
        labels = {}
        for h in HORIZONS:
            lab = open_.shift(-1 - h) / open_.shift(-1) - 1
            lab = lab.where(has_bar.shift(-1, fill_value=False))
            lab.iloc[max(0, last - h):] = np.nan
            labels[f"label_{h}d"] = lab
        frame = {"open": open_, "high": wide["high"], "low": wide["low"], "close": close, "vwap": close,
                 "volume": wide["volume"], "amount": wide["amount"], "ret_1d": close / close.shift(1) - 1,
                 "in_universe": in_universe, "tradable_buy": has_bar, "tradable_sell": has_bar,
                 "is_suspended": ~has_bar, "days_listed": listed.astype(float),
                 "circ_mv": has_bar.astype(float).where(has_bar), "is_proxy": is_proxy, **labels}
        long_out = pd.concat({k: v.stack(future_stack=True) for k, v in frame.items()}, axis=1)
        long_out.index.names = ["date", "code"]
        long_out = long_out.reset_index()
        long_out = long_out[long_out["close"].notna()]
        long_out["industry"] = long_out["code"]
        long_out["is_st"] = False
        long_out["in_universe"] = long_out["in_universe"].astype(bool)
        long_out["is_proxy"] = long_out["is_proxy"].astype(bool)
        long_out = long_out[long_out["date"] <= window_end].reset_index(drop=True)
        start = pd.Timestamp(splits["dev"]["start"]) if tier == "dev" else pd.Timestamp(window["start"])
        meta = {"panel_version": PANEL_VERSION, "tier": tier, "universe": "etf_sector",
                "window": {"start": str(start.date()), "end": window["end"], "warmup_start": str(dates[0].date())},
                "splits_version": splits["version"], "rows": int(len(long_out)),
                "codes": int(long_out["code"].nunique()), "horizons": list(HORIZONS),
                "fields": sorted(long_out.columns), "etfs": ETF_UNIVERSE,
                "warnings": ["westock ETF volumes/amounts are zero before mid-2018; the liquidity screen applies"
                             " only when amounts exist", "prices are westock default-adjusted ETF bars",
                             "before an ETF listed, its tracked index (rescaled) stands in; is_proxy marks those"
                             " rows, which are not tradable"],
                "built_at": utcnow_iso()}
        meta = _write_tier(settings, tier, long_out, pd.DataFrame(), meta, "etf_sector")
        per_year = long_out[long_out["in_universe"]].groupby(long_out["date"].dt.year)["code"].nunique()
        results[tier] = {"rows": meta["rows"], "codes": meta["codes"], "signature": meta["signature"],
                         "universe_names_by_year": json.loads(per_year.to_json())}
    return results
