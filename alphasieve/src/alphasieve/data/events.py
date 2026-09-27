"""Event data (S-7): earnings forecasts and preliminary results, plus daily aggregates of 5-minute bars.

Events are point-in-time: a report becomes usable on the first trading day strictly after its publication date
and its values are carried forward for at most ``EVENT_CARRY_DAYS`` trading days (after that the proper
statement is usually out). Minute bars are reduced to daily features at sync time; raw minute bars are not kept.
"""

import numpy as np
import pandas as pd

EVENT_CARRY_DAYS = 60
POSITIVE_FORECASTS = {"预增", "扭亏", "续盈", "略增"}
NEGATIVE_FORECASTS = {"预减", "首亏", "续亏", "略减"}
EVENT_FIELDS = ["fc_chg_mid", "fc_positive", "fc_age", "ex_eps_chg", "ex_roe", "ex_gr_yoy"]
INTRADAY_FIELDS = ["rv_5m", "tail30_vol_share", "open30_ret", "updown_vol_share"]


def forecast_rows(fc: pd.DataFrame) -> pd.DataFrame:
    if fc.empty:
        return pd.DataFrame(columns=["code", "pub_date", "fc_chg_mid", "fc_positive"])
    out = pd.DataFrame({
        "code": fc["code"], "pub_date": fc["profitForcastExpPubDate"],
        "fc_chg_mid": (fc["profitForcastChgPctUp"] + fc["profitForcastChgPctDwn"]) / 2,
        "fc_positive": np.where(fc["profitForcastType"].isin(POSITIVE_FORECASTS), 1.0,
                                np.where(fc["profitForcastType"].isin(NEGATIVE_FORECASTS), -1.0, 0.0)),
    })
    return out[out["pub_date"].fillna("") != ""]


def express_rows(ex: pd.DataFrame) -> pd.DataFrame:
    if ex.empty:
        return pd.DataFrame(columns=["code", "pub_date", "ex_eps_chg", "ex_roe", "ex_gr_yoy"])
    out = pd.DataFrame({
        "code": ex["code"], "pub_date": ex["performanceExpPubDate"],
        "ex_eps_chg": ex.get("performanceExpressEPSChgPct"), "ex_roe": ex.get("performanceExpressROEWa"),
        "ex_gr_yoy": ex.get("performanceExpressGRYOY"),
    })
    return out[out["pub_date"].fillna("") != ""]


def align_events(rows: pd.DataFrame, calendar: list[str], fields: list[str]) -> pd.DataFrame:
    """Rows keyed by (code, effective_date): the latest report per effective day, first publication wins."""
    if rows.empty:
        return pd.DataFrame(columns=["code", "date", "event_pos", *fields])
    cal = np.array(calendar)
    idx = np.searchsorted(cal, rows["pub_date"].to_numpy(), side="right")
    rows = rows[idx < len(cal)].copy()
    rows["date"] = pd.to_datetime(cal[idx[idx < len(cal)]])
    rows["event_pos"] = idx[idx < len(cal)]
    rows = rows.sort_values(["code", "date", "pub_date"]).drop_duplicates(["code", "date"], keep="last")
    return rows[["code", "date", "event_pos", *fields]]


def attach_events(panel: pd.DataFrame, aligned: pd.DataFrame, calendar: list[str], fields: list[str],
                  age_field: str | None = None) -> pd.DataFrame:
    positions = {pd.Timestamp(d): i for i, d in enumerate(calendar)}
    panel = panel.sort_values(["date", "code"])
    if aligned.empty:
        for f in fields + ([age_field] if age_field else []):
            panel[f] = np.nan
        return panel
    aligned = aligned.astype({"code": panel["code"].dtype})
    merged = pd.merge_asof(panel, aligned.sort_values(["date", "code"]), on="date", by="code", direction="backward")
    age = merged["date"].map(positions) - merged["event_pos"]
    stale = ~(age <= EVENT_CARRY_DAYS)
    merged.loc[stale, fields] = np.nan
    if age_field:
        merged[age_field] = age.where(~stale).astype(float)
    return merged.drop(columns=["event_pos"])


def intraday_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Daily features from one stock's 5-minute bars (48 per full day)."""
    if bars.empty:
        return pd.DataFrame(columns=["date", "code", *INTRADAY_FIELDS])
    bars = bars.sort_values(["date", "time"]).copy()
    bars["ret"] = bars.groupby("date")["close"].pct_change()
    first_ret = bars.groupby("date")["close"].transform("first") / bars.groupby("date")["open"].transform("first") - 1
    bars["ret"] = bars["ret"].fillna(first_ret)
    g = bars.groupby("date")
    total_vol = g["volume"].sum()
    out = pd.DataFrame({
        "rv_5m": np.sqrt(g["ret"].apply(lambda r: float((r**2).sum()))),
        "tail30_vol_share": g["volume"].apply(lambda v: float(v.iloc[-6:].sum())) / total_vol.replace(0, np.nan),
        "open30_ret": g.apply(lambda d: float(d["close"].iloc[min(5, len(d) - 1)] / d["open"].iloc[0] - 1)
                              if d["open"].iloc[0] > 0 else np.nan, include_groups=False),
        "updown_vol_share": g.apply(lambda d: float(d.loc[d["ret"] > 0, "volume"].sum()), include_groups=False)
        / total_vol.replace(0, np.nan),
        "bars": g.size(),
    })
    out = out[out["bars"] >= 40].drop(columns=["bars"]).reset_index()
    out["code"] = bars["code"].iloc[0]
    return out[["date", "code", *INTRADAY_FIELDS]]
