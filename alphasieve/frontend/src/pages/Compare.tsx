import { useApi, type Json } from "../api";
import { Badge, Card, Chart, Empty, fmtNum, fmtPct, link, Loading } from "../components";
import { CHECK_LABEL, checkDirection, fmtCheck, thresholdText } from "./Mandates";

const ID = /^S-[0-9a-f]{12}(?:-H)?$/;
const METRICS: [string, string, "pct" | "num"][] = [
  ["annual_excess", "年化净超额", "pct"], ["annual_return", "年化收益", "pct"],
  ["information_ratio", "信息比率", "num"], ["sharpe", "夏普", "num"],
  ["tracking_error", "跟踪误差", "pct"], ["max_drawdown_excess", "超额最大回撤", "pct"],
  ["max_drawdown", "最大回撤", "pct"], ["annual_turnover", "年换手", "num"],
  ["annual_cost", "年成本", "pct"],
];

function flatten(value: unknown, prefix = ""): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return prefix ? { [prefix]: value } : {};
  return Object.fromEntries(Object.entries(value).flatMap(([key, child]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return child && typeof child === "object" && !Array.isArray(child)
      ? Object.entries(flatten(child, path)) : [[path, child]];
  }));
}

function show(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function comparison(a: unknown, b: unknown) {
  const left = flatten(a);
  const right = flatten(b);
  return Array.from(new Set([...Object.keys(left), ...Object.keys(right)])).sort()
    .map((key) => ({ key, a: left[key], b: right[key], changed: JSON.stringify(left[key]) !== JSON.stringify(right[key]) }));
}

function finished(data: Json) {
  return data?.records?.find((r: Json) => r.record_kind === "completed" || r.record_kind === "failed");
}

function Equity({ a, b, aId, bId }: { a: Json; b: Json; aId: string; bId: string }) {
  const ink = document.documentElement.dataset.theme === "dark" ? "#cbd5e1" : "#475569";
  const as = a?.detail?.series ?? {};
  const bs = b?.detail?.series ?? {};
  const months = Array.from(new Set([...(as.month ?? []), ...(bs.month ?? [])])).sort() as string[];
  if (!months.length) return <Empty text="两次 trial 都没有月末净值序列" />;
  const lines = [
    { id: aId, series: as, color: "#2563eb" },
    { id: bId, series: bs, color: "#ea580c" },
  ].flatMap(({ id, series, color }) => ["excess_nav", "nav"].filter((key) => Array.isArray(series[key])).map((key) => {
    const byMonth = new Map<string, number>((series.month as string[]).map((month, index) => [month, series[key][index]]));
    return { name: `${id} · ${key === "excess_nav" ? "超额净值" : "组合净值"}`, type: "line" as const,
      showSymbol: false, connectNulls: false, lineStyle: { type: key === "nav" ? "dashed" as const : "solid" as const },
      itemStyle: { color }, data: months.map((month) => byMonth.get(month) ?? null) };
  }));
  return <Chart height={320} option={{
    tooltip: { trigger: "axis" }, legend: { top: 0, textStyle: { color: ink } },
    grid: { left: 48, right: 18, top: 56, bottom: 32 },
    xAxis: { type: "category", data: months, axisLabel: { color: ink } }, yAxis: { type: "value", scale: true, axisLabel: { color: ink } }, series: lines,
  }} />;
}

export default function Compare({ query: queryText }: { query: string }) {
  const query = new URLSearchParams(queryText);
  const aId = query.get("a") ?? "";
  const bId = query.get("b") ?? "";
  const valid = ID.test(aId) && ID.test(bId) && aId !== bId;
  const left = useApi<Json>(valid ? `/api/strategy/${aId}` : null);
  const right = useApi<Json>(valid ? `/api/strategy/${bId}` : null);
  if (!valid) return <div className="page"><div className="subnav"><a href={link("/mandates")}>← 策略列表</a></div><Empty text="请在策略列表中勾选两个不同的 trial" /></div>;
  if (!left.data || !right.data) return <Loading error={left.error ?? right.error} />;
  const a = left.data;
  const b = right.data;
  const ar = finished(a);
  const br = finished(b);
  const ac = a.detail?.acceptance?.checks ?? {};
  const bc = b.detail?.acceptance?.checks ?? {};
  const checks = Array.from(new Set([...Object.keys(ac), ...Object.keys(bc)])).sort();
  const am = ar?.metrics ?? {};
  const bm = br?.metrics ?? {};
  const metrics = METRICS.filter(([key]) => am[key] != null || bm[key] != null);
  const config = comparison(a.detail?.task, b.detail?.task);
  const changedConfig = config.filter((row) => row.changed);
  return <div className="page">
    <div className="subnav"><a href={link("/mandates")}>← 策略列表</a><span className="muted small">两个 trial 并排对比</span></div>
    <Card title="策略对比">
      <div className="table-scroll"><table className="table"><thead><tr><th>项目</th><th><a href={link(`/strategy/${aId}`)}>{aId}</a></th><th><a href={link(`/strategy/${bId}`)}>{bId}</a></th></tr></thead><tbody>
        <tr><td>结果</td><td><Badge value={ar?.outcome ?? "open"} /></td><td><Badge value={br?.outcome ?? "open"} /></td></tr>
        <tr><td>证据</td><td>{ar?.evidence_tier === "holdout" ? "留出集" : ar?.evidence_tier === "dev" ? "开发" : "—"}</td><td>{br?.evidence_tier === "holdout" ? "留出集" : br?.evidence_tier === "dev" ? "开发" : "—"}</td></tr>
        <tr><td>验收</td><td>{a.detail?.acceptance?.passed == null ? "—" : a.detail.acceptance.passed ? "通过" : "未通过"}</td><td>{b.detail?.acceptance?.passed == null ? "—" : b.detail.acceptance.passed ? "通过" : "未通过"}</td></tr>
      </tbody></table></div>
    </Card>
    <Card title="关键指标"><div className="table-scroll"><table className="table"><thead><tr><th>指标</th><th>{aId}</th><th>{bId}</th></tr></thead><tbody>
      {metrics.map(([key, label, unit]) => <tr key={key}><td>{label}</td><td>{unit === "pct" ? fmtPct(am[key], 2) : fmtNum(am[key], 3)}</td><td>{unit === "pct" ? fmtPct(bm[key], 2) : fmtNum(bm[key], 3)}</td></tr>)}
      <tr><td>搜索折扣后</td><td>{fmtNum(am.search_discount?.deflated_ratio, 3)}</td><td>{fmtNum(bm.search_discount?.deflated_ratio, 3)}</td></tr>
      <tr><td>累计试验次数 N</td><td>{fmtNum(am.search_discount?.trials)}</td><td>{fmtNum(bm.search_discount?.trials)}</td></tr>
    </tbody></table></div></Card>
    <Card title="逐项验收"><div className="table-scroll"><table className="table"><thead><tr><th>项目</th><th>{aId}</th><th>{bId}</th></tr></thead><tbody>
      {checks.map((key) => <tr key={key}><td title={key}>{CHECK_LABEL[key] ?? key}</td>{[ac[key], bc[key]].map((check, index) => <td key={index}>{check ? <><span className={`badge ${check[2] ? "green" : "red"}`}>{check[2] ? "通过" : "未通过"}</span> {fmtCheck(key, check[0])} <span className="muted small">/ 门槛 {checkDirection(key)} {thresholdText(key, check[0], check[1])}</span></> : "—"}</td>)}</tr>)}
    </tbody></table></div>{!checks.length && <Empty text="暂无逐项验收数据" />}</Card>
    <Card title="月末净值叠加"><Equity a={a} b={b} aId={aId} bId={bId} /></Card>
    <Card title="锁定任务配置差异">
      {!config.length ? <Empty text="暂无锁定配置" /> : <><p className="muted small">显示 {changedConfig.length} 个变化字段；配置来自各 trial 的结果 artifact。</p><div className="table-scroll"><table className="table compare-config"><thead><tr><th>字段</th><th>{aId}</th><th>{bId}</th></tr></thead><tbody>
        {changedConfig.map((row) => <tr key={row.key}><td className="mono">{row.key}</td><td className="small">{show(row.a)}</td><td className="small">{show(row.b)}</td></tr>)}
      </tbody></table></div></>}
    </Card>
  </div>;
}
