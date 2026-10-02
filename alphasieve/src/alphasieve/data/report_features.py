"""Point-in-time daily features from parsed westock sell-side reports (2010+)."""

import numpy as np
import pandas as pd

REPORT_FIELDS = [
    "wr_coverage_90d", "wr_coverage_180d", "wr_eps_fy1", "wr_eps_fy2",
    "wr_eps_change_30d", "wr_eps_change_90d", "wr_revision_balance_30d",
    "wr_revision_balance_90d", "wr_rating_mean", "wr_rating_change_30d",
    "wr_rating_change_90d", "wr_consensus_ep", "wr_eps_growth",
]
RATING_SCORE = {"strong_buy": 5.0, "buy": 4.0, "hold": 3.0, "sell": 2.0, "strong_sell": 1.0}


def _ratio_change(current: float, previous: float) -> float:
    if not np.isfinite(current) or not np.isfinite(previous) or previous == 0:
        return np.nan
    return current / previous - 1


def _daily_features(dates: pd.DatetimeIndex, reports: pd.DataFrame) -> pd.DataFrame:
    """Calculate one stock's features, retaining empty days for correct lag lookups."""
    reports = reports.sort_values(["publish_date", "report_id"]).copy()
    publications = reports["publish_date"].to_numpy(dtype="datetime64[ns]")
    values = []
    for day in dates:
        # Midnight timestamps do not establish intraday availability.
        known = reports.iloc[:np.searchsorted(publications, day.to_datetime64(), side="left")]
        age = (day - known["publish_date"]).dt.days
        recent = known[age <= 180]
        eps_recent = recent[recent["eps"].notna() & recent["broker"].notna()]
        year = day.year
        fy = []
        for fiscal_year in (year, year + 1):
            per_broker = eps_recent[eps_recent["fiscal_year"] == fiscal_year]
            per_broker = per_broker.drop_duplicates("broker", keep="last")
            fy.append(float(per_broker["eps"].median()) if not per_broker.empty else np.nan)
        ratings = recent[recent["rating"].isin(RATING_SCORE) & recent["broker"].notna()]
        ratings = ratings.drop_duplicates("broker", keep="last")
        rating = float(ratings["rating"].map(RATING_SCORE).mean()) if not ratings.empty else np.nan
        row = {"wr_eps_fy1": fy[0], "wr_eps_fy2": fy[1], "wr_rating_mean": rating}
        for window in (90, 180):
            brokers = recent.loc[age.loc[recent.index] <= window, "broker"]
            row[f"wr_coverage_{window}d"] = float(brokers.dropna().nunique())
        for window in (30, 90):
            revisions = recent[age.loc[recent.index] <= window].drop_duplicates("report_id")
            revisions = revisions[revisions["broker"].notna()]
            signs = revisions["is_revision"].map({"up": 1, "down": -1, "上调": 1, "下调": -1})
            row[f"wr_revision_balance_{window}d"] = (float(signs.sum() / signs.notna().sum())
                                                      if signs.notna().any() else np.nan)
        row["wr_eps_growth"] = _ratio_change(fy[1], fy[0])
        values.append(row)
    out = pd.DataFrame(values, index=dates)
    for window in (30, 90):
        prior_dates = dates - pd.Timedelta(days=window)
        prior = out.reindex(prior_dates, method="ffill")
        prior.index = dates
        out[f"wr_eps_change_{window}d"] = [
            _ratio_change(now, old) for now, old in zip(out["wr_eps_fy1"], prior["wr_eps_fy1"], strict=True)]
        out[f"wr_rating_change_{window}d"] = out["wr_rating_mean"] - prior["wr_rating_mean"]
    return out


def attach_report_features(panel: pd.DataFrame, reports: pd.DataFrame, calendar: list[str]) -> pd.DataFrame:
    """Add sell-side features without changing any existing panel values or row order."""
    result = panel.copy()
    for field in REPORT_FIELDS:
        result[field] = np.nan
    if reports.empty:
        return result
    reports = reports.copy()
    reports["publish_date"] = pd.to_datetime(reports["publish_date"], errors="coerce")
    reports["eps"] = pd.to_numeric(reports["eps"], errors="coerce")
    reports["fiscal_year"] = pd.to_numeric(reports["fiscal_year"], errors="coerce")
    reports = reports.dropna(subset=["publish_date", "code"])
    dates = pd.DatetimeIndex(pd.to_datetime(calendar))
    if dates.empty:
        return result
    for code, locations in result.groupby("code", sort=False).indices.items():
        stock_reports = reports[reports["code"] == code]
        if stock_reports.empty:
            continue
        daily = _daily_features(dates, stock_reports)
        row_dates = pd.DatetimeIndex(result.iloc[locations]["date"])
        aligned = daily.reindex(row_dates)
        result.iloc[locations, result.columns.get_indexer(daily.columns)] = aligned.to_numpy()
    # EPS is unadjusted yuan/share, so use the matching unadjusted prior close.
    previous_close = result.groupby("code", sort=False)["close_raw"].shift(1)
    result["wr_consensus_ep"] = result["wr_eps_fy1"] / previous_close.where(previous_close > 0)
    return result
