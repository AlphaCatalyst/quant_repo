"""Deterministic synthetic market data in BaoStock raw format.

The generator plants known structure so tests can check the pipeline end to end:
a short-term reversal effect, suspensions, limit-up opens, ST periods, splits,
IPOs inside the sample and delistings.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

REVERSAL_STRENGTH = 0.12


@dataclass
class SynthInfo:
    codes: list[str]
    suspended_code: str
    suspended_range: tuple[str, str]
    limit_up_events: list[tuple[str, str]]
    st_code: str
    st_range: tuple[str, str]
    split_code: str
    split_date: str
    ipo_code: str
    ipo_date: str
    delisted_code: str
    delist_date: str
    chinext_code: str
    star_code: str


def _codes(n: int) -> list[str]:
    codes = []
    for i in range(n):
        if i % 10 == 8:
            codes.append(f"sz.300{i:03d}")
        elif i % 10 == 9:
            codes.append(f"sh.688{i:03d}")
        elif i % 2 == 0:
            codes.append(f"sh.600{i:03d}")
        else:
            codes.append(f"sz.000{i:03d}")
    return codes


def _write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def limit_ratio(code: str, day: str, is_st: bool) -> float:
    if code.startswith("sh.688"):
        return 0.20
    if code.startswith("sz.300"):
        return 0.20 if day >= "2020-08-24" else (0.05 if is_st else 0.10)
    return 0.05 if is_st else 0.10


def make_raw(
    root: Path, n_stocks: int = 200, start: str = "2018-01-01", end: str = "2021-12-31", seed: int = 7
) -> SynthInfo:
    rng = np.random.default_rng(seed)
    root = Path(root)
    all_days = pd.date_range("2017-06-01", pd.Timestamp(end) + pd.Timedelta(days=30), freq="D")
    trading = pd.bdate_range("2017-06-01", pd.Timestamp(end) + pd.Timedelta(days=30))
    cal = pd.DataFrame({
        "calendar_date": all_days.strftime("%Y-%m-%d"),
        "is_trading_day": all_days.isin(trading).astype(int),
    })
    _write(cal, root / "trade_dates.parquet")
    days = [d.strftime("%Y-%m-%d") for d in trading if start <= d.strftime("%Y-%m-%d") <= end]
    history_days = [d.strftime("%Y-%m-%d") for d in trading if d.strftime("%Y-%m-%d") <= end]
    history_days = [d for d in history_days if d >= "2017-06-01"]
    codes = _codes(n_stocks)
    n_days = len(history_days)

    info = SynthInfo(
        codes=codes,
        suspended_code=codes[2],
        suspended_range=(days[100], days[119]),
        limit_up_events=[(codes[4], days[150]), (codes[6], days[300]), (codes[8], days[400])],
        st_code=codes[10],
        st_range=(days[200], days[450]),
        split_code=codes[12],
        split_date=days[250],
        ipo_code=codes[14],
        ipo_date=days[120],
        delisted_code=codes[16],
        delist_date=days[500],
        chinext_code=codes[8],
        star_code=codes[9],
    )

    basic = pd.DataFrame({
        "code": codes,
        "code_name": [f"S{i:03d}" for i in range(n_stocks)],
        "ipoDate": "2010-01-04",
        "outDate": "",
        "type": "1",
        "status": "1",
    })
    basic.loc[basic.code == info.ipo_code, "ipoDate"] = info.ipo_date
    basic.loc[basic.code == info.delisted_code, ["outDate", "status"]] = [info.delist_date, "0"]
    index_basic = pd.DataFrame({
        "code": ["sh.000300", "sh.000905", "sh.000906"], "code_name": ["HS300", "ZZ500", "CSI800"],
        "ipoDate": "2005-01-04", "outDate": "", "type": "2", "status": "1",
    })
    _write(pd.concat([basic, index_basic], ignore_index=True), root / "stock_basic.parquet")

    industries = [f"I{i % 10}" for i in range(n_stocks)]
    _write(pd.DataFrame({
        "updateDate": end, "code": codes, "code_name": basic.code_name, "industry": industries,
        "industryClassification": "synthetic", "fetched_date": end,
    }), root / "industry.parquet")

    member_rows = []
    for k, snapshot in enumerate(pd.date_range(start, end, freq="MS").strftime("%Y-%m-%d")):
        shift = (k // 6) * 5
        order = codes[shift:] + codes[:shift]
        hs = [c for c in order if c != info.delisted_code or snapshot < info.delist_date][:60]
        zz = [c for c in order if c not in hs][:90]
        for index, members in (("hs300", hs), ("zz500", zz)):
            for code in members:
                member_rows.append({"updateDate": snapshot, "code": code, "code_name": "", "snapshot_date": snapshot,
                                    "index": index})
    _write(pd.DataFrame(member_rows), root / "members.parquet")

    market = rng.normal(0.0003, 0.012, n_days)
    ind_ret = rng.normal(0, 0.006, (n_days, 10))
    beta = rng.uniform(0.6, 1.4, n_stocks)
    idio = rng.normal(0, 0.02, (n_days, n_stocks))
    ret = np.zeros((n_days, n_stocks))
    for t in range(n_days):
        past = ret[max(0, t - 5):t].mean(axis=0) if t > 0 else 0.0
        ret[t] = beta * market[t] + ind_ret[t, [i % 10 for i in range(n_stocks)]] + idio[t] - REVERSAL_STRENGTH * past
    ret = np.clip(ret, -0.095, 0.095)
    adj_close = 10.0 * np.cumprod(1 + ret, axis=0)
    overnight = ret * 0.3 + rng.normal(0, 0.002, (n_days, n_stocks))
    float_shares = rng.uniform(2e8, 5e9, n_stocks)

    day_index = {d: i for i, d in enumerate(history_days)}
    split_t = day_index[info.split_date]
    factor = np.ones((n_days, n_stocks))
    split_i = codes.index(info.split_code)
    factor[split_t:, split_i] = 2.0

    idx_frames = []
    for j, code in enumerate(codes):
        close_adj = adj_close[:, j]
        prev_adj = np.concatenate([[close_adj[0] / (1 + ret[0, j])], close_adj[:-1]])
        open_adj = prev_adj * (1 + overnight[:, j])
        raw_close = close_adj / factor[:, j]
        raw_open = open_adj / factor[:, j]
        raw_prev = prev_adj / factor[:, j]
        high = np.maximum(raw_open, raw_close) * (1 + np.abs(rng.normal(0, 0.004, n_days)))
        low = np.minimum(raw_open, raw_close) * (1 - np.abs(rng.normal(0, 0.004, n_days)))
        volume = (float_shares[j] * rng.uniform(0.003, 0.03, n_days)).astype(np.int64)
        amount = volume * (raw_open + raw_close) / 2
        df = pd.DataFrame({
            "date": history_days, "code": code,
            "open": raw_open, "high": high, "low": low, "close": raw_close, "preclose": raw_prev,
            "volume": volume, "amount": amount, "turn": volume / float_shares[j] * 100,
            "tradestatus": 1, "pctChg": (raw_close / raw_prev - 1) * 100,
            "peTTM": 15 * np.exp(np.cumsum(rng.normal(0, 0.01, n_days))),
            "pbMRQ": 2 * np.exp(np.cumsum(rng.normal(0, 0.01, n_days))),
            "psTTM": 3 * np.exp(np.cumsum(rng.normal(0, 0.01, n_days))),
            "isST": 0,
        })
        if code == info.st_code:
            mask = (df.date >= info.st_range[0]) & (df.date <= info.st_range[1])
            df.loc[mask, "isST"] = 1
        if code == info.suspended_code:
            mask = (df.date >= info.suspended_range[0]) & (df.date <= info.suspended_range[1])
            first = df.index[mask][0]
            frozen = df.loc[first - 1, "close"]
            df.loc[mask, ["open", "high", "low", "close", "preclose"]] = frozen
            df.loc[mask, ["volume", "amount", "turn"]] = 0
            df.loc[mask, "tradestatus"] = 0
        for ev_code, ev_day in info.limit_up_events:
            if code == ev_code:
                i = df.index[df.date == ev_day][0]
                is_st = bool(df.loc[i, "isST"])
                up = round(df.loc[i, "preclose"] * (1 + limit_ratio(code, ev_day, is_st)), 2)
                df.loc[i, ["open", "high", "close"]] = up
                df.loc[i, "low"] = min(df.loc[i, "low"], up)
        if code == info.ipo_code:
            df = df[df.date >= info.ipo_date]
        if code == info.delisted_code:
            df = df[df.date < info.delist_date]
        _write(df.reset_index(drop=True), root / "daily" / f"{code}.parquet")
        adj = pd.DataFrame({"code": [code], "dividOperateDate": ["2010-01-04"], "foreAdjustFactor": [1.0],
                            "backAdjustFactor": [1.0], "adjustFactor": [1.0]})
        if code == info.split_code:
            adj = pd.concat([adj, pd.DataFrame({"code": [code], "dividOperateDate": [info.split_date],
                                                "foreAdjustFactor": [1.0], "backAdjustFactor": [2.0],
                                                "adjustFactor": [2.0]})], ignore_index=True)
        _write(adj, root / "adj" / f"{code}.parquet")

        pub_rows_p, pub_rows_g = [], []
        for q_end in pd.date_range("2017-03-31", end, freq="QE"):
            pub = q_end + pd.Timedelta(days=int(rng.integers(25, 100)))
            dates = {"pubDate": pub.strftime("%Y-%m-%d"), "statDate": q_end.strftime("%Y-%m-%d")}
            pub_rows_p.append({"code": code, **dates,
                               "roeAvg": rng.normal(0.08, 0.03), "npMargin": rng.normal(0.1, 0.05), "gpMargin": None,
                               "netProfit": rng.normal(1e9, 3e8), "epsTTM": rng.normal(1, 0.3),
                               "MBRevenue": rng.normal(1e10, 1e9), "totalShare": float_shares[j] * 1.2,
                               "liqaShare": float_shares[j]})
            pub_rows_g.append({"code": code, **dates,
                               "YOYEquity": rng.normal(0.1, 0.1), "YOYAsset": rng.normal(0.1, 0.1),
                               "YOYNI": rng.normal(0.1, 0.2), "YOYEPSBasic": rng.normal(0.1, 0.2),
                               "YOYPNI": rng.normal(0.1, 0.2)})
        _write(pd.DataFrame(pub_rows_p), root / "financials" / "profit" / f"{code}.parquet")
        _write(pd.DataFrame(pub_rows_g), root / "financials" / "growth" / f"{code}.parquet")

    mean_ret = ret.mean(axis=1)
    for code in ("sh.000300", "sh.000905", "sh.000906"):
        close = 1000 * np.cumprod(1 + mean_ret)
        idx_frames.append(pd.DataFrame({
            "date": history_days, "code": code, "open": close / (1 + mean_ret * 0.3), "high": close * 1.005,
            "low": close * 0.995, "close": close, "preclose": np.concatenate([[1000], close[:-1]]),
            "volume": 1e9, "amount": 1e10, "pctChg": mean_ret * 100,
        }))
    for frame in idx_frames:
        _write(frame, root / "index_daily" / f"{frame.code.iloc[0]}.parquet")
    return info
