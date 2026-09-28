"""Derived fields from westock statements (all A-shares, 2000+) and daily fund flow (2020+).

Statement rows are merged per (code, report period) and become usable on the first trading day strictly after
the latest announcement date of the three statements (``align_financials_pit``). westock keeps one version per
period: half-year values match BaoStock's first publication, but about a third of annual balance sheets carry
later restatements (median 0.4% on leverage), a residual look-ahead documented in D-30.
"""

import numpy as np
import pandas as pd

STATEMENT_FIELDS = [
    "ws_roe_ttm", "ws_roa_ttm", "ws_gross_margin_ttm", "ws_op_margin_ttm", "ws_cfoa_ttm", "ws_accruals_ttm",
    "ws_debt_to_assets", "ws_ibd_to_equity", "ws_goodwill_to_equity", "ws_cash_to_assets", "ws_rd_to_rev_q",
    "ws_asset_growth_yoy", "ws_rev_growth_yoy", "ws_np_growth_q_yoy", "ws_np_surprise_q",
    "ws_np_ttm", "ws_rev_ttm", "ws_ocf_ttm", "ws_equity",
]
FLOW_RATIOS = {"mf_main_net_ratio": "MainNetFlow", "mf_jumbo_net_ratio": "JumboNetFlow",
               "mf_block_net_ratio": "BlockNetFlow", "mf_mid_net_ratio": "MidNetFlow",
               "mf_small_net_ratio": "SmallNetFlow"}
FLOW_FIELDS = list(FLOW_RATIOS)


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    return df[name] if name in df.columns else pd.Series(np.nan, index=df.index)


def _ratio(num: pd.Series, den: pd.Series) -> pd.Series:
    """num / den for a strictly positive denominator, NaN otherwise."""
    return num / den.where(den > 0)


def statement_rows(lrb: pd.DataFrame, zcfz: pd.DataFrame, xjll: pd.DataFrame) -> pd.DataFrame:
    """One row per (code, statDate) with pubDate and the derived ``ws_*`` fields."""
    parts = [df.rename(columns={"InfoPublDate": f"pub_{k}"}).drop(columns=["EnterpriseType"], errors="ignore")
             for k, df in (("lrb", lrb), ("zcfz", zcfz), ("xjll", xjll)) if not df.empty]
    if not parts:
        return pd.DataFrame(columns=["code", "statDate", "pubDate", *STATEMENT_FIELDS])
    df = parts[0]
    for part in parts[1:]:
        dup = [c for c in part.columns if c in df.columns and c not in ("code", "EndDate")]
        df = df.merge(part.drop(columns=dup), on=["code", "EndDate"], how="outer")
    pubs = df[[c for c in df.columns if c.startswith("pub_")]].replace("", np.nan)
    np_ttm, rev_ttm = _col(df, "NPParentCompanyOwnersTTM"), _col(df, "TotalOperatingRevenueTTM")
    ocf_ttm, liab = _col(df, "NetOperateCashFlowTTM"), _col(df, "TotalLiability")
    assets = liab + _col(df, "TotalShareholderEquity")
    equity = _col(df, "SEWithoutMI")
    out = pd.DataFrame({"code": df["code"], "statDate": df["EndDate"], "pubDate": pubs.max(axis=1)})
    out["ws_roe_ttm"] = _ratio(np_ttm, equity)
    out["ws_roa_ttm"] = _ratio(np_ttm, assets)
    out["ws_gross_margin_ttm"] = _ratio(_col(df, "GrossProfitTTM"), rev_ttm)
    out["ws_op_margin_ttm"] = _ratio(_col(df, "OperatingProfitTTM"), rev_ttm)
    out["ws_cfoa_ttm"] = _ratio(ocf_ttm, assets)
    out["ws_accruals_ttm"] = _ratio(np_ttm - ocf_ttm, assets)
    out["ws_debt_to_assets"] = _ratio(liab, assets)
    out["ws_ibd_to_equity"] = _ratio(_col(df, "InterestBearDebt"), equity)
    out["ws_goodwill_to_equity"] = _ratio(_col(df, "GoodWill").fillna(0.0), equity)
    out["ws_cash_to_assets"] = _ratio(_col(df, "CashEquivalents"), assets)
    out["ws_rd_to_rev_q"] = _ratio(_col(df, "RAndD_Q"), _col(df, "TotalOperatingRevenue_Q"))
    out["ws_np_ttm"], out["ws_rev_ttm"], out["ws_ocf_ttm"], out["ws_equity"] = np_ttm, rev_ttm, ocf_ttm, equity
    out["_assets"], out["_np_q"] = assets, _col(df, "NPParentCompanyOwners_Q")
    prev = out[["code", "statDate", "_assets", "ws_rev_ttm", "_np_q"]].rename(
        columns={"_assets": "p_assets", "ws_rev_ttm": "p_rev", "_np_q": "p_np_q"})
    prev["statDate"] = (prev["statDate"].str[:4].astype(int) + 1).astype(str) + prev["statDate"].str[4:]
    out = out.merge(prev, on=["code", "statDate"], how="left")
    out["ws_asset_growth_yoy"] = _ratio(out["_assets"], out["p_assets"]) - 1
    out["ws_rev_growth_yoy"] = _ratio(out["ws_rev_ttm"], out["p_rev"]) - 1
    out["ws_np_growth_q_yoy"] = (out["_np_q"] - out["p_np_q"]) / out["p_np_q"].abs().where(out["p_np_q"] != 0)
    out["ws_np_surprise_q"] = _ratio(out["_np_q"] - out["p_np_q"], out["_assets"])
    out = out.replace([np.inf, -np.inf], np.nan)
    return out[["code", "statDate", "pubDate", *STATEMENT_FIELDS]].sort_values(["code", "statDate"])


def attach_fund_flow(panel: pd.DataFrame, flow: pd.DataFrame) -> pd.DataFrame:
    """Same-day flows scaled by traded amount; known after the close like the close price itself."""
    flow = flow.assign(date=pd.to_datetime(flow["date"])).astype({"code": panel["code"].dtype})
    merged = panel.merge(flow, on=["date", "code"], how="left")
    amount = merged["amount"].where(merged["amount"] > 0)
    for name, src in FLOW_RATIOS.items():
        merged[name] = merged[src] / amount if src in merged.columns else np.nan
    return merged.drop(columns=[c for c in flow.columns if c not in ("date", "code")])
