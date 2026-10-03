import { useApi, type ForecastItem, type ThesisDetail, type ThesisSummary } from "../api";
import { Card, Empty, fmtNum, fmtPct, Loading } from "../components";

const statusName: Record<string, string> = { researching: "研究中", holding: "持有中", falsified: "已证伪", realized: "已实现", abandoned: "已放弃" };
const gradeName: Record<string, string> = { A: "A · 原始数据", B: "B · 交叉核实", C: "C · 单一来源", D: "D · 待核实" };
function ForecastRows({ rows }: { rows: ForecastItem[] }) {
  return rows.length ? <div className="table-scroll"><table className="table"><thead><tr><th>命题</th><th>概率</th><th>结算日</th><th>状态</th><th>结果</th></tr></thead><tbody>
    {rows.map((row) => <tr key={row.forecast_id}><td>{row.spec.statement}</td><td>{fmtPct(row.spec.p, 0)}</td>
      <td>{row.spec.resolver_params.settle_date}</td><td>{row.status === "open" ? "待结算" : row.status === "settled" ? "已结算" : "已作废"}</td>
      <td>{row.resolution?.outcome == null ? "—" : row.resolution.outcome ? "发生" : "未发生"}</td></tr>)}
  </tbody></table></div> : <Empty text="暂无已登记预测" />;
}
export function Thesis({ id }: { id: string }) {
  const { data, error } = useApi<ThesisDetail>(`/api/theses/${encodeURIComponent(id)}`, 30000);
  if (!data) return <div className="page"><div className="subnav"><a href="#/theses">← 论点列表</a></div><Loading error={error} /></div>;
  const thesis = data.thesis;
  const scenarios = data.scenarios ?? {};
  const sensitivity = data.sensitivity ?? [];
  const maxImpact = Math.max(0, ...sensitivity.map((row) => row.absolute_impact));
  return <div className="page">
    <div className="subnav"><a href="#/theses">← 论点列表</a></div>
    <div className="page-head"><div><h2>{thesis.title}</h2><p className="muted small">{thesis.assets.map((asset) => `${asset.name} ${asset.code}`).join(" · ")} · 截至 {thesis.as_of} · {statusName[thesis.status] ?? thesis.status}</p></div></div>
    <Card title="核心论证"><ul className="list">{thesis.argument.map((line, index) => <li key={index}>{line}</li>)}</ul>
      <p className="small muted">核心变量：{thesis.core_variables.join("、") || "—"}</p></Card>
    <Card title="估值情景" extra={<span className="small muted">单位：{thesis.valuation.output_unit}</span>}>
      <p className="small muted">公式：<span className="mono">{thesis.valuation.formula}</span> · 市价 {fmtNum(thesis.valuation.market_price)}</p>
      <div className="table-scroll"><table className="table"><thead><tr><th>情景</th><th>估值</th><th>相对基准</th></tr></thead><tbody>
        <tr><td>基准</td><td>{fmtNum(data.base_valuation)}</td><td>—</td></tr>
        {Object.entries(scenarios).map(([name, value]) => <tr key={name}><td>{name}</td><td>{fmtNum(value)}</td><td>{fmtNum(value - data.base_valuation)}</td></tr>)}
      </tbody></table></div>
    </Card>
    <Card title="参数敏感度" extra={<span className="small muted">单变量低值 / 高值相对基准估值的变化</span>}>
      {sensitivity.length ? <div className="sensitivity-list">{sensitivity.map((row) => <div className="sensitivity-row" key={row.parameter}>
        <span>{row.parameter}</span><div className="sensitivity-track" title={`低值 ${fmtNum(row.low)}，高值 ${fmtNum(row.high)}`}>
          <span className="sensitivity-negative" style={{ width: `${maxImpact ? Math.min(50, Math.abs(Math.min(row.low_impact, row.high_impact, 0)) / maxImpact * 50) : 0}%` }} />
          <span className="sensitivity-positive" style={{ width: `${maxImpact ? Math.min(50, Math.max(row.low_impact, row.high_impact, 0) / maxImpact * 50) : 0}%` }} />
        </div><span>{fmtNum(row.low_impact)} / {fmtNum(row.high_impact)}</span>
      </div>)}</div> : <Empty text="暂无敏感度数据" />}
    </Card>
    <Card title={`证据（${data.evidence?.length ?? 0}）`}><div className="table-scroll"><table className="table"><thead><tr><th>等级</th><th>主张</th><th>数值</th><th>来源</th><th>查阅日期</th></tr></thead><tbody>
      {(data.evidence ?? []).map((row) => <tr key={row.id} className={`evidence-grade-${row.grade}`}><td><span className="grade-label">{gradeName[row.grade] ?? row.grade}</span></td><td>{row.claim}</td><td>{row.value ?? "未找到"}</td><td>{row.source}</td><td>{row.accessed}</td></tr>)}
    </tbody></table></div></Card>
    <Card title={`证伪条件（${data.falsifiers?.length ?? 0}）`}>{data.falsifiers?.length ? <div className="table-scroll"><table className="table"><thead><tr><th>变量</th><th>触发条件</th><th>预定动作</th></tr></thead><tbody>
      {data.falsifiers.map((row) => <tr key={row.id}><td>{row.variable}</td><td>{row.condition}</td><td>{row.action}</td></tr>)}
    </tbody></table></div> : <Empty text="暂无证伪条件" />}</Card>
    <Card title="拟登记预测">{data.proposed_forecasts?.length ? <ul className="list">{data.proposed_forecasts.map((row, index) => <li key={index}>{String(row.statement ?? JSON.stringify(row))}</li>)}</ul> : <Empty text="暂无拟登记预测" />}</Card>
    <Card title="已登记预测"><ForecastRows rows={data.registered_forecasts ?? []} /></Card>
    <Card title="关联决策日志">{data.journal_entries?.length ? <div className="table-scroll"><table className="table"><thead><tr><th>时间</th><th>动作</th><th>记录</th></tr></thead><tbody>{data.journal_entries.map((row) => <tr key={row.entry_id}><td>{row.created_at ?? "—"}</td><td>{row.action ?? "—"}</td><td>{JSON.stringify(row.payload ?? {})}</td></tr>)}</tbody></table></div> : <Empty text="暂无关联决策记录" />}</Card>
  </div>;
}
export default function Theses() {
  const { data, error } = useApi<{ theses: ThesisSummary[] }>("/api/theses", 30000);
  if (!data) return <Loading error={error} />;
  const rows = data.theses ?? [];
  return <div className="page"><div className="page-head"><div><h2>论点</h2><p className="muted small">估值、证据和证伪条件均来自已记录的论点。</p></div></div>
    <Card title={`论点列表（${rows.length}）`}>{rows.length ? <div className="table-scroll"><table className="table"><thead><tr><th>论点</th><th>状态</th><th>标的</th><th>基准估值</th><th>市价</th><th>市场隐含核心值</th><th>证据 A/B/C/D</th><th>证伪条件</th><th>关联预测</th></tr></thead><tbody>
      {rows.map((row) => <tr key={row.id}><td><a href={`#/thesis/${encodeURIComponent(row.id)}`}>{row.title}</a></td><td>{statusName[row.status] ?? row.status}</td><td>{row.assets.map((asset) => asset.name).join("、")}</td><td>{fmtNum(row.base_valuation)}</td><td>{fmtNum(row.market_price)}</td><td>{fmtNum(row.implied_core_value)}</td><td>{["A", "B", "C", "D"].map((grade) => row.evidence_counts?.[grade] ?? 0).join(" / ")}</td><td>{row.falsifiers_count}</td><td>{row.linked_forecasts_count}</td></tr>)}
    </tbody></table></div> : <Empty text="暂无论点" />}</Card>
  </div>;
}
