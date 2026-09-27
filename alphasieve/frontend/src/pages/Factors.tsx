import { useMemo, useState } from "react";
import { getText, useApi, type Json } from "../api";
import { Badge, Card, Empty, fmtNum, fmtPct, fmtTime, link, Loading } from "../components";

export function Factors() {
  const { data, error } = useApi<Json>("/api/factors?limit=5000", 60000);
  const [q, setQ] = useState("");
  const [state, setState] = useState("");
  const [campaign, setCampaign] = useState("");
  const rows = useMemo(() => {
    if (!data) return [];
    const needle = q.toLowerCase();
    return data.factors.filter((f: Json) =>
      (!needle || f.name.toLowerCase().includes(needle) || f.canonical_expression.toLowerCase().includes(needle)) &&
      (!state || f.state === state) && (!campaign || (f.campaign_id ?? "") === campaign));
  }, [data, q, state, campaign]);
  if (!data) return <Loading error={error} />;
  const states = Array.from(new Set(data.factors.map((f: Json) => f.state))).sort() as string[];
  const campaigns = Array.from(new Set(data.factors.map((f: Json) => f.campaign_id ?? ""))).sort() as string[];
  return (
    <div className="page">
      <Card title={`因子（${rows.length} / ${data.count}）`} extra={
        <div className="filters">
          <input placeholder="搜索名称或表达式" value={q} onChange={(e) => setQ(e.target.value)} />
          <select value={state} onChange={(e) => setState(e.target.value)}>
            <option value="">全部状态</option>
            {states.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={campaign} onChange={(e) => setCampaign(e.target.value)}>
            <option value="">全部 campaign</option>
            {campaigns.map((s) => <option key={s} value={s}>{s || "（无）"}</option>)}
          </select>
        </div>
      }>
        {rows.length === 0 ? <Empty /> : (
          <table className="table">
            <thead><tr><th>ID</th><th>名称</th><th>表达式</th><th>方向</th><th>格子</th><th>状态</th><th>IC</th><th>ICIR</th><th>覆盖</th><th>库相关</th><th>campaign</th><th>创建</th></tr></thead>
            <tbody>
              {rows.map((f: Json) => (
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
