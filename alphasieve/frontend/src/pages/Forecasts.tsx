import { useMemo } from "react";
import { useApi, type ForecastsResponse } from "../api";
import { Card, Chart, Empty, fmtNum, fmtPct, Loading } from "../components";

export default function Forecasts() {
  const { data, error } = useApi<ForecastsResponse>("/api/forecasts", 30000);
  const buckets = data?.score?.calibration ?? [];
  const option = useMemo(() => ({
    tooltip: { trigger: "axis" as const }, legend: { data: ["实际发生频率", "理想校准线"] },
    grid: { left: 48, right: 22, top: 44, bottom: 50 },
    xAxis: { type: "category" as const, data: buckets.map((bucket) => bucket.range), axisLabel: { rotate: 35 } },
    yAxis: { type: "value" as const, min: 0, max: 1, axisLabel: { formatter: (v: number) => `${Math.round(v * 100)}%` } },
    series: [
      { name: "实际发生频率", type: "bar" as const, data: buckets.map((bucket) => bucket.observed_frequency) },
      { name: "理想校准线", type: "line" as const, data: buckets.map((bucket) => { const [low, high] = bucket.range.split("-").map(Number); return (low + high) / 2; }) },
    ],
  }), [buckets]);
  if (!data) return <Loading error={error} />;
  const rows = data.forecasts ?? [];
  return <div className="page"><div className="page-head"><div><h2>预测</h2><p className="muted small">预先登记的可结算命题及已结算预测的校准表现。</p></div></div>
    <div className="stats-row"><div className="stat"><div className="stat-value">{rows.length}</div><div className="stat-label">登记预测</div></div>
      <div className="stat"><div className="stat-value">{data.score?.count ?? 0}</div><div className="stat-label">已结算预测</div></div>
      <div className="stat"><div className="stat-value">{fmtNum(data.score?.brier_score, 3)}</div><div className="stat-label">总体 Brier 分数 · 越低越好</div></div></div>
    <Card title="预测列表">{rows.length ? <div className="table-scroll"><table className="table"><thead><tr><th>命题</th><th>关联论点</th><th>概率</th><th>结算日</th><th>状态</th><th>观测值</th><th>是否发生</th><th>数据来源</th></tr></thead><tbody>
      {rows.map((row) => <tr key={row.forecast_id}><td>{row.spec.statement}</td><td>{row.thesis_id ? <a href={`#/thesis/${encodeURIComponent(row.thesis_id)}`}>{row.thesis_id}</a> : "—"}</td><td>{fmtPct(row.spec.p, 0)}</td><td>{row.spec.resolver_params.settle_date}</td><td>{row.status === "open" ? "待结算" : row.status === "settled" ? "已结算" : "已作废"}</td><td>{fmtNum(row.resolution?.observed_value)}</td><td>{row.resolution?.outcome == null ? "—" : row.resolution.outcome ? "发生" : "未发生"}</td><td>{row.resolution?.source ?? row.spec.source_of_truth}</td></tr>)}
    </tbody></table></div> : <Empty text="暂无已登记预测" />}</Card>
    <Card title="概率校准" extra={<span className="small muted">每档十个百分点；仅统计已结算预测</span>}>
      {data.score?.count ? <><Chart option={option} height={300} /><div className="table-scroll"><table className="table"><thead><tr><th>预测概率区间</th><th>样本数</th><th>实际发生频率</th></tr></thead><tbody>
        {buckets.map((bucket) => <tr key={bucket.range}><td>{bucket.range}</td><td>{bucket.count}</td><td>{fmtPct(bucket.observed_frequency, 0)}</td></tr>)}
      </tbody></table></div></> : <Empty text="暂无已结算预测，无法计算校准度" />}
    </Card>
  </div>;
}
