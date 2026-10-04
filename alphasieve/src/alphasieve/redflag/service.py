"""Deterministic financial statement screening; no model-generated numbers."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from alphasieve.errors import validation_error

FINANCIAL = {"银行", "非银金融"}
KINDS = ("lrb", "zcfz", "xjll")


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    formula: str
    amber: float | None
    red: float | None
    fields: tuple[str, ...]
    rationale: str
    direction: str = "high"
    available: bool = True


RULES = (
    Rule(
        "receivables_growth",
        "应收增速超营收",
        "应收票据及账款同比增速－营业收入TTM同比增速",
        0.20,
        0.50,
        ("BillAccReceivable", "TotalOperatingRevenueTTM"),
        "应收增长快于销售可能提示回款风险",
    ),
    Rule(
        "inventory_growth",
        "存货增速超收入",
        "存货同比增速－营业收入TTM同比增速",
        0.25,
        0.60,
        ("Inventories", "TotalOperatingRevenueTTM"),
        "存货积压可能提示需求或减值风险",
    ),
    Rule(
        "cash_profit",
        "利润现金背离",
        "经营现金流TTM／归母净利润TTM",
        0.8,
        0.3,
        ("NetOperateCashFlowTTM", "NPParentCompanyOwnersTTM"),
        "盈利未同步转成经营现金流",
        "low",
    ),
    Rule(
        "accruals",
        "应计利润偏高",
        "(归母净利润TTM－经营现金流TTM)／总资产",
        0.05,
        0.10,
        ("NPParentCompanyOwnersTTM", "NetOperateCashFlowTTM", "TotalLiability", "TotalShareholderEquity"),
        "应计利润占资产较高",
    ),
    Rule(
        "goodwill",
        "商誉占净资产",
        "商誉／归母净资产",
        0.20,
        0.40,
        ("GoodWill", "SEWithoutMI"),
        "商誉减值可能侵蚀净资产",
    ),
    Rule(
        "other_receivables",
        "其他应收款占资产",
        "其他应收款／总资产",
        0.05,
        0.12,
        ("OtherReceivableED", "TotalLiability", "TotalShareholderEquity"),
        "其他应收款集中可能提示资金占用",
    ),
    Rule(
        "prepayments",
        "预付款激增",
        "预付款同比增速",
        0.50,
        1.50,
        ("AdvancePayment",),
        "预付款激增可能提示交易或供应链风险",
    ),
    Rule(
        "cash_debt",
        "存贷双高",
        "短期借款／货币资金；且两者分别超过资产10%",
        0.8,
        1.2,
        ("ShortTermLoan", "CashEquivalents", "TotalLiability", "TotalShareholderEquity"),
        "大量现金同时伴随短债需解释融资安排",
    ),
    Rule(
        "gross_margin",
        "毛利率异常",
        "毛利TTM／营业收入TTM；按行业上尾比较",
        None,
        None,
        ("GrossProfitTTM", "TotalOperatingRevenueTTM"),
        "异常高毛利率需要同行核验",
    ),
    Rule("audit_opinion", "非标准审计意见", "审计意见类型", None, None, (), "报表无可靠审计意见历史", available=False),
    Rule(
        "auditor_change",
        "频繁更换审计机构",
        "近年审计机构变更次数",
        None,
        None,
        (),
        "报表无审计机构历史",
        available=False,
    ),
    Rule(
        "impairment", "大额减值", "当期资产减值损失／资产", None, None, (), "报表无可核验减值损失字段", available=False
    ),
    Rule(
        "capitalized_rd",
        "研发资本化占比",
        "资本化研发／总研发",
        None,
        None,
        (),
        "报表只有研发费用，无资本化研发字段",
        available=False,
    ),
)
RULE_BY_ID = {r.id: r for r in RULES}


def rule_catalog() -> list[dict]:
    return [asdict(r) for r in RULES]


def _number(row: dict | None, field: str) -> float | None:
    if row is None:
        return None
    try:
        n = float(row[field])
        return n if math.isfinite(n) else None
    except (KeyError, TypeError, ValueError):
        return None


def _ratio(a: float | None, b: float | None, *, positive=True) -> float | None:
    return a / b if a is not None and b is not None and (b > 0 if positive else b != 0) else None


def _growth(a: float | None, b: float | None) -> float | None:
    q = _ratio(a, b)
    return q - 1 if q is not None else None


def _level(value: float | None, rule: Rule) -> str:
    if value is None or rule.red is None:
        return "unavailable"
    sign = 1 if rule.direction == "high" else -1
    if sign * value >= sign * rule.red:
        return "red"
    if sign * value >= sign * rule.amber:
        return "amber"
    return "none"


def _date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise validation_error(f"invalid asof date: {value}") from exc


def _load_kind(root: Path, kind: str, code: str, asof: str) -> dict[str, dict]:
    path = root / kind / f"{code}.parquet"
    if not path.exists():
        return {}
    df = pd.read_parquet(path)
    if not {"EndDate", "InfoPublDate"}.issubset(df.columns):
        return {}
    df = df[(df.InfoPublDate.astype(str) < asof) & (df.InfoPublDate.notna()) & (df.EndDate.astype(str) < asof)]
    if df.empty:
        return {}
    df = df.sort_values("InfoPublDate").drop_duplicates("EndDate", keep="last")
    return {str(row["EndDate"]): row for row in df.to_dict("records")}


def _evaluate(code: str, asof: str, root: Path) -> dict:
    parts = {kind: _load_kind(root, kind, code, asof) for kind in KINDS}
    periods = sorted(set.intersection(*(set(p) for p in parts.values())))
    if not periods:
        return {
            "code": code,
            "asof": asof,
            "period": None,
            "announcement_date": None,
            "level": "unavailable",
            "rules": {r.id: {"value": None, "level": "unavailable", "evidence": {}} for r in RULES},
            "status": "no_complete_statement",
        }
    period = periods[-1]
    current = {k: parts[k][period] for k in KINDS}
    prior_period = f"{int(period[:4]) - 1}{period[4:]}"
    prior = {k: parts[k].get(prior_period) for k in KINDS}
    b, i, c = current["zcfz"], current["lrb"], current["xjll"]
    pb, pi = prior["zcfz"], prior["lrb"]
    n = _number
    assets = (
        sum((n(b, "TotalLiability"), n(b, "TotalShareholderEquity")))
        if all(n(b, f) is not None for f in ("TotalLiability", "TotalShareholderEquity"))
        else None
    )
    rev = _growth(n(i, "TotalOperatingRevenueTTM"), n(pi, "TotalOperatingRevenueTTM"))
    rec = _growth(n(b, "BillAccReceivable"), n(pb, "BillAccReceivable"))
    inv = _growth(n(b, "Inventories"), n(pb, "Inventories"))
    profit, cash = n(i, "NPParentCompanyOwnersTTM"), n(c, "NetOperateCashFlowTTM")
    debt, money = n(b, "ShortTermLoan"), n(b, "CashEquivalents")
    values = {
        "receivables_growth": rec - rev if rec is not None and rev is not None else None,
        "inventory_growth": inv - rev if inv is not None and rev is not None else None,
        "cash_profit": _ratio(cash, profit),
        "accruals": _ratio(profit - cash if profit is not None and cash is not None else None, assets),
        "goodwill": _ratio(n(b, "GoodWill"), n(b, "SEWithoutMI")),
        "other_receivables": _ratio(n(b, "OtherReceivableED"), assets),
        "prepayments": _growth(n(b, "AdvancePayment"), n(pb, "AdvancePayment")),
        "cash_debt": (_ratio(debt, money) if debt / assets >= 0.1 and money / assets >= 0.1 else 0.0)
        if all(v is not None for v in (debt, money, assets)) and assets > 0
        else None,
        "gross_margin": _ratio(n(i, "GrossProfitTTM"), n(i, "TotalOperatingRevenueTTM")),
    }
    fields = {k: {f: n(row, f) for r in RULES for f in r.fields if f in row} for k, row in current.items()}
    prev_fields = {
        k: {
            f: n(row, f)
            for f in ("BillAccReceivable", "Inventories", "AdvancePayment", "TotalOperatingRevenueTTM")
            if f in row
        }
        for k, row in prior.items()
        if row is not None
    }
    rules = {}
    for rule in RULES:
        value = values.get(rule.id)
        rules[rule.id] = {
            "value": value,
            "level": _level(value, rule) if rule.available else "unavailable",
            "evidence": {
                "period": period,
                "announcement_dates": {k: str(row["InfoPublDate"]) for k, row in current.items()},
                "fields": {k: {f: v for f, v in d.items() if f in rule.fields} for k, d in fields.items()},
                "prior_period": prior_period if prior["zcfz"] or prior["lrb"] else None,
                "prior_fields": {k: {f: v for f, v in d.items() if f in rule.fields} for k, d in prev_fields.items()},
            },
        }
    return {
        "code": code,
        "asof": asof,
        "period": period,
        "announcement_date": max(str(row["InfoPublDate"]) for row in current.values()),
        "level": "none",
        "rules": rules,
        "status": "ok",
    }


def _industry(settings):
    paths = sorted((settings.raw_dir / "westock" / "sw_industry").glob("*.parquet"))
    if not paths:
        return {}, None
    path = paths[-1]
    df = pd.read_parquet(path)
    df = df[df.level == 1].drop_duplicates("code")
    return {r.code: r.sector_name for r in df.itertuples()}, path.stem


def _effective_trade_date(settings, asof: str) -> str:
    path = settings.raw_dir / "baostock" / "trade_dates.parquet"
    if not path.exists():
        return asof
    cal = pd.read_parquet(path, columns=["calendar_date", "is_trading_day"])
    days = cal[(cal.calendar_date <= asof) & (cal.is_trading_day == 1)].calendar_date
    return str(days.max()) if not days.empty else asof


def flags_for(settings, codes, asof) -> list[dict]:
    """Pure screening: reads raw files, returns results, writes no output."""
    asof = _date(asof)
    effective_date = _effective_trade_date(settings, asof)
    industries, snapshot = _industry(settings)
    root = settings.raw_dir / "westock" / "financials"
    results = []
    for code in dict.fromkeys(codes):
        row = _evaluate(code, effective_date, root)
        row["asof"] = asof
        row["effective_trade_date"] = effective_date
        industry = industries.get(code)
        row.update(industry=industry, industry_snapshot=snapshot, industry_basis="current_snapshot_not_pit")
        if industry in FINANCIAL:
            row["status"], row["level"] = "financial_industry_skipped", "unavailable"
            for rule in row["rules"].values():
                rule["level"] = "unavailable"
        results.append(row)
    for rule in RULES:
        if not rule.available:
            continue
        groups: dict[str, list[tuple[dict, float]]] = {}
        for row in results:
            if row["status"] == "ok" and row["industry"]:
                value = row["rules"][rule.id]["value"]
                if value is not None:
                    groups.setdefault(row["industry"], []).append((row, value))
        for group in groups.values():
            vals = sorted(value for _, value in group)
            for row, value in group:
                rank = sum(x <= value for x in vals) / len(vals)
                row["rules"][rule.id]["industry_percentile"] = rank
                row["rules"][rule.id]["industry_sample"] = len(vals)
                if rule.id == "gross_margin" and len(vals) >= 10:
                    row["rules"][rule.id]["level"] = "red" if rank >= 0.98 else "amber" if rank >= 0.90 else "none"
    for row in results:
        if row["status"] == "ok":
            levels = [r["level"] for r in row["rules"].values()]
            row["level"] = "red" if "red" in levels else "amber" if "amber" in levels else "none"
    return results


def universe_codes(settings, universe: str, asof: str) -> list[str]:
    root = settings.raw_dir / "westock"
    if universe == "all":
        return sorted(p.stem for p in (root / "financials" / "lrb").glob("*.parquet"))
    if universe != "csi800":
        raise validation_error(f"unsupported universe: {universe}")
    members = settings.raw_dir / "baostock" / "members.parquet"
    if members.exists():
        df = pd.read_parquet(members)
        df = df[(df.snapshot_date <= asof) & df["index"].isin(["hs300", "zz500"])]
        if not df.empty:
            day = df.snapshot_date.max()
            return sorted(df[df.snapshot_date == day].code.unique().tolist())
    paths = sorted((root / "index_members").glob("*.parquet"))
    if paths:
        df = pd.read_parquet(paths[-1])
        return sorted(df[df["index"].isin(["sh.000300", "sh.000905"])].code.unique())
    raise validation_error("CSI800 membership unavailable")


def scan(settings, codes, asof, universe="custom") -> dict:
    asof = _date(asof)
    results = flags_for(settings, codes, asof)
    folder = settings.hot_root / "redflag" / asof
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "asof": asof,
        "universe": universe,
        "count": len(results),
        "results": results,
        "industry_snapshot": results[0]["industry_snapshot"] if results else None,
    }
    frame = pd.DataFrame(
        [
            {
                "code": r["code"],
                "asof": asof,
                "period": r["period"],
                "announcement_date": r["announcement_date"],
                "level": r["level"],
                "status": r["status"],
                "industry": r["industry"],
                **{f"{key}_value": v["value"] for key, v in r["rules"].items()},
                **{f"{key}_level": v["level"] for key, v in r["rules"].items()},
            }
            for r in results
        ]
    )
    frame.to_parquet(folder / "results.parquet", index=False)
    target = folder / "results.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=folder, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, allow_nan=False)
        tmp_name = tmp.name
    os.replace(tmp_name, target)
    levels = {level: sum(r["level"] == level for r in results) for level in ("red", "amber", "none", "unavailable")}
    rule_counts = {
        rule.id: {
            level: sum(r["rules"].get(rule.id, {}).get("level") == level for r in results) for level in ("red", "amber")
        }
        for rule in RULES
    }
    return {
        "asof": asof,
        "universe": universe,
        "count": len(results),
        "levels": levels,
        "rule_counts": rule_counts,
        "industry_snapshot": payload["industry_snapshot"],
        "paths": [str(folder / "results.parquet"), str(target)],
    }


def explain(row: dict) -> str:
    """Prompt-ready evidence only; interpretation belongs to the agent."""
    lines = [
        f"# 财报排雷证据包：{row['code']}",
        f"截至：{row['asof']}；报表期：{row['period']}；公告日：{row['announcement_date']}",
        f"总体级别：{row['level']}；申万一级行业：{row.get('industry') or '未知'}"
        f"（当前快照 {row.get('industry_snapshot')}，非历史PIT）",
        f"状态：{row['status']}。数值及级别由后端计算；请基于证据解读，不改写数值。",
    ]
    for spec in RULES:
        item = row.get("rules", {}).get(spec.id)
        if item:
            lines.append(f"\n## {spec.name} [{spec.id}]：{item['level']}")
            lines.append(
                f"公式：{spec.formula}；值：{item['value']}；"
                f"行业分位：{item.get('industry_percentile')}（样本 {item.get('industry_sample')}）"
            )
            lines.append("证据：" + json.dumps(item["evidence"], ensure_ascii=False))
    lines.append("\n限制：westock 每期仅存一个版本，重述可能造成历史前视；行业为当前快照。")
    return "\n".join(lines)
