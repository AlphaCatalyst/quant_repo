import { useState } from "react";
import { useApi, type BookReport, type BookSnapshot } from "../api";
import { Card, Empty, fmtNum, fmtPct, Loading } from "../components";

const ASSET_CLASS: Record<string, string> = { stock: "股票", etf: "ETF", convertible_bond: "可转债", cash: "现金", other: "其他" };
const BOND_FLAG: Record<string, string> = {
  high_conversion_premium: "转股溢价率超过 50%",
  small_remaining_size: "剩余规模低于 3 亿元",
  short_maturity: "剩余期限不足 1 年",
  low_rating: "评级低于 AA-",
};
const value = (v: number | null | undefined, digits = 2) => v == null ? "—" : fmtNum(v, digits);
const percentPoints = (v: number | null | undefined, digits = 1) => v == null ? "—" : `${fmtNum(v, digits)}%`;
const capYi = (v: number | null | undefined) => v == null ? "—" : `${fmtNum(v / 1e8, 1)} 亿`;
const labelClass = (name: string) => ASSET_CLASS[name] ?? name;

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
    <div className="two-col"><Weights title="持仓权重" weights={report.weights ?? {}} /><Weights title="资产类别" weights={Object.fromEntries(Object.entries(report.asset_class_weights ?? {}).map(([name, weight]) => [labelClass(name), weight]))} /></div>
    <div className="two-col"><Weights title="行业权重" weights={report.industry_weights ?? {}} /><Weights title="股票流通市值分组" weights={report.market_cap_buckets ?? {}} /></div>
    <p className="small muted">行业采用当前快照，并非持仓日的历史行业；优先使用申万一级，缺失时回退证监会行业。市值分组仅适用于股票。</p>
    {!!report.holdings?.length && <Card title="持仓明细" extra={<span className="small muted">报价为当日快照；持仓市值来自导入文件</span>}><div className="table-scroll"><table className="table"><thead><tr><th>代码</th><th>名称</th><th>类别</th><th>权重</th><th>持仓市值（元）</th><th>行业</th><th>市值分组</th><th>现价（元）</th><th>报价时间</th><th>市盈率</th><th>市净率</th><th>流通市值</th><th>总市值</th><th>52 周位置</th></tr></thead><tbody>
      {report.holdings.map((position) => <tr key={position.code}><td>{position.code}</td><td>{position.name || "—"}</td><td>{labelClass(position.asset_class)}</td><td>{fmtPct(position.weight, 1)}</td><td>{value(position.market_value ?? position.weight * report.total_value)}</td><td>{position.industry || "—"}</td><td>{position.market_cap_bucket || "—"}</td><td>{value(position.quote?.price)}</td><td>{position.quote?.time || "—"}</td><td>{value(position.quote?.pe_ratio)}</td><td>{value(position.quote?.pb_ratio)}</td><td>{capYi(position.quote?.circulating_market_cap)}</td><td>{capYi(position.quote?.total_market_cap)}</td><td>{position.quote?.position_52week == null ? "—" : fmtPct(position.quote.position_52week, 1)}</td></tr>)}
    </tbody></table></div></Card>}
    {!!report.convertible_bonds?.length && <Card title="可转债指标" extra={<span className="small muted">触发价距离为正值表示正股现价高于触发价</span>}><div className="table-scroll"><table className="table"><thead><tr><th>代码</th><th>名称</th><th>转股溢价率</th><th>纯债溢价率</th><th>双低值</th><th>到期收益率</th><th>评级</th><th>剩余规模（亿元）</th><th>剩余期限（年）</th><th>强赎触发价距离</th><th>回售触发价距离</th><th>客观标记</th></tr></thead><tbody>
      {report.convertible_bonds.map((bond) => <tr key={bond.code}><td>{bond.code}</td><td>{bond.name || report.holdings?.find((item) => item.code === bond.code)?.name || "—"}</td><td>{percentPoints(bond.conversion_premium_pct)}</td><td>{percentPoints(bond.pure_bond_premium_pct)}</td><td>{value(bond.double_low)}</td><td>{percentPoints(bond.ytm_pct)}</td><td>{bond.rating || "—"}</td><td>{value(bond.remaining_size_yi)}</td><td>{value(bond.remaining_term_years)}</td><td>{percentPoints(bond.redeem_distance_pct)}</td><td>{percentPoints(bond.buyback_distance_pct)}</td><td>{bond.flags?.length ? bond.flags.map((flag) => BOND_FLAG[flag] ?? flag).join("；") : "—"}</td></tr>)}
    </tbody></table></div></Card>}
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
  return <div className="page book-page"><div className="page-head"><div><h2>持仓体检</h2><p className="muted small">本机已导入的持仓快照及最近保存的体检报告。</p></div></div>
    <Card title={`持仓快照（${rows.length}）`}>{rows.length ? <div className="table-scroll"><table className="table"><thead><tr><th>账户</th><th>截至日期</th><th>持仓数</th><th>总市值</th><th>体检报告</th></tr></thead><tbody>
      {rows.map((row) => <tr key={row.snapshot_id} className={active?.snapshot_id === row.snapshot_id ? "best-row" : ""}><td><button className="table-sort" onClick={() => setSelected(row.snapshot_id)}>{row.account}</button></td><td>{row.as_of}</td><td>{row.positions_count}</td><td>{fmtNum(row.total_value, 2)}</td><td>{row.report ? "已保存" : "暂无"}</td></tr>)}
    </tbody></table></div> : <Empty text="暂无已导入持仓" />}</Card>
    {active && <><Card title={`${active.account} · ${active.as_of}`} extra={<span className="small muted">总市值 {fmtNum(active.total_value, 2)}</span>}>
      {active.report ? <p className="small muted">已保存体检报告 · {active.positions_count} 个持仓</p> : <Empty text="这份快照暂无已保存的体检报告" />}</Card>
      {active.report && <Report report={active.report} />}</>}
  </div>;
}
