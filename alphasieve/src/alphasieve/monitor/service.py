"""Evaluate local evidence and persist deterministic alerts."""

import hashlib
import json
import os
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import yaml

from alphasieve.announcements import recent_for
from alphasieve.forecasts.service import list_forecasts
from alphasieve.redflag import flags_for
from alphasieve.thesis import load_thesis
from alphasieve.util import canonical_json


def _alert(rule, subject, trigger_date, kind, severity, title, detail, evidence, visibility):
    key = f"{rule}|{subject}|{trigger_date}"
    return {"alert_id": hashlib.sha256(key.encode()).hexdigest()[:24], "kind": kind,
            "severity": severity, "subject": subject, "title": title, "detail": detail,
            "evidence": evidence, "created_at": datetime.now(UTC).isoformat(),
            "visibility": visibility}


def _watchlist(settings, conn):
    path = settings.config_dir / "monitor" / "watchlist.yaml"
    configured = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    codes = configured.get("codes", []) if isinstance(configured, dict) else configured
    public = {str(c).split(".")[-1].zfill(6) for c in (codes or [])}
    theses = []
    thesis_dir = Path(os.environ.get("ALPHASIEVE_THESES_DIR", Path(__file__).resolve().parents[3] / "theses"))
    for path in sorted(thesis_dir.glob("*.yaml")):
        theses.append(load_thesis(path))
        public.update(str(a.code).split(".")[-1].zfill(6) for a in theses[-1].assets)
    row = conn.execute("SELECT snapshot_id, positions_json FROM holdings_snapshots "
                       "ORDER BY as_of DESC, created_at DESC LIMIT 1").fetchone()
    held = set()
    positions = []
    if row:
        positions = json.loads(row["positions_json"])
        held = {str(p["code"]).split(".")[-1].zfill(6) for p in positions if p["code"] != "CASH"}
    return public, held, theses, row, positions


def _market_value(variable, asof):
    """Explicit variable grammar: sina_futures_close:LH2707 or stock_close:sh.600519."""
    if variable.startswith("sina_futures_close:"):
        from alphasieve.data.providers.sina import daily_bars
        symbol = variable.split(":", 1)[1]
        df = daily_bars(symbol)
        source = f"sina:{symbol}"
    elif variable.startswith("stock_close:"):
        from alphasieve.data.providers.baostock import BaoStockSession
        code = variable.split(":", 1)[1]
        with BaoStockSession() as session:
            df = session.daily(code, asof, asof)
        source = f"baostock:{code}"
    else:
        return None
    if df.empty:
        return None
    dates = df["date"].astype(str)
    rows = df[dates <= asof]
    if rows.empty:
        return None
    latest = rows.iloc[-1]
    return float(latest["close"]), str(latest["date"])[:10], source


def evaluate(settings, conn, asof):
    today = date.fromisoformat(asof)
    public, held, theses, snapshot, positions = _watchlist(settings, conn)
    alerts, warnings = [], []
    for thesis in theses:
        tid = thesis.thesis_id
        for f in thesis.falsifiers:
            evidence = [f"thesis:{tid}:{f.id}"]
            match = re.search(r"(>=|<=|>|<)\s*(\d+(?:\.\d+)?)", f.condition)
            value = None
            if match and (f.variable.startswith("sina_futures_close:") or f.variable.startswith("stock_close:")):
                try:
                    value = _market_value(f.variable, asof)
                except Exception as exc:
                    warnings.append(f"thesis {tid}/{f.id}: {str(exc)[:160]}")
            if value is not None:
                observed, observed_date, source = value
                op, threshold = match.group(1), float(match.group(2))
                hit = {">": observed > threshold, ">=": observed >= threshold,
                       "<": observed < threshold, "<=": observed <= threshold}[op]
                if hit:
                    alerts.append(_alert(f"thesis-falsified:{tid}:{f.id}", tid, observed_date,
                        "thesis_falsifier", "critical", f"论点证伪条件触发：{thesis.title}",
                        f"{f.condition}；观测值 {observed:g}。建议：{f.action}", evidence + [source], "public"))
            else:
                alerts.append(_alert(f"thesis-manual:{tid}:{f.id}", tid, asof, "manual_check", "info",
                    f"论点待人工核查：{thesis.title}", f"{f.condition}；建议：{f.action}", evidence, "public"))
    for item in list_forecasts(conn, status="open")["forecasts"]:
        settle = date.fromisoformat(item["spec"]["resolver_params"]["settle_date"])
        delta = (settle - today).days
        if delta <= 7:
            overdue = delta < 0
            alerts.append(_alert("forecast-overdue" if overdue else "forecast-due", item["forecast_id"],
                settle.isoformat(), "forecast", "warning" if overdue else "info",
                "预测逾期未结算" if overdue else "预测即将结算",
                f"{item['spec']['statement']}；结算日 {settle.isoformat()}",
                [f"forecast:{item['forecast_id']}"], "public"))
    if snapshot:
        total = sum(float(p.get("market_value") or 0) for p in positions)
        for thesis in theses:
            codes = {str(a.code).split(".")[-1].zfill(6) for a in thesis.assets}
            weight = sum(float(p.get("market_value") or 0) for p in positions
                         if str(p["code"]).split(".")[-1].zfill(6) in codes) / total if total > 0 else 0
            if weight > thesis.position_limit + 1e-9:
                alerts.append(_alert(f"position-cap:{thesis.thesis_id}", thesis.thesis_id,
                    snapshot["snapshot_id"], "position_cap", "critical",
                    f"论点仓位超过上限：{thesis.title}",
                    f"当前 {weight:.1%}，上限 {thesis.position_limit:.1%}",
                    [f"thesis:{thesis.thesis_id}", f"holdings:{snapshot['snapshot_id']}"], "private"))
    # Run public and holdings-only scans separately so watchlist alerts never reveal holdings provenance.
    for codes, visibility in ((public, "public"), (held - public, "private")):
        if not codes:
            continue
        try:
            for row in recent_for(settings, sorted(codes), (today - timedelta(days=14)).isoformat()):
                published = str(row["published_at"])[:10]
                aid = str(row["announcement_id"])
                alerts.append(_alert(f"announcement:{aid}", row["code"], published,
                    "announcement", "warning" if row["importance"] == "high" else "info",
                    row["title"], f"{row['event_type']}；{published}",
                    [f"cninfo:{aid}"] + ([row["url"]] if row.get("url") else []), visibility))
        except Exception as exc:
            warnings.append(f"announcements {visibility}: {str(exc)[:160]}")
        try:
            for row in flags_for(settings, sorted(codes), asof):
                if row["level"] not in ("red", "amber"):
                    continue
                triggered = [key for key, rule in row["rules"].items()
                             if rule["level"] in ("red", "amber")]
                alerts.append(_alert(f"redflag:{row['level']}:{','.join(sorted(triggered))}",
                    row["code"], str(row.get("period") or asof)[:10], "redflag",
                    "critical" if row["level"] == "red" else "warning",
                    f"财报排雷：{row['code']} {row['level']}",
                    f"触发规则：{', '.join(triggered)}；报表期 {row.get('period') or '未知'}",
                    [f"redflag:{row['code']}:{asof}"] + [f"redflag-rule:{r}" for r in triggered], visibility))
        except Exception as exc:
            warnings.append(f"redflag {visibility}: {str(exc)[:160]}")
    return alerts, warnings


def run(settings, conn, asof=None):
    asof = asof or date.today().isoformat()
    date.fromisoformat(asof)
    alerts, warnings = evaluate(settings, conn, asof)
    inserted = 0
    for item in alerts:
        cursor = conn.execute("INSERT OR IGNORE INTO alerts VALUES (?,?,?,?,?,?,?,?,?)",
            (item["alert_id"], item["kind"], item["severity"], item["subject"], item["title"],
             item["detail"], canonical_json(item["evidence"]), item["created_at"], item["visibility"]))
        inserted += cursor.rowcount
    return {"evaluated": len(alerts), "inserted": inserted, "warnings": warnings}


def list_alerts(conn, *, visibility=None, open_only=False, kind=None):
    where, args = [], []
    if visibility:
        where.append("a.visibility=?")
        args.append(visibility)
    if kind:
        where.append("a.kind=?")
        args.append(kind)
    if open_only:
        where.append("NOT EXISTS (SELECT 1 FROM alert_acks x WHERE x.alert_id=a.alert_id)")
    sql = ("SELECT a.*, (SELECT COUNT(*) FROM alert_acks x WHERE x.alert_id=a.alert_id) AS ack_count "
           "FROM alerts a" + (" WHERE " + " AND ".join(where) if where else "") +
           " ORDER BY a.created_at DESC, a.alert_id DESC")
    return [{**dict(r), "evidence": json.loads(r["evidence_json"]), "acknowledged": bool(r["ack_count"])}
            for r in conn.execute(sql, args)]


def acknowledge(conn, alert_id, actor, note):
    if not conn.execute("SELECT 1 FROM alerts WHERE alert_id=?", (alert_id,)).fetchone():
        raise ValueError("alert not found")
    if not note.strip():
        raise ValueError("acknowledgement note required")
    ack_id = hashlib.sha256(f"{alert_id}|{actor}|{note}|{datetime.now(UTC).isoformat()}".encode()).hexdigest()[:24]
    conn.execute("INSERT INTO alert_acks VALUES (?,?,?,?,?)",
                 (ack_id, alert_id, actor, note.strip(), datetime.now(UTC).isoformat()))
    return ack_id
