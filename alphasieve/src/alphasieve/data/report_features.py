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
    brokers = reports["broker"].fillna("").astype(str).to_numpy()
    years = reports["fiscal_year"].astype(float).to_numpy()
    eps = reports["eps"].to_numpy(dtype=float)
    ratings = reports["rating"].map(RATING_SCORE).to_numpy(dtype=float)
    revisions = reports["is_revision"].map({"up": 1, "down": -1, "maintain": 0,
                                            "上调": 1, "下调": -1, "维持": 0}).to_numpy(dtype=float)
    report_ids = reports["report_id"].to_numpy()
    values = []
    for day in dates:
        # Midnight timestamps do not establish intraday availability.
        end = np.searchsorted(publications, day.to_datetime64(), side="left")
        begin = np.searchsorted(publications, (day - pd.Timedelta(days=180)).to_datetime64(), side="left")
        year = day.year
        fy_values = ({}, {})
        rating_values = {}
        seen_ids = set()
        coverage_180 = set()
        coverage_90 = set()
        revision_30 = {}
        revision_90 = {}
        cutoff_90 = (day - pd.Timedelta(days=90)).to_datetime64()
        cutoff_30 = (day - pd.Timedelta(days=30)).to_datetime64()
        for i in range(end - 1, begin - 1, -1):
            broker = brokers[i]
            if not broker:
                continue
            coverage_180.add(broker)
            if publications[i] >= cutoff_90:
                coverage_90.add(broker)
            fiscal = years[i]
            if np.isfinite(eps[i]):
                if fiscal == year:
                    fy_values[0].setdefault(broker, eps[i])
                elif fiscal == year + 1:
                    fy_values[1].setdefault(broker, eps[i])
            if np.isfinite(ratings[i]):
                rating_values.setdefault(broker, ratings[i])
            if report_ids[i] not in seen_ids:
                seen_ids.add(report_ids[i])
                if np.isfinite(revisions[i]):
                    if publications[i] >= cutoff_90:
                        revision_90.setdefault(broker, revisions[i])
                    if publications[i] >= cutoff_30:
                        revision_30.setdefault(broker, revisions[i])
        fy = [float(np.median(list(v.values()))) if v else np.nan for v in fy_values]
        rating = float(np.mean(list(rating_values.values()))) if rating_values else np.nan
        row = {"wr_eps_fy1": fy[0], "wr_eps_fy2": fy[1], "wr_rating_mean": rating,
               "wr_coverage_90d": float(len(coverage_90)), "wr_coverage_180d": float(len(coverage_180)),
               "wr_revision_balance_30d": (float(np.mean(list(revision_30.values()))) if revision_30 else np.nan),
               "wr_revision_balance_90d": (float(np.mean(list(revision_90.values()))) if revision_90 else np.nan)}
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
