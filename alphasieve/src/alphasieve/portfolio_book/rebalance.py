"""Factual, advisory-only book limit and drift observations."""

import yaml

from alphasieve.config import Settings
from alphasieve.errors import validation_error
from alphasieve.portfolio_book.attribution import _thesis_links, industry_asof
from alphasieve.portfolio_book.importer import normalize_code, show_snapshot


def build_rebalance(conn, settings: Settings, snapshot_id: str | None = None) -> dict:
    if snapshot_id:
        snapshot = show_snapshot(conn, snapshot_id)
    else:
        row = conn.execute(
            "SELECT snapshot_id FROM holdings_snapshots ORDER BY as_of DESC, created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise validation_error("no holdings snapshots")
        snapshot = show_snapshot(conn, row["snapshot_id"])
    path = settings.config_dir / "book" / "limits.yaml"
    limits = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("single_name_max", "industry_max", "drift_absolute"):
        value = limits.get(key)
        if not isinstance(value, (int, float)) or not 0 < value <= 1:
            raise validation_error("invalid book limit", key=key)
    target_weights = limits.get("target_weights") or {}
    if not isinstance(target_weights, dict):
        raise validation_error("target_weights must be a mapping")
    targets = {}
    for code, value in target_weights.items():
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise validation_error("invalid target weight", code=code)
        targets[normalize_code(code)] = float(value)
    positions = snapshot["positions"]
    total = sum(float(p["market_value"]) for p in positions)
    if total <= 0:
        raise validation_error("holdings total market value must be positive")
    codes = [p["code"] for p in positions if p["code"] != "CASH"]
    links, caps = _thesis_links(conn, codes)
    industry, meta = industry_asof(settings, codes, snapshot["as_of"])
    sector_weights = {}
    for p in positions:
        code = p["code"]
        if code == "CASH":
            continue
        sector = industry.get(code, "未知")
        sector_weights[sector] = sector_weights.get(sector, 0.0) + float(p["market_value"]) / total
    rows = []
    cash_available = sum(float(p["market_value"]) for p in positions if p["code"] == "CASH")
    for p in positions:
        code = p["code"]
        if code == "CASH":
            continue
        weight = float(p["market_value"]) / total
        tid = links.get(code)
        cap = min(limits["single_name_max"], caps[tid]) if tid in caps else limits["single_name_max"]
        sector = industry.get(code, "未知")
        reasons = []
        if tid in caps and weight > caps[tid]:
            reasons.append(f"论点 {tid} 仓位 {weight:.1%} 超过上限 {caps[tid]:.1%}")
        if weight > limits["single_name_max"]:
            reasons.append(f"单一证券权重 {weight:.1%} 超过上限 {limits['single_name_max']:.1%}")
        if sector != "未知" and sector_weights[sector] > limits["industry_max"]:
            reasons.append(f"{sector} 行业权重 {sector_weights[sector]:.1%} 超过上限 {limits['industry_max']:.1%}")
        excess = max(weight - cap, 0)
        if sector != "未知" and sector_weights[sector] > limits["industry_max"]:
            excess = max(excess, weight * (sector_weights[sector] - limits["industry_max"]) / sector_weights[sector])
        action = "trim" if excess > limits["drift_absolute"] or (excess > 0 and reasons) else "hold"
        target = targets.get(code)
        add = 0.0
        if action == "hold" and target is not None and target - weight >= limits["drift_absolute"]:
            headroom = min(
                cap - weight, limits["industry_max"] - sector_weights[sector] if sector != "未知" else cap - weight
            )
            add = max(0.0, min(target - weight, headroom, cash_available / total)) * total
            if add > 0:
                action = "add"
                reasons.append(f"当前权重 {weight:.1%} 低于已配置目标 {target:.1%}，且上限与现金允许")
                cash_available -= add
        rows.append(
            {
                "code": code,
                "name": p["name"],
                "weight": weight,
                "industry": sector,
                "thesis_id": tid,
                "cap": cap,
                "target_weight": target,
                "action": action,
                "suggested_trim_value": round(excess * total, 2) if action == "trim" else 0.0,
                "suggested_add_value": round(add, 2),
                "reasons": reasons,
            }
        )
    return {
        "snapshot_id": snapshot["snapshot_id"],
        "account": snapshot["account"],
        "as_of": snapshot["as_of"],
        "total_value": total,
        "rows": rows,
        "limits": limits,
        "industry_weights": sector_weights,
        "industry_meta": meta,
        "summary": {
            "flagged": sum(bool(r["reasons"]) for r in rows),
            "suggested_trim_total": sum(r["suggested_trim_value"] for r in rows),
        },
        "method": "Advisory limit comparison only. Trims are approximate value reductions at snapshot valuations; "
        "no buy or sell orders. Adds require an explicit configured target and are bounded by cash and limits.",
    }
