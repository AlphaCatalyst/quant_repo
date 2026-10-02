import { useMemo, useState } from "react";
import { useApi } from "../api";
import { Card, Chart, Empty, fmtNum, fmtPct, Loading, Progress } from "../components";

type Point = { date: string; nav: number | null; benchmark_nav: number | null; valid?: boolean };
type Gap = { date: string; reason?: string; status?: string };
type Cohort = {
  cohort_id: string; object_kind: string; mode: string; scope?: string; trial_id?: string;
  status: string; approved: boolean; start_date?: string; last_date?: string;
  observed_days: number; required_days: number; verdict?: string | null;
  nav?: Point[]; missing_days?: Gap[];
};
type ForwardResponse = { cohorts: Cohort[] };

const verdictText: Record<string, string> = {
  supported: "前瞻支持", fresh_supported: "前瞻支持", failed: "前瞻未通过",
  fresh_failed: "前瞻未通过", inconclusive: "证据不足", shadow_complete: "影子观察完成",
  pending: "待观察", observing: "观察中",
};
const statusText: Record<string, string> = { locked: "已锁定", observing: "观察中", paused: "已暂停", closed: "已关闭" };
const gapText: Record<string, string> = { data_gap: "关键数据缺失", missed_signal: "错过决策截止时间", not_recorded: "未记录" };

function CohortDetail({ cohort }: { cohort: Cohort }) {
  const points = cohort.nav ?? [];
  const gaps = cohort.missing_days ?? [];
  const option = useMemo(() => ({
    tooltip: { trigger: "axis" as const }, legend: { data: ["模拟净值", "基准净值"], textStyle: { color: "#94a3b8" } },
    grid: { left: 55, right: 20, top: 42, bottom: 48 },
    xAxis: { type: "category" as const, data: points.map((p) => p.date), axisLabel: { rotate: points.length > 12 ? 35 : 0, color: "#94a3b8" } },
    yAxis: { type: "value" as const, scale: true, axisLabel: { color: "#94a3b8" }, splitLine: { lineStyle: { color: "#64748b", opacity: 0.25 } } },
    series: [
      { name: "模拟净值", type: "line" as const, showSymbol: false, connectNulls: false, data: points.map((p) => p.valid === false ? null : p.nav) },
      { name: "基准净值", type: "line" as const, showSymbol: false, connectNulls: false, data: points.map((p) => p.valid === false ? null : p.benchmark_nav) },
    ],
  }), [points]);
  const latest = [...points].reverse().find((p) => p.valid !== false && p.nav != null);
  return <div className="page">
    <Card title={<>{cohort.cohort_id} · {cohort.object_kind === "factor" ? "因子" : "策略"}</>} extra={<span className="badge blue">{statusText[cohort.status] ?? cohort.status}</span>}>
      <div className="forward-facts">
        <div><span>观察类型</span><strong>{cohort.mode === "diagnostic_shadow" ? "诊断影子 · 不可晋升" : "前瞻验证"}</strong></div>
        <div><span>人工批准</span><strong>{cohort.approved ? "已批准启用" : "待人工批准 · 未运行"}</strong></div>
        <div><span>来源</span><strong>{cohort.scope ?? "—"}{cohort.trial_id ? ` · ${cohort.trial_id}` : ""}</strong></div>
        <div><span>观察区间</span><strong>{cohort.start_date ?? "—"} 至 {cohort.last_date ?? "—"}</strong></div>
      </div>
      <Progress label="有效观察日" used={cohort.observed_days ?? 0} budget={cohort.required_days ?? 0} unit=" 日" />
      <p className="small muted">工程连续运行与统计裁决分别验收；缺失日不计入有效观察日。影子组只供诊断。</p>
    </Card>
    <div className="forward-summary">
      <div className="stat"><div className="stat-value">{fmtNum(cohort.observed_days ?? 0)} / {fmtNum(cohort.required_days ?? 0)}</div><div className="stat-label">有效日 / 所需日</div></div>
      <div className="stat"><div className="stat-value">{latest ? fmtNum(latest.nav, 4) : "—"}</div><div className="stat-label">最近有效模拟净值</div></div>
      <div className="stat"><div className="stat-value">{gaps.length}</div><div className="stat-label">缺失日</div></div>
      <div className="stat"><div className="stat-value forward-verdict">{cohort.mode === "diagnostic_shadow" ? "仅诊断" : verdictText[cohort.verdict ?? ""] ?? "尚无裁决"}</div><div className="stat-label">固定端点裁决</div></div>
    </div>
    <Card title="模拟净值与基准" extra={<span className="small muted">初始净值 1</span>}>
      {points.length ? <Chart option={option} height={280} /> : <Empty text="尚无有效净值记录" />}
      {latest && <p className="small muted">最近有效日 {latest.date} · 模拟净值 {fmtNum(latest.nav, 4)} · 基准 {fmtNum(latest.benchmark_nav, 4)} · 差值 {fmtPct((latest.nav ?? 0) - (latest.benchmark_nav ?? 0), 2)}</p>}
    </Card>
    <Card title={`缺失日（${gaps.length}）`}>
      {gaps.length ? <div className="table-scroll"><table className="table"><thead><tr><th>日期</th><th>状态</th><th>原因</th></tr></thead><tbody>
        {gaps.map((gap) => <tr key={gap.date}><td>{gap.date}</td><td>{gap.status === "not_recorded" ? "未记录" : "数据缺口"}</td><td>{gapText[gap.reason ?? ""] ?? gap.reason ?? "—"}</td></tr>)}
      </tbody></table></div> : <Empty text="没有缺失日" />}
    </Card>
  </div>;
}

export default function Forward() {
  const { data, error } = useApi<ForwardResponse>("/api/forward", 30000);
  const [selected, setSelected] = useState<string | null>(null);
  if (!data) return <Loading error={error} />;
  const cohorts = data.cohorts ?? [];
  const active = cohorts.find((c) => c.cohort_id === selected) ?? cohorts[0];
  return <div className="page">
    <div className="page-head"><div><h2>前瞻</h2><p className="muted small">锁定配置后的每日观察与模拟账簿；启用和正式 paper 批准由人工完成。</p></div></div>
    <Card title={`观察组（${cohorts.length}）`}>
      {cohorts.length ? <div className="forward-cohorts" role="tablist" aria-label="前瞻观察组">
        {cohorts.map((c) => <button key={c.cohort_id} className={`forward-cohort ${active?.cohort_id === c.cohort_id ? "selected" : ""}`}
          role="tab" aria-selected={active?.cohort_id === c.cohort_id} onClick={() => setSelected(c.cohort_id)}>
          <strong>{c.cohort_id}</strong><span>{c.mode === "diagnostic_shadow" ? "诊断影子" : "正式验证"} · {c.object_kind === "factor" ? "因子" : "策略"}</span>
          <small>{c.approved ? `${c.observed_days ?? 0} / ${c.required_days ?? 0} 日` : "待人工批准"}</small>
        </button>)}
      </div> : <Empty text="暂无已登记的前瞻观察组" />}
    </Card>
    {active && <CohortDetail cohort={active} />}
  </div>;
}
