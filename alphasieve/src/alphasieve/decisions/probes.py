"""Feasibility probes for P1, P2, P4, P6 and P7 on dev data.

Probes are descriptive looks at the dev tier taken before the tasks are pre-registered. They decide whether a task
is worth registering and which data are missing; they are not acceptance evidence, and a registered task must not
tune on what a probe showed.
"""

import warnings

import numpy as np
import pandas as pd

from alphasieve.config import Settings
from alphasieve.decisions.bars import Bars, load_bars
from alphasieve.decisions.simulate import trailing_vol
from alphasieve.decisions.synthetic import pool_masks
from alphasieve.portfolio_book.importer import normalize_code

SPLIT = "2016-01-01"  # probes fit on dev data before this date and report on dev data after it
STEP = 21
CYCLICAL_L2 = {
    "煤炭": ("煤炭开采", "焦炭Ⅱ"), "工业金属": ("工业金属",), "小金属与能源金属": ("小金属", "稀有金属", "能源金属"),
    "贵金属": ("贵金属", "黄金"), "钢铁": ("钢铁", "普钢", "特钢Ⅱ", "冶钢原料"), "化学原料": ("化学原料",),
    "化学纤维": ("化学纤维",), "农化制品": ("农化制品",), "水泥": ("水泥", "水泥制造"),
    "玻璃": ("玻璃玻纤", "玻璃制造"), "航运": ("航运", "航运港口"), "油气开采": ("油气开采Ⅱ", "石油开采"),
    "炼化": ("炼化及贸易", "石油化工"), "养殖": ("养殖业", "畜禽养殖"), "油服": ("油服工程", "采掘服务"),
}


def _rank(x: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Cross-sectional percentile per row; NaN outside ``valid``."""
    out = np.full(x.shape, np.nan)
    for i in range(x.shape[0]):
        ok = valid[i] & np.isfinite(x[i])
        if ok.sum() > 1:
            out[i, ok] = pd.Series(x[i, ok]).rank(pct=True).to_numpy()
    return out


def _forward(close: np.ndarray, open_: np.ndarray, rows: np.ndarray, h: int) -> np.ndarray:
    """Return from the next open to the close h days later; names that stop trading keep their last close."""
    last = pd.DataFrame(close).ffill().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        return last[rows + h] / open_[rows + 1] - 1


def _auc(score: np.ndarray, label: np.ndarray) -> float:
    order = pd.Series(score).rank().to_numpy()
    pos = label.sum()
    neg = len(label) - pos
    return float((order[label].sum() - pos * (pos + 1) / 2) / (pos * neg))


def _sample_rows(bars: Bars, h: int, start: str = "2007-01-01") -> np.ndarray:
    first = int(np.searchsorted(bars.dates, start))
    return np.arange(first, len(bars.dates) - h - 1, STEP)


def _features(bars: Bars, vol: np.ndarray, rows: np.ndarray, valid: np.ndarray) -> dict[str, np.ndarray]:
    close = pd.DataFrame(bars["close"]).ffill().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        ret20 = close[rows] / close[rows - 20] - 1
        high = pd.DataFrame(close).rolling(250, min_periods=120).max().to_numpy()[rows]
        off_high = close[rows] / high - 1
        turn = pd.DataFrame(bars["turnover_rate"]).rolling(20, min_periods=10).mean().to_numpy()[rows]
        size = np.log(bars["circ_mv"][rows].astype(float))
        bp = 1 / bars["pb_mrq"][rows].astype(float)
    raw = {"vol60": vol[rows], "ret20": ret20, "off_high": off_high, "turnover20": turn, "size": size, "bp": bp}
    return {k: _rank(v, valid) for k, v in raw.items()}


def probe_p1(bars: Bars, vol: np.ndarray, masks: np.ndarray, h: int = 20, drop: float = -0.15) -> dict:
    from sklearn.linear_model import LogisticRegression

    rows = _sample_rows(bars, h)
    valid = masks[rows]
    feats = _features(bars, vol, rows, valid)
    fwd = _forward(bars["close"], bars["open"], rows, h)
    names = list(feats)
    x = np.stack([feats[n] for n in names], axis=-1)
    ok = valid & np.isfinite(x).all(axis=-1) & np.isfinite(fwd)
    dates = np.repeat(bars.dates[rows][:, None], valid.shape[1], axis=1)
    X, y, r, d = x[ok], fwd[ok] <= drop, fwd[ok], dates[ok]
    train, test = d < SPLIT, d >= SPLIT
    model = LogisticRegression(max_iter=500).fit(X[train], y[train])
    p = model.predict_proba(X[test])[:, 1]
    vol_only = X[test][:, names.index("vol60")]
    base = float(y[test].mean())
    top = p >= np.quantile(p, 0.9)
    by_year = {}
    for year in sorted({s[:4] for s in d[test]}):
        sel = np.char.startswith(d[test].astype(str), year)
        cut = np.quantile(p[sel], 0.9)
        rate = y[test][sel].mean()
        by_year[year] = {"base_rate": float(rate), "top_decile_lift": float(y[test][sel][p[sel] >= cut].mean() / rate)
                         if rate else None}
    # Halving a warned name instead of holding it: value = -0.5 x its forward return, minus about 0.2% round trip.
    months = d[test]
    value = -0.5 * r[test][top] - 0.002
    monthly = pd.Series(value).groupby(months[top]).mean()
    random_value = -0.5 * pd.Series(r[test]).groupby(months).mean() - 0.002
    diff = (monthly - random_value.reindex(monthly.index)).dropna()
    return {
        "label": f"next-open to close {h} trading days later <= {drop:.0%}",
        "train": f"dev before {SPLIT}", "test": f"dev from {SPLIT}", "features": names,
        "coefficients": dict(zip(names, model.coef_[0].round(4).tolist(), strict=True)),
        "test_rows": int(test.sum()), "base_rate": base,
        "auc": _auc(p, y[test]), "auc_vol_only": _auc(vol_only, y[test]),
        "top_decile_rate": float(y[test][top].mean()), "top_decile_lift": float(y[test][top].mean() / base),
        "brier": float(np.mean((p - y[test]) ** 2)), "brier_climatology": float(base * (1 - base)),
        "top_decile_mean_forward": float(r[test][top].mean()), "all_mean_forward": float(r[test].mean()),
        "halve_on_warning_vs_random_halving": {"mean_per_month": float(diff.mean()), "months": int(len(diff)),
                                               "t": float(diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff))))},
        "by_year": by_year,
    }


def probe_p2(bars: Bars, vol: np.ndarray, masks: np.ndarray, h: int = 20, pairs: int = 400, seed: int = 7) -> dict:
    """Pairwise switch: does a pre-declared reversal + low-volatility score call the sign of a 20-day difference?"""
    rows = _sample_rows(bars, h)
    valid = masks[rows]
    feats = _features(bars, vol, rows, valid)
    score = (1 - feats["ret20"]) + (1 - feats["vol60"])
    fwd = _forward(bars["close"], bars["open"], rows, h)
    rng = np.random.default_rng(seed)
    diffs, gaps, years = [], [], []
    for i in range(len(rows)):
        ok = np.flatnonzero(valid[i] & np.isfinite(score[i]) & np.isfinite(fwd[i]))
        if len(ok) < 50:
            continue
        a, b = rng.choice(ok, pairs), rng.choice(ok, pairs)
        keep = a != b
        a, b = a[keep], b[keep]
        diffs.append(fwd[i, a] - fwd[i, b])
        gaps.append(score[i, a] - score[i, b])
        years.append(np.full(len(a), bars.dates[rows[i]][:4]))
    diff, gap, year = np.concatenate(diffs), np.concatenate(gaps), np.concatenate(years)
    hit = np.sign(diff) == np.sign(gap)
    buckets = pd.qcut(np.abs(gap), 5, labels=False)
    by_bucket = []
    for q in range(5):
        sel = buckets == q
        signed = np.sign(gap[sel]) * diff[sel]
        by_bucket.append({"quintile_of_score_gap": q + 1, "hit_rate": float(hit[sel].mean()),
                          "mean_signed_difference": float(signed.mean()),
                          "std_difference": float(diff[sel].std())})
    return {"score": "rank(-20d return) + rank(-60d volatility), pre-declared", "pairs": int(len(diff)),
            "hit_rate": float(hit.mean()), "std_difference": float(diff.std()),
            "switch_cost_round_trip": 0.0025, "by_gap_quintile": by_bucket,
            "hit_rate_by_year": {y: float(hit[year == y].mean()) for y in sorted(set(year.tolist()))}}


def _industry_at(settings: Settings, bars: Bars, rows: np.ndarray) -> np.ndarray:
    group_of = {l2: g for g, names in CYCLICAL_L2.items() for l2 in names}
    col = {c: j for j, c in enumerate(bars.codes)}
    out = np.full((len(rows), len(bars.codes)), "", dtype=object)
    hist = pd.read_parquet(settings.raw_dir / "swsresearch" / "sw_industry_hist.parquet",
                           columns=["code", "effective_date", "l2_name"]).dropna().sort_values("effective_date")
    for i, t in enumerate(rows):
        latest = hist[hist["effective_date"] <= bars.dates[t]].drop_duplicates("code", keep="last")
        latest = latest[latest["l2_name"].isin(group_of)]
        for code, l2 in zip(latest["code"], latest["l2_name"], strict=True):
            if code in col:
                out[i, col[code]] = group_of[l2]
    return out


def probe_p6(settings: Settings, bars: Bars, masks: np.ndarray, h: int = 244) -> dict:
    """Proposition 2: for cyclicals, the book-to-price percentile beats earnings yield at predicting 12-month excess."""
    rows = _sample_rows(bars, h, start="2007-01-01")
    groups = _industry_at(settings, bars, rows)
    mv = bars["circ_mv"][rows].astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        book = mv / bars["pb_mrq"][rows].astype(float)
        earnings = mv / bars["pe_ttm"][rows].astype(float)
    fwd = _forward(bars["close"], bars["open"], rows, h)
    market = np.nanmean(np.where(masks[rows], fwd, np.nan), axis=1)
    result = {}
    for group in CYCLICAL_L2:
        series = []
        for i in range(len(rows)):
            sel = (groups[i] == group) & masks[rows[i]] & np.isfinite(mv[i]) & np.isfinite(book[i])
            sel &= np.isfinite(earnings[i])
            if sel.sum() < 3:
                series.append((np.nan, np.nan, np.nan, 0))
                continue
            bp = book[i, sel].sum() / mv[i, sel].sum()
            ep = earnings[i, sel].sum() / mv[i, sel].sum()
            rel = np.nanmean(fwd[i, sel]) - market[i]
            series.append((bp, ep, rel, int(sel.sum())))
        frame = pd.DataFrame(series, columns=["bp", "ep", "excess", "names"], index=bars.dates[rows])
        for col in ("bp", "ep"):
            frame[f"{col}_pct"] = frame[col].rolling(120, min_periods=36).apply(
                lambda w: (w[:-1] < w[-1]).mean() if np.isfinite(w[-1]) else np.nan, raw=True)
        usable = frame.dropna(subset=["bp_pct", "ep_pct", "excess"])
        if len(usable) < 24:
            result[group] = {"months": int(len(usable)), "note": "too few months"}
            continue
        corr_bp = usable["bp_pct"].corr(usable["excess"], method="spearman")
        corr_ep = usable["ep_pct"].corr(usable["excess"], method="spearman")
        top_ep = usable[usable["ep_pct"] >= 0.8]["excess"].mean()
        result[group] = {"months": int(len(usable)), "median_names": float(frame["names"].median()),
                         "spearman_bp_pct_vs_12m_excess": float(corr_bp),
                         "spearman_ep_pct_vs_12m_excess": float(corr_ep),
                         "bp_beats_ep": bool(corr_bp > corr_ep),
                         "mean_12m_excess_when_ep_pct_top20": float(top_ep) if np.isfinite(top_ep) else None,
                         "mean_12m_excess_all": float(usable["excess"].mean())}
    scored = [v for v in result.values() if "bp_beats_ep" in v]
    return {"industries": result, "industries_scored": len(scored),
            "bp_beats_ep_share": float(np.mean([v["bp_beats_ep"] for v in scored])) if scored else None,
            "bp_positive_share": float(np.mean([v["spearman_bp_pct_vs_12m_excess"] > 0 for v in scored]))
            if scored else None,
            "note": "Monthly overlapping 12-month windows: about 15 independent years per industry, so the "
                    "correlations are descriptive. Industry membership is point-in-time from the SW history; "
                    "valuations are cap-weighted aggregates of BaoStock PE-TTM and PB-MRQ."}


def probe_p7(bars: Bars, masks: np.ndarray, h: int = 244, top_n: int = 100, draws: int = 200, seed: int = 11) -> dict:
    """Screen 3 (quality + value) for concentrated holders, against random picks from the same pool."""
    rows = _sample_rows(bars, h)
    valid = masks[rows]
    pe, pb = bars["pe_ttm"][rows].astype(float), bars["pb_mrq"][rows].astype(float)
    ok = valid & (pe > 0) & (pb > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        score = _rank(1 / pe, ok) + _rank(pb / pe, ok)
    fwd = _forward(bars["close"], bars["open"], rows, h)
    rng = np.random.default_rng(seed)
    out = {}
    for k in (3, 5):
        records = []
        for i in range(len(rows)):
            pool = np.flatnonzero(valid[i] & np.isfinite(fwd[i]))
            ranked = np.flatnonzero(ok[i] & np.isfinite(score[i]) & np.isfinite(fwd[i]))
            if len(ranked) < top_n or len(pool) < top_n:
                continue
            top = ranked[np.argsort(-score[i, ranked])[:top_n]]
            market = fwd[i, pool].mean()
            for kind, universe in (("screen", top), ("random", pool)):
                picks = np.stack([rng.choice(universe, k, replace=False) for _ in range(draws)])
                ret = fwd[i, picks].mean(axis=1)
                records.append(pd.DataFrame({"kind": kind, "year": bars.dates[rows[i]][:4], "ret": ret,
                                             "excess": ret - market}))
        frame = pd.concat(records)
        stats = {}
        for kind, g in frame.groupby("kind"):
            yearly = g.groupby("year")["excess"].median()
            stats[kind] = {"median_excess": float(g["excess"].median()),
                           "p_beat_market": float((g["excess"] > 0).mean()),
                           "p_loss_30": float((g["ret"] < -0.3).mean()),
                           "years_median_excess_positive": float((yearly > 0).mean())}
        out[f"k{k}"] = {**stats, "p_beat_gap_points": 100 * (stats["screen"]["p_beat_market"]
                                                              - stats["random"]["p_beat_market"])}
    return {"screen": "rank(earnings yield) + rank(PB/PE as an ROE proxy), top 100 of the pool, PE and PB > 0",
            "market": "equal-weight pool average", "results": out,
            "missing": "financial red-flag exclusion is not applied (needs the full-A red-flag history)"}


EVENT_PATTERNS = {
    "减持计划": (r"减持", r"计划|预披露"), "减持结果": (r"减持", r"完成|结果|实施完毕|届满"),
    "回购方案": (r"回购", r"预案|方案"), "限售解禁": (r"解除限售|限售股.*上市流通", None),
    "问询与关注函": (r"问询函|关注函|监管函", None), "业绩预告修正": (r"业绩预告修正", None),
}
EXCLUDE = r"回复|答复|更正|补充|摘要|英文|取消|终止|法律意见|核查意见"


def probe_p4(settings: Settings, bars: Bars, masks: np.ndarray, horizons: tuple[int, ...] = (5, 20, 60)) -> dict:
    """Cumulative excess return after pre-declared announcement types, entered at the next open."""
    limit = bars.dates[-1]
    files = sorted(p for p in (settings.raw_dir / "cninfo" / "announcements").glob("*.parquet") if p.stem <= limit)
    if not files:
        return {"note": "no dev-tier announcements"}
    ann = pd.concat([pd.read_parquet(p, columns=["code", "title", "published_date"]) for p in files])
    ann = ann[~ann["title"].str.contains(EXCLUDE, regex=True)]
    col = {c: j for j, c in enumerate(bars.codes)}
    close = pd.DataFrame(bars["close"]).ffill().to_numpy()
    open_ = bars["open"]
    pool_ret = {}
    out = {"announcement_days": len(files), "first_day": files[0].stem, "last_day": files[-1].stem, "events": {}}
    for kind, (first, second) in EVENT_PATTERNS.items():
        hit = ann["title"].str.contains(first, regex=True)
        if second:
            hit &= ann["title"].str.contains(second, regex=True)
        events = ann[hit].drop_duplicates(["code", "published_date"])
        rows = np.searchsorted(bars.dates, events["published_date"].to_numpy(), side="right")
        cols = events["code"].map(lambda c: col.get(normalize_code(c)) if str(c).isdigit() else col.get(c))
        keep = cols.notna().to_numpy() & (rows < len(bars.dates) - max(horizons))
        rows, cols = rows[keep], cols[keep].astype(int).to_numpy()
        months = np.array([d[:7] for d in bars.dates[rows]])
        stats = {"events": int(len(rows))}
        for h in horizons:
            if h not in pool_ret:
                with np.errstate(invalid="ignore", divide="ignore"):
                    ret = close[np.minimum(np.arange(len(close)) + h - 1, len(close) - 1)] / open_ - 1
                pool_ret[h] = (ret, np.nanmean(np.where(masks, ret, np.nan), axis=1))
            ret, market = pool_ret[h]
            car = ret[rows, cols] - market[rows]
            ok = np.isfinite(car)
            stats[f"car{h}"] = {"mean": float(car[ok].mean()) if ok.any() else None,
                                "t_by_month": _clustered_month_t(car[ok], months[ok])}
        out["events"][kind] = stats
    out["note"] = ("Titles matched by pre-declared patterns; replies, corrections and cancellations dropped. Entry is "
                   "the open of the first trading day after the publication date. CAR is against the equal-weight "
                   "all-A pool. Only the dev days present locally are used.")
    return out


def _clustered_month_t(values: np.ndarray, months: np.ndarray) -> float | None:
    if len(values) < 30:
        return None
    means = pd.Series(values).groupby(months).mean()
    if len(means) < 6 or means.std(ddof=1) == 0:
        return None
    return float(means.mean() / (means.std(ddof=1) / np.sqrt(len(means))))


def run_probe(settings: Settings, name: str) -> dict:
    bars = load_bars(settings)
    masks = pool_masks(bars, "all")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        if name == "p1":
            return probe_p1(bars, trailing_vol(bars), masks)
        if name == "p2":
            return probe_p2(bars, trailing_vol(bars), masks)
        if name == "p6":
            return probe_p6(settings, bars, masks)
        if name == "p7":
            return probe_p7(bars, masks)
        if name == "p4":
            return probe_p4(settings, bars, masks)
    raise ValueError(name)
