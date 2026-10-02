import { useState } from "react";
import { getJSON, getText, useApi, type Json } from "../api";
import { Badge, Card, DataTable, Empty, STATUS_LABEL, fmtNum, fmtPct, fmtTime, link, Loading } from "../components";

export function Factors() {
  const { data, error } = useApi<Json>("/api/factors?limit=5000", 60000);
  const [extra, setExtra] = useState<Json[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  if (!data) return <Loading error={error} />;
  const rows: Json[] = [...data.factors, ...extra];
  const states = Array.from(new Set(rows.map((f) => f.state))) as string[];
  const campaigns = Array.from(new Set(rows.map((f) => f.campaign_id).filter(Boolean))) as string[];
  const loadMore = async () => {
    setLoadingMore(true);
    try { const next = await getJSON<Json>(`/api/factors?limit=5000&offset=${rows.length}`); setExtra([...extra, ...next.factors]); }
    finally { setLoadingMore(false); }
  };
  return <div className="page"><Card title={`因子（已载入 ${rows.length} / ${data.count}）`}>
    {rows.length < data.count && <div className="alert-banner">筛选与矩阵当前只覆盖已载入项。
      <button className="btn small" disabled={loadingMore} onClick={loadMore}>{loadingMore ? "载入中…" : "载入后续 5000 项"}</button></div>}
    <FactorMatrix rows={rows} />
    <DataTable rows={rows} filename="factors.csv" searchPlaceholder="搜索因子、表达式"
      filters={[
        { label: "状态", value: (f: Json) => f.state, options: states.map((v) => ({ value: v, label: STATUS_LABEL[v] ?? v })) },
        { label: "研究", value: (f: Json) => f.campaign_id ?? "", options: campaigns.map((v) => ({ value: v, label: v })) },
      ]}
      columns={[
        { key: "id", label: "ID", value: (f: Json) => `${f.factor_id}@${f.version}`, render: (f: Json) => <a href={link(`/factor/${f.factor_id}`)}>{f.factor_id}@{f.version}</a> },
        { key: "name", label: "名称", value: (f: Json) => f.name, render: (f: Json) => <>{f.name}{f.in_library && <span className="tag">库</span>}</> },
        { key: "expr", label: "表达式", value: (f: Json) => f.canonical_expression, render: (f: Json) => <code>{f.canonical_expression}</code> },
        { key: "direction", label: "方向", value: (f: Json) => f.direction },
        { key: "cell", label: "格子", value: (f: Json) => f.cell ? `${f.cell.domain}/${f.cell.form}/${f.cell.scale}` : "—" },
        { key: "state", label: "状态", value: (f: Json) => f.state, render: (f: Json) => <Badge value={f.state} /> },
        { key: "ic", label: "IC", value: (f: Json) => f.metrics?.ic_mean, render: (f: Json) => fmtNum(f.metrics?.ic_mean, 4) },
        { key: "icir", label: "ICIR", value: (f: Json) => f.metrics?.icir, render: (f: Json) => fmtNum(f.metrics?.icir) },
        { key: "coverage", label: "覆盖", value: (f: Json) => f.metrics?.coverage, render: (f: Json) => fmtPct(f.metrics?.coverage, 0) },
        { key: "corr", label: "库相关", value: (f: Json) => f.metrics?.library_max_abs_corr, render: (f: Json) => fmtNum(f.metrics?.library_max_abs_corr, 2) },
        { key: "campaign", label: "研究", value: (f: Json) => f.campaign_id },
        { key: "created", label: "创建", value: (f: Json) => f.created_at, render: (f: Json) => fmtTime(f.created_at) },
      ]} />
  </Card></div>;
}

const FACTOR_METRICS: [string, string, (v: unknown) => string][] = [
  ["ic_mean", "IC", (v) => fmtNum(v, 4)],
  ["icir", "ICIR", (v) => fmtNum(v, 2)],
  ["coverage", "覆盖", (v) => fmtPct(v, 0)],
  ["library_max_abs_corr", "库相关", (v) => fmtNum(v, 2)],
];

function FactorMatrix({ rows }: { rows: Json[] }) {
  if (!rows.length) return null;
  const shown = rows;
  const values = FACTOR_METRICS.map(([key]) => shown.map((f: Json) => f.metrics?.[key]).filter((v: unknown): v is number => typeof v === "number" && Number.isFinite(v)));
  return <div className="table-scroll matrix-scroll" style={{ maxHeight: 420 }}>
    <h4>因子 × dev 指标（共 {shown.length} 项；颜色表示相对高低，不代表过关）</h4>
    <table className="table compact">
      <thead><tr><th>因子</th>{FACTOR_METRICS.map(([, label]) => <th key={label}>{label}</th>)}</tr></thead>
      <tbody>{shown.map((f: Json) => <tr key={`${f.factor_id}@${f.version}`}>
        <td className="small"><a href={link(`/factor/${f.factor_id}`)}>{f.name || f.factor_id}</a></td>
        {FACTOR_METRICS.map(([key, , format], i) => {
          const v = f.metrics?.[key];
          const min = Math.min(...values[i]);
          const max = Math.max(...values[i]);
          const fraction = typeof v === "number" && max > min ? (v - min) / (max - min) : 0.5;
          const strength = typeof v === "number" ? 0.08 + fraction * 0.3 : 0;
          return <td key={key} style={{ background: `rgba(37, 99, 235, ${strength})` }} title={key}>{format(v)}</td>;
        })}
      </tr>)}</tbody>
    </table>
  </div>;
}

export function Factor({ id }: { id: string }) {
  const { data, error } = useApi<Json>(`/api/factors/${id}`);
  const [report, setReport] = useState<string | null>(null);
  if (!data) return <Loading error={error} />;
  const spec = data.spec;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>{data.name} <span className="muted">{data.factor_id}@{data.version}</span> <Badge value={data.state} /></h2>
          <p><code className="big">{data.canonical_expression}</code></p>
          <p className="question">{spec.hypothesis}</p>
          <div className="muted small">
            方向 {spec.direction} · 周期 {spec.horizon} 日 · 格子 {spec.cell.domain}/{spec.cell.form}/{spec.cell.scale} · 参数来源 {spec.params_source} · 创建者 {data.created_by}
          </div>
        </div>
      </div>
      <Card title="评估记录">
        {data.trials.length === 0 ? <Empty /> : data.trials.map((t: Json) => (
          <div key={t.trial_id} className="trial">
            <div className="trial-head">
              <b>{t.trial_id}</b> <span className="tag">{t.evidence_tier}</span> <Badge value={t.outcome} />
              <span className="muted small"> {t.campaign_id ?? "无 campaign"} · {t.created_by} · {fmtTime(t.created_at)}</span>
              {t.artifact_id && <button className="btn small" onClick={() => getText(`/api/artifacts/${t.artifact_id}/report`).then(setReport).catch(() => setReport("没有报告"))}>报告</button>}
            </div>
            <table className="table compact">
              <thead><tr><th>关卡</th><th>检查</th><th>值</th><th>阈值</th><th>通过</th></tr></thead>
              <tbody>
                {Object.entries(t.gates as Record<string, Json>).flatMap(([lvl, g]) => (g.checks ?? []).map((c: Json) => (
                  <tr key={`${lvl}-${c.name}`} className={c.passed ? "" : "fail"}>
                    <td>{lvl}</td><td>{c.name}</td><td>{typeof c.value === "number" ? fmtNum(c.value, 4) : String(c.value)}</td>
                    <td>{c.op} {c.threshold ?? ""}</td><td>{c.passed ? "✓" : "✗"}</td>
                  </tr>
                )))}
              </tbody>
            </table>
          </div>
        ))}
      </Card>
      <Card title="状态变迁">
        <ul className="list">
          {data.state_history.map((e: Json, i: number) => (
            <li key={i}>{fmtTime(e.ts)} · {STATUS_LABEL[e.from] ?? e.from} → <b>{STATUS_LABEL[e.to] ?? e.to}</b> <span className="muted small">({e.role}{e.reason ? `，${e.reason}` : ""})</span></li>
          ))}
        </ul>
      </Card>
      {report !== null && (
        <div className="drawer" onClick={() => setReport(null)}>
          <div className="drawer-body" onClick={(e) => e.stopPropagation()}>
            <button className="btn close" onClick={() => setReport(null)}>关闭</button>
            <pre className="pre">{report}</pre>
          </div>
        </div>
      )}
    </div>
  );
}
