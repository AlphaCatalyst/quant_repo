import { useState } from "react";
import { useApi, type Json } from "../api";
import { Card, DataTable, Empty, fmtNum, link, Loading } from "../components";

export const EVENT_LABEL: Record<string, string> = {
  convertible_redemption: "可转债强赎", convertible_revision: "转股价下修", regulatory_penalty: "行政处罚/立案",
  regulatory_inquiry: "问询/关注函", audit_opinion: "审计意见", auditor_change: "更换会计师", earnings_guidance: "业绩预告",
  repurchase: "回购", shareholding_change: "增减持", litigation: "诉讼仲裁", annual_report: "年报", semiannual_report: "半年报",
  quarterly_report: "季报", convertible_other: "可转债其他", material_event: "重大事项", equity_change: "股权变动", other: "其他",
};
const IMPORTANCE: Record<string, [string, string]> = { high: ["高", "red"], medium: ["中", "amber"], low: ["低", "grey"] };

function Importance({ value }: { value: string }) {
  const [label, tone] = IMPORTANCE[value] ?? [value, "grey"];
  return <span className={`badge ${tone}`}>{label}</span>;
}

export default function Announcements() {
  const [days, setDays] = useState(30);
  const [scope, setScope] = useState("");
  const { data, error } = useApi<Json>(`/api/announcements?days=${days}${scope ? `&importance=${scope}` : ""}`, 300000);
  if (!data) return <Loading error={error} />;
  const types = Object.entries(data.by_type ?? {}) as [string, number][];
  const maxType = Math.max(1, ...types.map(([, n]) => n));
  return (
    <div className="page">
      <div className="page-head"><div>
        <h2>公司公告</h2>
        <p className="muted question">巨潮资讯的全 A 公告标题，按标题自动归类并标重要度（高：强赎、处罚、非标审计、诉讼；中：业绩预告、回购、增减持、问询等）。
          持仓相关的公告会进入告警。<a href={link("/docs/announcements.md")}>分类口径 →</a></p>
      </div>
        <div className="filters">
          <select aria-label="时间范围" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[7, 14, 30, 60, 90].map((d) => <option key={d} value={d}>近 {d} 天</option>)}
          </select>
          <select aria-label="范围" value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="">中高重要度</option><option value="high">仅高</option><option value="held">仅持仓</option><option value="all">全部（含低）</option>
          </select>
        </div>
      </div>

      <div className="metric-strip">
        <div className="stat"><div className="stat-value">{fmtNum(data.total)}</div><div className="stat-label">近 {data.days} 天公告</div></div>
        {(["high", "medium", "low"] as const).map((k) => <div key={k} className={`stat ${k === "high" ? "amber" : ""}`}><div className="stat-value">{fmtNum(data.by_importance?.[k] ?? 0)}</div><div className="stat-label">{IMPORTANCE[k][0]}重要度</div></div>)}
        <div className="stat"><div className="stat-value">{fmtNum(data.held?.length ?? 0)}</div><div className="stat-label">涉及持仓</div></div>
      </div>

      <div className="two-col">
        <Card title="我的持仓公告">
          {data.held?.length ? <ul className="announcements">{(data.held as Json[]).slice(0, 20).map((a) => <li key={a.announcement_id}>
            <span className="muted small">{a.published_at?.slice(0, 10)}</span> <Importance value={a.importance} /> <b>{a.name}</b>{" "}
            <a href={a.pdf_url} target="_blank" rel="noreferrer">{a.title}</a></li>)}</ul> : <Empty text="这段时间持仓没有公告" />}
        </Card>
        <Card title="中高重要度事件分布">
          {types.length === 0 ? <Empty /> : <div className="bars">{types.map(([k, n]) => <div key={k} className="bar-row">
            <span className="bar-label">{EVENT_LABEL[k] ?? k}</span>
            <span className="bar-track"><span className="bar-fill" style={{ width: `${(n / maxType) * 100}%` }} /></span>
            <span className="bar-value">{n}</span></div>)}</div>}
          <h4>本地公告覆盖（按年的交易日数）</h4>
          <p className="small">{Object.entries(data.coverage ?? {}).map(([y, n]) => `${y}: ${n}`).join(" · ")}</p>
          <p className="small muted">2019–2022 用于 dev 期事件研究（P4）；2026 年起为每日增量。</p>
        </Card>
      </div>

      <Card title={`公告列表（${fmtNum(data.shown)}${data.truncated ? "，只显示最新 400 条" : ""}）`}>
        <DataTable rows={data.rows as Json[]} filename={`announcements-${days}d.csv`} searchPlaceholder="搜索公司或标题"
          filters={[
            { label: "重要度", value: (a: Json) => a.importance, options: ["high", "medium", "low"].map((v) => ({ value: v, label: IMPORTANCE[v][0] })) },
            { label: "事件", value: (a: Json) => a.event_type, options: Object.keys(EVENT_LABEL).map((v) => ({ value: v, label: EVENT_LABEL[v] })) },
          ]}
          columns={[
            { key: "date", label: "日期", value: (a: Json) => a.published_at?.slice(0, 10) },
            { key: "name", label: "公司", value: (a: Json) => `${a.name} ${a.code}`, render: (a: Json) => <>{a.name}{a.held && <span className="tag">持仓</span>}<div className="muted small">{a.code}</div></> },
            { key: "type", label: "事件", value: (a: Json) => EVENT_LABEL[a.event_type] ?? a.event_type },
            { key: "imp", label: "重要度", value: (a: Json) => ({ high: 0, medium: 1, low: 2 } as Record<string, number>)[a.importance], render: (a: Json) => <Importance value={a.importance} /> },
            { key: "title", label: "标题", value: (a: Json) => a.title, render: (a: Json) => <a href={a.pdf_url} target="_blank" rel="noreferrer" className="small">{a.title}</a> },
          ]} />
      </Card>
    </div>
  );
}
