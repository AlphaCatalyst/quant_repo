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
    stock = pd.read_parquet(path, columns=["date", "close", "volume", "turn"])
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
        adj = pd.read_parquet(adj_path, columns=["dividOperateDate", "backAdjustFactor"])
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
    members = pd.read_parquet(root / "members.parquet", columns=["snapshot_date", "index", "code"])
    members = members[(members["index"] == "zz500") & (members["snapshot_date"] <= DEV_END)]
    if members.empty:
        raise ValueError("no CSI 500 member snapshots through dev end")
    calendar = pd.read_parquet(root / "trade_dates.parquet", columns=["calendar_date", "is_trading_day"])
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


def compare_raw(raw_root: Path) -> dict:
    raw_root = Path(raw_root)
    official_path = raw_root / "csindex" / "total_return" / "H00905.parquet"
    official = pd.read_parquet(official_path, columns=["date", "close"])
    official = official[(official["date"] <= DEV_END)].drop_duplicates("date", keep="last")
    close = pd.Series(pd.to_numeric(official["close"], errors="coerce").to_numpy(),
                      index=pd.to_datetime(official["date"]))
    return compare_returns(proxy_returns_from_raw(raw_root), close)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True, help="hot-root/raw directory")
    args = parser.parse_args()
    print(json.dumps(compare_raw(args.raw_root), ensure_ascii=False, indent=2, allow_nan=False))
