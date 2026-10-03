"""Read-only D-31 CSI 500 proxy check against the official total-return index.

Reads only baostock stock/member raw files and csindex H00905 raw data. The
comparison window is fixed to the dev split and never loads a panel artifact.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

DEV_START = "2012-01-01"
DEV_END = "2022-12-31"
ANN = 252


def _annual_stats(proxy: pd.Series, official: pd.Series) -> dict:
    paired = pd.concat([proxy.rename("proxy"), official.rename("official")], axis=1).dropna()
    if paired.empty:
        return {"days": 0, "annual_gap": None, "tracking_error": None, "correlation": None}
    diff = paired["proxy"] - paired["official"]
    correlation = paired["proxy"].corr(paired["official"])
    return {
        "days": len(paired),
        "annual_gap": float(diff.mean() * ANN),
        "tracking_error": float(diff.std(ddof=0) * np.sqrt(ANN)),
        "correlation": float(correlation) if pd.notna(correlation) else None,
    }


def compare_returns(proxy: pd.Series, official_close: pd.Series) -> dict:
    """Compare daily returns on common dev dates; never infer missing values as zero."""
    proxy = proxy.loc[DEV_START:DEV_END].sort_index()
    close = official_close.sort_index()
    close = close.loc[:DEV_END]
    official = close.pct_change(fill_method=None).loc[DEV_START:DEV_END]
    paired = pd.concat([proxy.rename("proxy"), official.rename("official")], axis=1).dropna()
    if paired.empty:
        raise ValueError("no common dev daily returns")
    return {
        "window": {"start": DEV_START, "end": DEV_END},
        "overall": _annual_stats(paired["proxy"], paired["official"]),
        "by_year": {
            str(year): _annual_stats(group["proxy"], group["official"])
            for year, group in paired.groupby(paired.index.year)
        },
    }


def _stock_values(path: Path, dates: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    stock = pd.read_parquet(path, columns=["date", "close", "volume", "turn"],
                            filters=[("date", ">=", "2011-01-01"), ("date", "<=", DEV_END)])
    stock = stock[(stock["date"] >= "2011-01-01") & (stock["date"] <= DEV_END)]
    if stock.empty:
        nan = np.full(len(dates), np.nan)
        return nan, nan.copy()
    stock = stock.drop_duplicates("date", keep="last").sort_values("date")
    stock.index = pd.to_datetime(stock["date"])
    close = pd.to_numeric(stock["close"], errors="coerce")
    volume = pd.to_numeric(stock["volume"], errors="coerce")
    turn = pd.to_numeric(stock["turn"], errors="coerce")
    shares = (volume / (turn / 100)).where((turn > 0) & (volume > 0)).ffill()
    cap = (close * shares).reindex(dates).to_numpy(dtype=float)
    adj_path = path.parent.parent / "adj" / path.name
    if adj_path.exists():
        adj = pd.read_parquet(adj_path, columns=["dividOperateDate", "backAdjustFactor"],
                              filters=[("dividOperateDate", "<=", DEV_END)])
        adj = adj[(adj["dividOperateDate"] <= DEV_END)].dropna(subset=["backAdjustFactor"])
        adj = adj.sort_values("dividOperateDate")
        if not adj.empty:
            pos = np.searchsorted(adj["dividOperateDate"].to_numpy(), stock["date"].to_numpy(), side="right") - 1
            factors = adj["backAdjustFactor"].to_numpy(dtype=float)
            close = close * np.where(pos >= 0, factors[np.clip(pos, 0, None)], factors[0])
    return close.reindex(dates).to_numpy(dtype=float), cap


def proxy_returns_from_raw(raw_root: Path) -> pd.Series:
    """Reproduce training.mandates.proxy_tracking from baostock raw inputs."""
    root = raw_root / "baostock"
    members = pd.read_parquet(root / "members.parquet", columns=["snapshot_date", "index", "code"],
                              filters=[("snapshot_date", "<=", DEV_END)])
    members = members[(members["index"] == "zz500") & (members["snapshot_date"] <= DEV_END)]
    if members.empty:
        raise ValueError("no CSI 500 member snapshots through dev end")
    calendar = pd.read_parquet(root / "trade_dates.parquet", columns=["calendar_date", "is_trading_day"],
                               filters=[("calendar_date", "<=", DEV_END)])
    calendar = calendar[(calendar["is_trading_day"] == 1) &
                        (calendar["calendar_date"] >= "2011-01-01") &
                        (calendar["calendar_date"] <= DEV_END)]
    dates = pd.DatetimeIndex(pd.to_datetime(calendar["calendar_date"].drop_duplicates().sort_values()))
    if dates.empty:
        raise ValueError("no dev trading dates")
    month = dates.to_period("M").to_timestamp().strftime("%Y-%m-%d")
    codes = sorted(members["code"].unique())
    membership = set(zip(members["snapshot_date"], members["code"], strict=True))
    numerator = np.zeros(len(dates), dtype=float)
    denominator = np.zeros(len(dates), dtype=float)
    for code in codes:
        path = root / "daily" / f"{code}.parquet"
        if not path.exists():
            continue
        close, cap = _stock_values(path, dates)
        held = np.fromiter(((m, code) in membership for m in month), dtype=bool, count=len(dates))
        weight = np.where(held & np.isfinite(cap) & (cap > 0), cap, 0.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            stock_return = close[1:] / close[:-1] - 1
        numerator[1:] += weight[:-1] * np.nan_to_num(stock_return, nan=0.0, posinf=0.0, neginf=0.0)
        denominator[1:] += weight[:-1]
    out = np.full(len(dates), np.nan)
    valid = denominator > 0
    out[valid] = numerator[valid] / denominator[valid]
    return pd.Series(out, index=dates, name="proxy").loc[DEV_START:DEV_END]


def official_weight_returns_from_raw(raw_root: Path) -> pd.Series:
    """Rebuild CSI 500 total returns from published month-end weights and adjusted closes.

    A weight dated T first applies to the close-to-close return on the next trading
    day. Between snapshots, constituent weights drift with adjusted prices.
    """
    root = raw_root / "baostock"
    weights = pd.read_parquet(raw_root / "dolthub" / "index_weights" / "000905.SH.parquet",
                              filters=[("trade_date", "<=", DEV_END)])
    weights = weights[weights["trade_date"] <= DEV_END].copy()
    if weights.empty:
        raise ValueError("no CSI 500 historical weights through dev end")
    weights["trade_date"] = pd.to_datetime(weights["trade_date"])
    if "code" not in weights:
        suffix = weights["stock_code"].str[-2:].str.upper().map({"SH": "sh", "SZ": "sz"})
        weights["code"] = suffix + "." + weights["stock_code"].str[:6]
    calendar = pd.read_parquet(root / "trade_dates.parquet", columns=["calendar_date", "is_trading_day"],
                               filters=[("calendar_date", "<=", DEV_END)])
    calendar = calendar[(calendar["is_trading_day"] == 1) &
                        (calendar["calendar_date"] >= "2011-01-01") &
                        (calendar["calendar_date"] <= DEV_END)]
    dates = pd.DatetimeIndex(pd.to_datetime(calendar["calendar_date"].drop_duplicates().sort_values()))
    codes = sorted(weights["code"].unique())
    close = np.column_stack([_stock_values(root / "daily" / f"{code}.parquet", dates)[0]
                             if (root / "daily" / f"{code}.parquet").exists()
                             else np.full(len(dates), np.nan) for code in codes])
    daily = np.full_like(close, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        daily[1:] = close[1:] / close[:-1] - 1
    snapshot = weights.pivot_table(index="trade_date", columns="code", values="weight", aggfunc="last")
    snapshot = snapshot.reindex(columns=codes).fillna(0.0).sort_index() / 100.0
    if not snapshot.sum(axis=1).between(0.99, 1.01).all():
        raise ValueError("CSI 500 monthly weights do not sum to approximately 100 percent")
    snapshot_dates = snapshot.index.to_numpy()
    snapshot_values = snapshot.to_numpy(dtype=float)
    out = np.full(len(dates), np.nan)
    held = None
    last_snapshot = -1
    for i in range(1, len(dates)):
        snap = np.searchsorted(snapshot_dates, dates[i].to_datetime64(), side="left") - 1
        if snap < 0:
            continue
        if snap != last_snapshot:
            held = snapshot_values[snap].copy()
            last_snapshot = snap
        if held is None:
            continue
        active = held > 0
        if not active.any() or not np.isfinite(daily[i, active]).all():
            held = None
            continue
        out[i] = float(np.dot(held, daily[i]) if np.isfinite(daily[i]).all()
                       else np.dot(held[active], daily[i, active]))
        held *= np.where(active, 1 + np.nan_to_num(daily[i], nan=0.0), 1.0)
        held /= held.sum()
    return pd.Series(out, index=dates, name="official_weights").loc[DEV_START:DEV_END]


def compare_three_raw(raw_root: Path) -> dict:
    raw_root = Path(raw_root)
    official = pd.read_parquet(raw_root / "csindex" / "total_return" / "H00905.parquet",
                               columns=["date", "close"], filters=[("date", "<=", DEV_END)])
    official = official[official["date"] <= DEV_END].drop_duplicates("date", keep="last")
    close = pd.Series(pd.to_numeric(official["close"], errors="coerce").to_numpy(),
                      index=pd.to_datetime(official["date"]))
    reference = close.sort_index().pct_change(fill_method=None).loc[DEV_START:DEV_END]
    weighted = official_weight_returns_from_raw(raw_root)
    proxy = proxy_returns_from_raw(raw_root)
    paired = pd.concat([weighted, reference.rename("H00905"), proxy], axis=1).dropna()
    if paired.empty:
        raise ValueError("no common dev daily returns for three-way comparison")
    return {"window": {"start": DEV_START, "end": DEV_END},
            "common_days": len(paired),
            "official_weights_vs_H00905": _annual_stats(paired["official_weights"], paired["H00905"]),
            "D31_proxy_vs_H00905": _annual_stats(paired["proxy"], paired["H00905"]),
            "official_weights_vs_D31_proxy": _annual_stats(paired["official_weights"], paired["proxy"])}


def compare_raw(raw_root: Path) -> dict:
    raw_root = Path(raw_root)
    official_path = raw_root / "csindex" / "total_return" / "H00905.parquet"
    official = pd.read_parquet(official_path, columns=["date", "close"], filters=[("date", "<=", DEV_END)])
    official = official[(official["date"] <= DEV_END)].drop_duplicates("date", keep="last")
    close = pd.Series(pd.to_numeric(official["close"], errors="coerce").to_numpy(),
                      index=pd.to_datetime(official["date"]))
    return compare_returns(proxy_returns_from_raw(raw_root), close)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True, help="hot-root/raw directory")
    parser.add_argument("--three-way", action="store_true", help="include DoltHub historical CSI 500 weights")
    args = parser.parse_args()
    result = compare_three_raw(args.raw_root) if args.three_way else compare_raw(args.raw_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
