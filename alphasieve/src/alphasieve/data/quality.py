import json

import numpy as np
import pandas as pd

from alphasieve.config import Settings
from alphasieve.util import utcnow_iso

THRESHOLDS = {
    "min_universe_size": 100,
    "min_close_coverage": 0.99,
    "min_label_coverage": 0.90,
    "max_anomaly_rate": 0.001,
    "max_missing_member_months": 0,
}


def quality_report(settings: Settings, tier: str, panel: pd.DataFrame, meta: dict, calendar: list[str]) -> dict:
    window = meta["window"]
    in_window = (panel["date"] >= window["start"]) & (panel["date"] <= window["end"])
    uni = panel[in_window & panel["in_universe"]]
    by_date = uni.groupby("date")
    universe_size = by_date.size()
    close_cov = by_date["close"].apply(lambda s: s.notna().mean())
    label_cov = by_date["label_5d"].apply(lambda s: s.notna().mean())
    active = panel[in_window & ~panel["is_suspended"] & (panel["days_listed"] >= 5)]
    anomalies = {
        "nonpositive_close": int((active["close"] <= 0).sum()),
        "extreme_return": int((active["ret_1d"].abs() > 0.45).sum()),
        "missing_circ_mv": int(active["circ_mv"].isna().sum()),
    }
    window_days = [d for d in calendar if window["start"] <= d <= window["end"]]
    months = sorted({d[:7] for d in window_days})
    covered = set(panel.loc[in_window & panel["has_member_snapshot"], "date"].dt.strftime("%Y-%m").unique())
    missing_months = [m for m in months if m not in covered]
    anomaly_rate = sum(anomalies.values()) / max(len(active), 1)
    checks = {
        "universe_size_min": {"value": int(universe_size.min()) if len(universe_size) else 0,
                              "threshold": THRESHOLDS["min_universe_size"]},
        "close_coverage_min": {"value": float(close_cov.min()) if len(close_cov) else 0.0,
                               "threshold": THRESHOLDS["min_close_coverage"]},
        "label_coverage_median": {"value": float(label_cov.median()) if len(label_cov) else 0.0,
                                  "threshold": THRESHOLDS["min_label_coverage"]},
        "anomaly_rate": {"value": anomaly_rate, "threshold": THRESHOLDS["max_anomaly_rate"]},
        "missing_member_months": {"value": len(missing_months), "threshold": THRESHOLDS["max_missing_member_months"]},
    }
    checks["universe_size_min"]["passed"] = checks["universe_size_min"]["value"] >= THRESHOLDS["min_universe_size"]
    checks["close_coverage_min"]["passed"] = checks["close_coverage_min"]["value"] >= THRESHOLDS["min_close_coverage"]
    checks["label_coverage_median"]["passed"] = (
        checks["label_coverage_median"]["value"] >= THRESHOLDS["min_label_coverage"]
    )
    checks["anomaly_rate"]["passed"] = anomaly_rate <= THRESHOLDS["max_anomaly_rate"]
    checks["missing_member_months"]["passed"] = len(missing_months) <= THRESHOLDS["max_missing_member_months"]
    last_date = panel["date"].max().strftime("%Y-%m-%d")
    report = {
        "tier": tier,
        "ok": all(c["passed"] for c in checks.values()),
        "checks": checks,
        "anomalies": anomalies,
        "missing_member_months": missing_months,
        "universe_size_mean": float(universe_size.mean()) if len(universe_size) else 0.0,
        "last_date": last_date,
        "window_last_trading_day": window_days[-1] if window_days else None,
        "stale_days": int(np.busday_count(last_date, window_days[-1])) if window_days else None,
        "generated_at": utcnow_iso(),
    }
    settings.quality_dir.mkdir(parents=True, exist_ok=True)
    (settings.quality_dir / f"{tier}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
