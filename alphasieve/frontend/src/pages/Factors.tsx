import { useEffect, useMemo, useState } from "react";
import { getText, useApi, type Json } from "../api";
import { Badge, Card, Empty, exportCSV, fmtNum, fmtPct, fmtTime, link, Loading } from "../components";
import { sortRows } from "./Overview";

export function Factors() {
  const { data, error } = useApi<Json>("/api/factors?limit=5000", 60000);
  const [factorSort, setFactorSort] = useState("-created_at");
  const [filters, setFilters] = useState(() => new URLSearchParams(window.location.hash.split("?")[1] ?? ""));
  useEffect(() => {
    const update = () => setFilters(new URLSearchParams(window.location.hash.split("?")[1] ?? ""));
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  const q = filters.get("q") ?? "";
  const state = filters.get("state") ?? "";
  const campaign = filters.get("campaign") ?? "";
  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(filters);
    if (value) next.set(key, value); else next.delete(key);
    window.history.replaceState(null, "", `#/factors${next.size ? `?${next}` : ""}`);
    setFilters(next);
  };
  const rows = useMemo(() => {
    if (!data) return [];
    const needle = q.toLowerCase();
    return data.factors.filter((f: Json) =>
      (!needle || (f.name ?? "").toLowerCase().includes(needle) || (f.canonical_expression ?? "").toLowerCase().includes(needle)) &&
      (!state || f.state === state) && (!campaign || (f.campaign_id ?? "") === campaign));
  }, [data, q, state, campaign]);
  if (!data) return <Loading error={error} />;
  const searchMatch = (f: Json) => !q || (f.name ?? "").toLowerCase().includes(q.toLowerCase()) || (f.canonical_expression ?? "").toLowerCase().includes(q.toLowerCase());
  const states = Array.from(new Set(data.factors.map((f: Json) => f.state))).sort() as string[];
  const campaigns = Array.from(new Set(data.factors.map((f: Json) => f.campaign_id ?? ""))).sort() as string[];
  const stateCount = (s: string) => data.factors.filter((f: Json) => searchMatch(f) && (!campaign || (f.campaign_id ?? "") === campaign) && (!s || f.state === s)).length;
  const campaignCount = (c: string) => data.factors.filter((f: Json) => searchMatch(f) && (!state || f.state === state) && (!c || (f.campaign_id ?? "") === c)).length;
  const sorted = sortRows(rows, factorSort, {
    factor_id: (f) => f.factor_id, name: (f) => f.name, direction: (f) => f.direction,
    state: (f) => f.state, ic: (f) => f.metrics?.ic_mean, icir: (f) => f.metrics?.icir,
    coverage: (f) => f.metrics?.coverage, corr: (f) => f.metrics?.library_max_abs_corr,
    campaign: (f) => f.campaign_id, created_at: (f) => f.created_at,
  });
  return (
    <div className="page">
      <Card title={`因子（${rows.length} / ${data.count}）`} extra={
        <div className="filters">
          <button className="btn small" onClick={() => exportCSV("factors.csv", ["ID", "版本", "名称", "表达式", "方向", "状态", "IC", "ICIR", "覆盖", "库相关", "研究", "创建"], sorted.map((f: Json) => [f.factor_id, f.version, f.name, f.canonical_expression, f.direction, f.state, f.metrics?.ic_mean, f.metrics?.icir, f.metrics?.coverage, f.metrics?.library_max_abs_corr, f.campaign_id, f.created_at]))}>导出 CSV</button>
          <input aria-label="搜索因子" placeholder="搜索名称或表达式" value={q} onChange={(e) => setFilter("q", e.target.value)} />
          <select aria-label="因子状态" value={state} onChange={(e) => setFilter("state", e.target.value)}>
            <option value="">全部状态（{stateCount("")}）</option>
            {states.map((s) => <option key={s} value={s}>{s}（{stateCount(s)}）</option>)}
          </select>
          <select aria-label="研究" value={campaign} onChange={(e) => setFilter("campaign", e.target.value)}>
            <option value="">全部研究（{campaignCount("")}）</option>
            {campaigns.filter(Boolean).map((s) => <option key={s} value={s}>{s}（{campaignCount(s)}）</option>)}
          </select>
        </div>
      }>
        <FactorMatrix rows={sorted} />
        {rows.length === 0 ? <Empty /> : (
          <table className="table">
            <thead><tr>{[["factor_id", "ID"], ["name", "名称"], ["", "表达式"], ["direction", "方向"], ["", "格子"], ["state", "状态"], ["ic", "IC"], ["icir", "ICIR"], ["coverage", "覆盖"], ["corr", "库相关"], ["campaign", "研究"], ["created_at", "创建"]].map(([key, label], i) => <th key={`${key}-${i}`}>{key ? <button className="table-sort" onClick={() => setFactorSort(factorSort === key ? `-${key}` : key)}>{label}{factorSort.replace("-", "") === key ? factorSort.startsWith("-") ? " ↓" : " ↑" : ""}</button> : label}</th>)}</tr></thead>
            <tbody>
              {sorted.map((f: Json) => (
                <tr key={`${f.factor_id}@${f.version}`}>
                  <td><a href={link(`/factor/${f.factor_id}`)}>{f.factor_id}@{f.version}</a></td>
                  <td>{f.name}{f.in_library && <span className="tag">库</span>}</td>
                  <td><code>{f.canonical_expression}</code></td>
                  <td>{f.direction}</td>
                  <td className="small">{f.cell ? `${f.cell.domain}/${f.cell.form}/${f.cell.scale}` : "—"}</td>
                  <td><Badge value={f.state} /></td>
                  <td>{fmtNum(f.metrics.ic_mean, 4)}</td>
                  <td>{fmtNum(f.metrics.icir)}</td>
                  <td>{fmtPct(f.metrics.coverage, 0)}</td>
                  <td>{fmtNum(f.metrics.library_max_abs_corr, 2)}</td>
                  <td className="small">{f.campaign_id ?? "—"}</td>
                  <td className="small">{fmtTime(f.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

const FACTOR_METRICS: [string, string, (v: unknown) => string][] = [
  ["ic_mean", "IC", (v) => fmtNum(v, 4)],
  ["icir", "ICIR", (v) => fmtNum(v, 2)],
  ["coverage", "覆盖", (v) => fmtPct(v, 0)],
  ["library_max_abs_corr", "库相关", (v) => fmtNum(v, 2)],
];

function FactorMatrix({ rows }: { rows: Json[] }) {
  if (!rows.length) return null;
  const shown = rows.slice(0, 100);
  const values = FACTOR_METRICS.map(([key]) => shown.map((f: Json) => f.metrics?.[key]).filter((v: unknown): v is number => typeof v === "number" && Number.isFinite(v)));
  return <div style={{ overflowX: "auto", maxHeight: 420, overflowY: "auto", marginBottom: 16 }}>
    <h4>因子 × dev 指标（显示前 {shown.length} 项；颜色表示相对高低，不代表过关）</h4>
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
            <li key={i}>{fmtTime(e.ts)} · {e.from} → <b>{e.to}</b> <span className="muted small">({e.role}{e.reason ? `，${e.reason}` : ""})</span></li>
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
