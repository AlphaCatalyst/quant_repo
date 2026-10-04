import { useState } from "react";
import { useApi } from "../api";

type Alert = { alert_id: string; kind: string; severity: string; subject: string;
  title: string; detail: string; evidence: string[]; created_at: string; acknowledged: boolean };
type Response = { alerts: Alert[]; count: number };
const severity: Record<string, string> = { critical: "紧急", warning: "关注", info: "提示" };

export default function Alerts() {
  const [openOnly, setOpenOnly] = useState(true);
  const suffix = openOnly ? "?open=true" : "";
  const publicAlerts = useApi<Response>(`/api/alerts${suffix}`, 30000);
  const privateAlerts = useApi<Response>(`/api/alerts/private${suffix}`, 30000);
  const rows = [...(publicAlerts.data?.alerts || []), ...(privateAlerts.data?.alerts || [])]
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  return <section className="card">
    <div className="intro-head"><h2>监控告警</h2><label><input type="checkbox" checked={openOnly} onChange={e => setOpenOnly(e.target.checked)} /> 只看未确认</label></div>
    <p>每日数据更新后检查公告、财报、论点、预测和仓位。确认告警请使用人工身份运行 <code>monitor ack</code>。</p>
    {privateAlerts.error?.startsWith("403") && <p>持仓相关告警仅对已登录用户显示。</p>}
    {publicAlerts.error && <p>公开告警读取失败：{publicAlerts.error}</p>}
    {!publicAlerts.error && rows.length === 0 && <p>暂无告警。</p>}
    <div className="alert-list">{rows.map(a => <article className="card" key={a.alert_id}>
      <small>{severity[a.severity] || a.severity} · {a.kind} · {a.created_at.slice(0, 10)}{a.acknowledged ? " · 已确认" : ""}</small>
      <h3>{a.title}</h3><p>{a.detail}</p><p>对象：{a.subject}</p>
      <small>证据：{a.evidence.join(" · ")}</small>
      <p><code>{a.alert_id}</code></p>
    </article>)}</div>
  </section>;
}
