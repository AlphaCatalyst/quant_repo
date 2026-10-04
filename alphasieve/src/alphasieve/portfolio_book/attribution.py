"""Book contribution and auditable industry/thesis attribution."""

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml

from alphasieve.config import Settings
from alphasieve.errors import AlphaSieveError
from alphasieve.portfolio_book.history import build_history
from alphasieve.portfolio_book.importer import normalize_code


def industry_asof(settings: Settings, codes: list[str], as_of: str) -> tuple[dict[str, str], dict]:
    path = settings.raw_dir / "swsresearch" / "sw_industry_hist.parquet"
    mapping = {}
    if path.is_file():
        frame = pd.read_parquet(path, columns=["code", "effective_date", "l1_name"])
        frame = frame[(frame["effective_date"].astype(str) <= as_of) & frame["code"].isin(codes)]
        frame = frame.sort_values("effective_date").drop_duplicates("code", keep="last")
        mapping = dict(zip(frame["code"], frame["l1_name"], strict=True))
    missing = [code for code in codes if code not in mapping]
    fallback = {}
    if missing:
        sw_dir = settings.raw_dir / "westock" / "sw_industry"
        files = sorted(sw_dir.glob("*.parquet")) if sw_dir.is_dir() else []
        if files:
            current = pd.read_parquet(files[-1])
            if {"code", "level", "sector_name"}.issubset(current.columns):
                current = current[(current["level"].astype(str) == "1") & current["code"].isin(missing)]
                fallback = dict(zip(current["code"], current["sector_name"], strict=True))
    mapping.update(fallback)
    return mapping, {
        "source": "swsresearch SW L1 PIT",
        "as_of": as_of,
        "fallback_codes": sorted(fallback),
        "fallback_label": "current snapshot, not PIT" if fallback else None,
        "unclassified_codes": sorted(set(codes) - set(mapping)),
    }


def _thesis_links(conn, codes: list[str]) -> tuple[dict[str, str], dict[str, float]]:
    root = Path(__file__).resolve().parents[3] / "theses"
    links, caps = {}, {}
    for path in sorted([*root.glob("*.yaml"), *root.glob("*.yml")]):
        try:
            item = yaml.safe_load(path.read_text(encoding="utf-8"))
            tid = item["thesis_id"]
            caps[tid] = float(item["position_limit"])
            for asset in item.get("assets", []):
                code = normalize_code(asset["code"])
                if code in codes:
                    links[code] = tid
        except (KeyError, ValueError, TypeError):
            continue
    rows = conn.execute(
        "SELECT thesis_id, payload_json FROM journal_entries WHERE thesis_id IS NOT NULL ORDER BY seq"
    ).fetchall()
    for row in rows:
        import json

        code = json.loads(row["payload_json"]).get("code")
        if code:
            try:
                code = normalize_code(code)
            except AlphaSieveError:
                continue
            if code in codes and code not in links:
                links[code] = row["thesis_id"]
    return links, caps


def _benchmark_sectors(settings: Settings, start: str, end: str) -> tuple[dict[str, float], dict[str, float], dict]:
    folder = settings.raw_dir / "csindex" / "weights" / "000300"
    files = sorted(p for p in folder.glob("*.parquet") if p.stem <= start) if folder.is_dir() else []
    if files:
        path = files[-1]
        snapshot_date = path.stem
        weights = pd.read_parquet(path, columns=["code", "weight"])
        source_label = "csindex official CSI300 weights"
    else:
        path = settings.raw_dir / "dolthub" / "index_weights" / "399300.SZ.parquet"
        if not path.is_file():
            return {}, {}, {"status": "unavailable", "reason": "no CSI300 weights on or before period start"}
        frame = pd.read_parquet(path, columns=["stock_code", "trade_date", "weight"])
        frame = frame[frame["trade_date"].astype(str) <= start]
        if frame.empty:
            return {}, {}, {"status": "unavailable", "reason": "no CSI300 weights on or before period start"}
        snapshot_date = str(frame["trade_date"].max())
        frame = frame[frame["trade_date"].astype(str) == snapshot_date]
        weights = frame.rename(columns={"stock_code": "code"})[["code", "weight"]].copy()
        weights["code"] = weights["code"].map(normalize_code)
        source_label = "DoltHub CSI300 weights fallback"
    codes = weights["code"].astype(str).tolist()
    industries, meta = industry_asof(settings, codes, snapshot_date)
    sector_weight = defaultdict(float)
    sector_return_value = defaultdict(float)
    sector_covered = defaultdict(float)
    covered = 0.0
    for row in weights.itertuples(index=False):
        code, weight = str(row.code), float(row.weight) / 100
        sector = industries.get(code, "未知")
        sector_weight[sector] += weight
        local = settings.raw_dir / "baostock_all" / "daily" / f"{code}.parquet"
        if not local.is_file():
            continue
        frame = pd.read_parquet(local, columns=["date", "close"])
        frame = frame[(frame["date"] >= start) & (frame["date"] <= end)].sort_values("date")
        if len(frame) < 2:
            continue
        if str(frame.iloc[-1]["date"]) < (date.fromisoformat(end) - timedelta(days=4)).isoformat():
            continue
        first, last = float(frame.iloc[0]["close"]), float(frame.iloc[-1]["close"])
        if first > 0:
            sector_return_value[sector] += weight * (last / first - 1)
            sector_covered[sector] += weight
            covered += weight
    sector_returns = {
        sector: sector_return_value[sector] / weight
        for sector, weight in sector_weight.items()
        if weight and sector_covered[sector] / weight >= 0.99
    }
    return (
        dict(sector_weight),
        sector_returns,
        {
            "status": "partial" if covered < 0.99 else "complete",
            "source": str(path),
            "source_label": source_label,
            "snapshot_date": snapshot_date,
            "price_source": "baostock_all/daily close",
            "constituent_weight_with_prices": covered,
            "industry": meta,
        },
    )


def build_attribution(
    conn,
    settings: Settings,
    start: str,
    end: str,
    by: str = "position",
    account: str | None = None,
    benchmark: str = "sh.000300",
) -> dict:
    if by not in {"position", "industry", "thesis"}:
        raise ValueError("by must be position, industry or thesis")
    history = build_history(conn, settings, start, end, account, benchmark)
    rows = [r for r in history["rows"] if r.get("positions")]
    contribution = defaultdict(float)
    for row in rows:
        for code, value in row.get("contribution", {}).items():
            contribution[code] += value
    all_codes = sorted({code for row in rows for code in row["positions"]})
    links, _ = _thesis_links(conn, all_codes)
    industry, industry_meta = industry_asof(settings, all_codes, start)
    total = sum(contribution.values())
    if by == "position":
        groups = {code: value for code, value in contribution.items()}
    elif by == "thesis":
        groups = defaultdict(float)
        for code, value in contribution.items():
            groups[links.get(code, "core")] += value
    else:
        groups = defaultdict(float)
        for code, value in contribution.items():
            groups[industry.get(code, "未知")] += value
    output = [{"name": name, "contribution": value} for name, value in sorted(groups.items())]
    benchmark_meta = {}
    if by == "industry":
        if benchmark == "sh.000300":
            bw, br, benchmark_meta = _benchmark_sectors(settings, start, end)
        else:
            bw, br = {}, {}
            benchmark_meta = {"status": "unavailable", "reason": "industry weights unavailable for this benchmark"}
        first = rows[0]
        portfolio_weights = defaultdict(float)
        for code, p in first["positions"].items():
            portfolio_weights[industry.get(code, "未知")] += p["market_value"] / first["nav"]
        portfolio_returns = {}
        for name, value in groups.items():
            w = portfolio_weights.get(name, 0)
            if w:
                portfolio_returns[name] = value / w
        for item in output:
            name = item["name"]
            wp, wb = portfolio_weights.get(name, 0), bw.get(name, 0)
            rp, rb = portfolio_returns.get(name), br.get(name)
            item.update(
                {
                    "portfolio_weight_start": wp,
                    "benchmark_weight": wb,
                    "portfolio_industry_return": rp,
                    "benchmark_industry_return": rb,
                    "allocation": (wp - wb) * rb if rb is not None else None,
                    "selection": wp * (rp - rb) if rp is not None and rb is not None else None,
                }
            )
        # Include benchmark-only sectors, so the allocation sum is not biased upward.
        for name in sorted(set(bw) - set(groups)):
            rb = br.get(name)
            output.append(
                {
                    "name": name,
                    "contribution": 0.0,
                    "portfolio_weight_start": 0.0,
                    "benchmark_weight": bw[name],
                    "portfolio_industry_return": None,
                    "benchmark_industry_return": rb,
                    "allocation": -bw[name] * rb if rb is not None else None,
                    "selection": None,
                }
            )
    return {
        "account": history["account"],
        "start": start,
        "end": end,
        "by": by,
        "rows": output,
        "summary": {
            "position_contribution_sum": total,
            "portfolio_return": history["summary"]["total_return"],
            "benchmark_return": history["summary"]["benchmark_return"],
            "residual": history["summary"]["total_return"] - total,
        },
        "industry_meta": industry_meta,
        "benchmark_industry_meta": benchmark_meta,
        "thesis_links": links,
        "risk_model_hook": None,
        "method": "Position P&L contribution sums prior-quantity close changes / prior NAV (arithmetic). "
        "Industry allocation=(portfolio start weight-benchmark official weight)*benchmark sector return; "
        "selection=portfolio start weight*(portfolio sector return-benchmark sector return). "
        "Missing benchmark sector prices leave effects null; residual includes trading and cash-flow timing.",
    }
