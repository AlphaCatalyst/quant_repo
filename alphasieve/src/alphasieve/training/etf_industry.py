"""Industry-level features of the sector ETFs for mandate B (docs/mandates/training-round2 §4).

westock only serves each ETF's current top-20 holdings and the panel's industry is a current CSRC snapshot, so every
mapping here is fixed at ``mapping_asof`` and applied to all history. Two mappings are built:

- ``industry_map`` (headline): the holdings' industries up to 80% of the matched weight; each day every stock
  feature is ranked across the market, cap-weighted into industries, ranked across industries and summed with the
  ETF's industry weights. The ETF inherits whole-industry behaviour, not the behaviour of today's constituents.
- ``basket_map`` (bias bound): the same stock ranks weighted by today's holdings directly; closer to the ETF but
  with stronger look-ahead in membership. A day where held names with data carry < 70% of the weight is missing.
"""

import json

import numpy as np
import pandas as pd

from alphasieve.config import Settings
from alphasieve.data.etf import ETF_UNIVERSE, etf_root
from alphasieve.errors import validation_error
from alphasieve.util import file_sha256, sha256_hex, utcnow_iso

MODES = ("industry_map", "basket_map")
INDUSTRY_COVER = 0.8
MIN_BASKET_WEIGHT = 0.7
MIN_INDUSTRY_NAMES = 5
FIELDS = ["date", "code", "industry", "close", "close_raw", "limit_up", "circ_mv", "turnover_rate", "pe_ttm",
          "pb_mrq", "ws_np_surprise_q", "ws_np_growth_q_yoy", "mf_main_net_ratio", "in_universe"]


def stock_code(code: str) -> str:
    return ("sh." if code[0] in "69" else "bj." if code[0] in "48" else "sz.") + code


def etf_code(code: str) -> str:
    return f"{code[:2]}.{code[2:]}"


def load_holdings(settings: Settings, asof: str) -> tuple[dict, str]:
    path = etf_root(settings) / "holdings" / f"{asof}.json"
    if not path.exists():
        raise validation_error(f"no ETF holdings snapshot for {asof}; run sync_etf_holdings")
    return json.loads(path.read_text()), file_sha256(path)


def _pct(frame: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    return frame.where(mask).rank(axis=1, pct=True)


def stock_ranks(long: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.Series]:
    """Per-day cross-sectional percentile of each stock feature inside the all-A universe."""
    wide = {f: long.pivot(index="date", columns="code", values=f).astype(float)
            for f in FIELDS if f not in ("date", "code", "industry", "in_universe")}
    uni = long.pivot(index="date", columns="code", values="in_universe").reindex_like(wide["close"])
    uni = uni.fillna(False).astype(bool)
    close = wide["close"]
    turn = wide["turnover_rate"]
    feats = {"mom_20": close / close.shift(20) - 1, "mom_60": close / close.shift(60) - 1,
             "mom_120": close / close.shift(120) - 1, "rev_5": -(close / close.shift(5) - 1),
             "dist_high_250": close / close.rolling(250, min_periods=120).max() - 1,
             "flow_5": wide["mf_main_net_ratio"].rolling(5, min_periods=3).mean(),
             "flow_20": wide["mf_main_net_ratio"].rolling(20, min_periods=10).mean(),
             "surprise": wide["ws_np_surprise_q"],
             "growth_pos": (wide["ws_np_growth_q_yoy"] > 0).astype(float).where(wide["ws_np_growth_q_yoy"].notna()),
             "ep": 1 / wide["pe_ttm"].where(wide["pe_ttm"] > 0), "bp": 1 / wide["pb_mrq"].where(wide["pb_mrq"] > 0),
             "turnover_rel": turn.rolling(20, min_periods=10).mean() / turn.rolling(250, min_periods=120).mean(),
             "limit_up_20": (wide["close_raw"] >= wide["limit_up"] * 0.9995).astype(float)
             .where(wide["close_raw"].notna()).rolling(20, min_periods=10).sum()}
    ranks = {k: (v if k == "growth_pos" else _pct(v, uni)).where(uni) for k, v in feats.items()}
    industry = long.dropna(subset=["industry"]).groupby("code")["industry"].last().reindex(close.columns)
    cap = wide["circ_mv"].where(uni & (wide["circ_mv"] > 0))
    return ranks, cap, industry


def _weighted_mean(x: pd.DataFrame, w: pd.DataFrame, onehot: np.ndarray) -> np.ndarray:
    ok = x.notna().to_numpy() & w.notna().to_numpy()
    wv = np.where(ok, w.to_numpy(), 0.0)
    num = np.nan_to_num(x.to_numpy() * wv) @ onehot
    den = wv @ onehot
    return np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)


def _rank_z_rows(a: np.ndarray) -> np.ndarray:
    # aggregates of tied ranks differ only by float noise; round so that ties stay ties whatever the sample length
    r = pd.DataFrame(np.round(a, 10)).rank(axis=1)
    n = r.notna().sum(axis=1).to_numpy()[:, None]
    z = (r.to_numpy() - (n + 1) / 2) / np.sqrt(np.maximum(n * n - 1, 1) / 12)
    return np.where(n >= 3, z, np.nan)


def industry_weights(holdings: dict, industry: pd.Series) -> dict[str, dict[str, float]]:
    out = {}
    for etf, rows in holdings.items():
        w: dict[str, float] = {}
        for r in rows:
            ind = industry.get(stock_code(r["code"]))
            if isinstance(ind, str):
                w[ind] = w.get(ind, 0.0) + r["weight"]
        total = sum(w.values())
        keep, acc = {}, 0.0
        for ind, v in sorted(w.items(), key=lambda kv: -kv[1]):
            if acc >= INDUSTRY_COVER * total:
                break
            keep[ind] = v
            acc += v
        s = sum(keep.values())
        out[etf] = {k: v / s for k, v in keep.items()} if s > 0 else {}
    return out


def build_features(long: pd.DataFrame, holdings: dict, dates: pd.DatetimeIndex) -> tuple[dict, dict]:
    ranks, cap, industry = stock_ranks(long)
    codes = list(cap.columns)
    inds = sorted(industry.dropna().unique())
    onehot = np.zeros((len(codes), len(inds)))
    pos = {g: i for i, g in enumerate(inds)}
    for j, g in enumerate(industry.to_numpy()):
        if isinstance(g, str):
            onehot[j, pos[g]] = 1.0
    names = (cap.notna().to_numpy().astype(float) @ onehot)
    thin = names < MIN_INDUSTRY_NAMES
    ind_feats = {k: np.where(thin, np.nan, _weighted_mean(v, cap, onehot)) for k, v in ranks.items()}
    eq = cap.notna().astype(float).where(cap.notna())
    m1 = _weighted_mean(ranks["mom_20"], eq, onehot)
    m2 = _weighted_mean(ranks["mom_20"] ** 2, eq, onehot)
    ind_feats["dispersion_20"] = np.where(thin, np.nan, np.sqrt(np.clip(m2 - m1 ** 2, 0, None)))
    ep_ind = pd.DataFrame(ind_feats["ep"])
    ind_feats["ep_hist_z"] = ((ep_ind - ep_ind.rolling(750, min_periods=250).mean())
                              / ep_ind.rolling(750, min_periods=250).std()).to_numpy()
    iw = industry_weights(holdings, industry)
    etfs = [e for e in ETF_UNIVERSE if e in holdings]
    E = np.zeros((len(etfs), len(inds)))
    for i, e in enumerate(etfs):
        for g, v in iw[e].items():
            E[i, pos[g]] = v
    idx = cap.index
    out = {m: {} for m in MODES}
    for k, grid in ind_feats.items():
        z = _rank_z_rows(grid)
        ok = np.isfinite(z)
        num = np.nan_to_num(z) @ E.T
        den = ok.astype(float) @ E.T
        out["industry_map"][f"ind_{k}"] = pd.DataFrame(np.where(den >= 0.5, num / np.where(den > 0, den, 1), np.nan),
                                                       index=idx, columns=[etf_code(e) for e in etfs])
    col = {c: j for j, c in enumerate(codes)}
    S = np.zeros((len(etfs), len(codes)))
    for i, e in enumerate(etfs):
        for r in holdings[e]:
            j = col.get(stock_code(r["code"]))
            if j is not None:
                S[i, j] = r["weight"]
    matched = S.sum(axis=1)
    for k, v in {**ranks}.items():
        x = v.to_numpy()
        ok = np.isfinite(x)
        num = np.nan_to_num(x) @ S.T
        den = ok.astype(float) @ S.T
        good = den >= MIN_BASKET_WEIGHT * np.maximum(matched, 1e-12)
        out["basket_map"][f"ind_{k}"] = pd.DataFrame(np.where(good, num / np.where(den > 0, den, 1), np.nan),
                                                     index=idx, columns=[etf_code(e) for e in etfs])
    bm = out["basket_map"]
    ep_b = bm["ind_ep"]
    bm["ind_ep_hist_z"] = (ep_b - ep_b.rolling(750, min_periods=250).mean()) / ep_b.rolling(750, min_periods=250).std()
    for m in MODES:
        out[m] = {k: f.reindex(dates) for k, f in out[m].items()}
    info = {"industries": len(inds), "etfs": len(etfs),
            "industry_weights": {e: {g: round(v, 4) for g, v in iw[e].items()} for e in etfs},
            "basket_matched_weight": {e: round(float(matched[i]), 4) for i, e in enumerate(etfs)}}
    return out, info


def mapping_agreement(feats: dict, real: pd.DataFrame) -> dict:
    """Per shared feature on real ETF rows: mean cross-sectional rank correlation and mean |rank-z gap| between
    the two mappings, plus each mapping's coverage. Uses no returns."""
    out = {}
    for name in sorted(set(feats["industry_map"]) & set(feats["basket_map"])):
        a = feats["industry_map"][name].reindex_like(real).where(real).to_numpy(float)
        b = feats["basket_map"][name].reindex_like(real).where(real).to_numpy(float)
        both = np.isfinite(a) & np.isfinite(b)
        za = _rank_z_rows(np.where(both, a, np.nan))
        zb = _rank_z_rows(np.where(both, b, np.nan))
        ok = np.isfinite(za) & np.isfinite(zb)
        with np.errstate(invalid="ignore", divide="ignore"):
            corr = [np.corrcoef(za[t, ok[t]], zb[t, ok[t]])[0, 1] for t in range(len(za)) if ok[t].sum() >= 5]
        out[name] = {"rank_corr_mean": round(float(np.nanmean(corr)), 3) if corr else None,
                     "abs_z_gap_mean": round(float(np.abs(za - zb)[ok].mean()), 3) if ok.any() else None,
                     "coverage": {"industry_map": round(float(np.isfinite(a)[real.to_numpy()].mean()), 3),
                                  "basket_map": round(float(np.isfinite(b)[real.to_numpy()].mean()), 3)}}
    return out


def build_etf_industry(settings: Settings, asof: str, tiers: tuple[str, ...] = ("dev",)) -> dict:
    import pyarrow.parquet as pq

    holdings, holdings_sha = load_holdings(settings, asof)
    results = {}
    for tier in tiers:
        src = settings.panel_dir(tier, "ashare_all") / "panel.parquet"
        etf_dir = settings.panel_dir(tier, "etf_sector")
        etf = pd.read_parquet(etf_dir / "panel.parquet", columns=["date", "code", "is_proxy"])
        etf["date"] = pd.to_datetime(etf["date"])
        real = etf.assign(real=~etf["is_proxy"].astype(bool)).pivot(index="date", columns="code", values="real")
        dates = pd.DatetimeIndex(real.index)
        long = pq.read_table(src, columns=FIELDS).to_pandas()
        long["date"] = pd.to_datetime(long["date"])
        feats, info = build_features(long, holdings, dates)
        del long
        meta = {"mapping_asof": asof, "holdings_sha256": holdings_sha, "source_panel": str(src), "tier": tier,
                "built_at": utcnow_iso(), **info,
                "agreement_on_real_etf_rows": mapping_agreement(feats, real.fillna(False).astype(bool)),
                "warnings": ["current top-20 holdings and current CSRC industries are applied to all history:"
                             " membership look-ahead, strongest in basket_map"]}
        for mode, frames in feats.items():
            long_out = pd.concat({k: v.stack(future_stack=True) for k, v in frames.items()}, axis=1)
            long_out.index.names = ["date", "code"]
            path = etf_dir / f"etf_industry_{mode}.parquet"
            long_out.reset_index().to_parquet(path, index=False)
            if tier != "dev":
                path.chmod(0o600)
            meta[mode] = {"path": str(path), "sha256": file_sha256(path),
                          "coverage": {k: round(float(v.notna().to_numpy().mean()), 3) for k, v in frames.items()}}
        (etf_dir / "etf_industry_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        results[tier] = {m: meta[m] for m in MODES} | {"signature": sha256_hex(json.dumps(
            [meta[m]["sha256"] for m in MODES]))[:16]}
    return results


def read_etf_industry(settings: Settings, tier: str, role: str, mode: str) -> tuple[dict[str, pd.DataFrame], dict]:
    from alphasieve.data.access import check_tier_access

    check_tier_access(role, tier)
    etf_dir = settings.panel_dir(tier, "etf_sector")
    path = etf_dir / f"etf_industry_{mode}.parquet"
    if not path.exists():
        raise validation_error(f"no {mode} ETF industry features for the {tier} tier; run"
                               f" 'alphasieve data build-etf-industry --tiers {tier}'")
    long = pd.read_parquet(path)
    long["date"] = pd.to_datetime(long["date"])
    meta = json.loads((etf_dir / "etf_industry_meta.json").read_text())
    frames = {c: long.pivot(index="date", columns="code", values=c) for c in long.columns if c not in ("date", "code")}
    return frames, {"mode": mode, "mapping_asof": meta["mapping_asof"], "sha256": file_sha256(path),
                    "holdings_sha256": meta["holdings_sha256"]}
