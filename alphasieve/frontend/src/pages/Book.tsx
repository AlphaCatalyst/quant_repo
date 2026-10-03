import { useState } from "react";
import { useApi, type BookReport, type BookSnapshot } from "../api";
import { Card, Empty, fmtNum, fmtPct, Loading } from "../components";

function Weights({ title, weights }: { title: string; weights: Record<string, number> }) {
  const rows = Object.entries(weights).sort((a, b) => b[1] - a[1]);
  return <Card title={title}>{rows.length ? <div className="bars">{rows.map(([name, value]) => <div className="bar-row" key={name}><span className="bar-label" title={name}>{name}</span><span className="bar-track"><span className="bar-fill" style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }} /></span><span className="bar-value">{fmtPct(value, 1)}</span></div>)}</div> : <Empty text="暂无权重" />}</Card>;
}
function Report({ report }: { report: BookReport }) {
  const stress = report.stress_returns;
  const industry = Object.entries(stress?.industry_down_20pct ?? {});
  return <>
    <div className="stats-row"><div className="stat"><div className="stat-value">{fmtPct(report.top_n_concentration, 1)}</div><div className="stat-label">前几大持仓集中度</div></div>
      <div className="stat"><div className="stat-value">{fmtNum(report.hhi, 3)}</div><div className="stat-label">持仓集中度 HHI</div></div>
      <div className="stat"><div className="stat-value">{fmtNum(report.portfolio_beta_60d, 2)}</div><div className="stat-label">60 日组合 Beta · 基准 {report.benchmark}</div></div></div>
    <div className="two-col"><Weights title="持仓权重" weights={report.weights ?? {}} /><Weights title="行业权重" weights={report.industry_weights ?? {}} /></div>
    <Card title="压力情景" extra={<span className="small muted">估算组合收益率</span>}><div className="table-scroll"><table className="table"><thead><tr><th>情景</th><th>估算影响</th></tr></thead><tbody>
      <tr><td>基准下跌 10%</td><td>{fmtPct(stress?.benchmark_down_10pct)}</td></tr>
      <tr><td>最大持仓下跌 30%</td><td>{fmtPct(stress?.largest_position_down_30pct)}</td></tr>
      {industry.map(([name, value]) => <tr key={name}><td>{name}行业下跌 20%</td><td>{fmtPct(value)}</td></tr>)}
    </tbody></table></div><p className="small muted">{report.stress_note ?? "线性冲击估算，仅供体检参考。"}</p></Card>
  </>;
}
export default function Book() {
  const { data, error } = useApi<{ snapshots: BookSnapshot[] }>("/api/book", 30000);
  const [selected, setSelected] = useState<string | null>(null);
  if (error?.startsWith("403 ")) return <div className="page"><div className="page-head"><h2>持仓体检</h2></div><Card title="访问受限">持仓属于个人数据，需认证后查看。</Card></div>;
  if (!data) return <Loading error={error} />;
  const rows = data.snapshots ?? [];
  const active = rows.find((row) => row.snapshot_id === selected) ?? rows[0];
  return <div className="page"><div className="page-head"><div><h2>持仓体检</h2><p className="muted small">本机已导入的持仓快照及最近保存的体检报告。</p></div></div>
    <Card title={`持仓快照（${rows.length}）`}>{rows.length ? <div className="table-scroll"><table className="table"><thead><tr><th>账户</th><th>截至日期</th><th>持仓数</th><th>总市值</th><th>体检报告</th></tr></thead><tbody>
      {rows.map((row) => <tr key={row.snapshot_id} className={active?.snapshot_id === row.snapshot_id ? "best-row" : ""}><td><button className="table-sort" onClick={() => setSelected(row.snapshot_id)}>{row.account}</button></td><td>{row.as_of}</td><td>{row.positions_count}</td><td>{fmtNum(row.total_value, 2)}</td><td>{row.report ? "已保存" : "暂无"}</td></tr>)}
    </tbody></table></div> : <Empty text="暂无已导入持仓" />}</Card>
    {active && <><Card title={`${active.account} · ${active.as_of}`} extra={<span className="small muted">总市值 {fmtNum(active.total_value, 2)}</span>}>
      {active.report ? <p className="small muted">已保存体检报告 · {active.positions_count} 个持仓</p> : <Empty text="这份快照暂无已保存的体检报告" />}</Card>
      {active.report && <Report report={active.report} />}</>}
  </div>;
}
