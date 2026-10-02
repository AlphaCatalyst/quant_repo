import { useApi, type Json } from "../api";
import { Badge, Card, DataTable, Empty, fmtNum, fmtPct, fmtTime, link, Loading, Progress } from "../components";

const TITLES: Record<string, string> = { A: "中证 500 增强", B: "行业 ETF 轮动", C: "业绩超预期漂移", D: "股指期货对冲" };

export default function Overview() {
  const { data, error } = useApi<Json>("/api/overview", 30000);
  const mandates = useApi<Json>("/api/mandates", 30000).data?.mandates ?? [];
  const activity = useApi<Json>("/api/ledger?limit=8", 30000).data?.trials ?? [];
  if (!data) return <Loading error={error} />;
  return (
    <div className="page">
      <div className="metric-strip">
        <Stat label="dev trial 总数" value={data.ledger.completed_trials} />
        <Stat label="不同候选" value={data.ledger.distinct_candidates} />
        <Stat label="因子库" value={data.library_size} />
        <Stat label="待回复请求" value={data.inbox.open_requests} tone={data.inbox.open_requests ? "amber" : undefined} />
        <Stat label="待批准 holdout" value={data.inbox.pending_holdout} tone={data.inbox.pending_holdout ? "amber" : undefined} />
        <Stat label="待评审" value={data.inbox.open_reviews} tone={data.inbox.open_reviews ? "amber" : undefined} />
      </div>

      <div className="mandate-strip">
        {mandates.map((m: Json) => {
          const dev = m.trials.filter((t: Json) => t.tier === "dev" && t.status === "completed");
          const ranked = [...dev].filter((t: Json) => typeof (t.metrics?.information_ratio ?? t.metrics?.sharpe) === "number")
            .sort((a: Json, b: Json) => (b.metrics?.information_ratio ?? b.metrics?.sharpe) - (a.metrics?.information_ratio ?? a.metrics?.sharpe));
          const best = ranked[0];
          const pending = m.holdout_requests.filter((q: Json) => q.status === "pending").length;
          return <div className="stat" key={m.mandate}>
            <div><a href={link(`/mandates?mandate=${m.mandate}`)}><strong>{m.mandate} · {TITLES[m.mandate]}</strong></a></div>
            <Progress label="dev trial 预算" used={m.dev_trials} budget={m.budget} />
            <div className="small">最佳 dev {best ? <a href={link(`/strategy/${best.trial_id}`)}>{best.trial_id}</a> : "—"}</div>
            <div className="small muted">{best ? `${best.metrics?.information_ratio != null ? "IR" : "夏普"} ${fmtNum(best.metrics?.information_ratio ?? best.metrics?.sharpe, 2)} · 净超额 ${fmtPct(best.metrics?.annual_excess ?? best.metrics?.annual_return, 1)} · ${dev.length} 次中择优` : "暂无已完成的 dev trial"}</div>
            <div className="small muted">holdout：已批准 {m.holdout_reads.used} / {m.holdout_reads.budget}，待批准 {pending}</div>
          </div>;
        })}
      </div>

      <Card title={`因子研究（${data.campaigns.length}）`}>
        <DataTable rows={data.campaigns as Json[]} filename="campaigns.csv" searchPlaceholder="搜索研究"
          filters={[{ label: "状态", value: (c: Json) => c.status, options: Array.from(new Set((data.campaigns as Json[]).map((c) => c.status))).map((v: string) => ({ value: v, label: v })) }]}
          columns={[
            { key: "name", label: "研究", value: (c: Json) => c.title || c.campaign_id, render: (c: Json) => <><a href={link(`/campaign/${c.campaign_id}`)}>{c.title || c.campaign_id}</a><div className="muted small">{c.campaign_id}</div></> },
            { key: "status", label: "状态", value: (c: Json) => c.status, render: (c: Json) => <Badge value={c.status} /> },
            { key: "trials", label: "trial 用量", value: (c: Json) => c.budgets?.trials?.used, render: (c: Json) => `${fmtNum(c.budgets?.trials?.used)} / ${fmtNum(c.budgets?.trials?.budget)}` },
            ...["l1", "l2", "l3"].map((k) => ({ key: k, label: k.toUpperCase(), value: (c: Json) => c.funnel?.[k] ?? 0 })),
            { key: "started", label: "开始时间", value: (c: Json) => c.started_at, render: (c: Json) => fmtTime(c.started_at) },
            { key: "cost", label: "费用", value: (c: Json) => c.budgets?.usage?.cost_usd, render: (c: Json) => `$${fmtNum(c.budgets?.usage?.cost_usd, 2)}` },
          ]} />
      </Card>

      <div className="two-col">
        <Card title="最近记录" extra={<a className="small" href={link("/ledger")}>查看全部</a>}>
          {activity.length === 0 ? <Empty /> : <table className="table compact"><thead><tr><th>时间</th><th>对象</th><th>结果</th></tr></thead><tbody>
            {activity.map((t: Json) => <tr key={t.seq}><td className="small">{fmtTime(t.created_at)}</td>
              <td className="small">{t.factor_id ? <a href={link(`/factor/${t.factor_id}`)}>{t.factor_id}</a> : t.layer === "strategy" ? <a href={link(`/strategy/${t.trial_id}`)}>{t.trial_id}</a> : t.trial_id}</td>
              <td><Badge value={t.outcome ?? t.record_kind} /></td></tr>)}
          </tbody></table>}
        </Card>
        <Card title="试验账本">
          <table className="kv">
            <tbody>
              <tr><td>已完成试验</td><td>{data.ledger.completed_trials}</td></tr>
              <tr><td>评估出错的 trial</td><td>{data.ledger.outcomes?.error ?? 0}</td></tr>
              <tr><td>不同候选</td><td>{data.ledger.distinct_candidates}</td></tr>
            </tbody>
          </table>
          <h4>主要失败原因</h4>
          <FailureList reasons={data.ledger.failure_reasons} />
        </Card>
      </div>
    </div>
  );
}

export function sortRows(rows: Json[], key: string, accessors: Record<string, (row: Json) => unknown>): Json[] {
  const desc = key.startsWith("-");
  const get = accessors[key.replace(/^-/, "")];
  return [...rows].sort((a, b) => {
    const av = get?.(a), bv = get?.(b);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    const order = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv), "zh-CN", { numeric: true });
    return desc ? -order : order;
  });
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className={`stat ${tone ?? ""}`}>
      <div className="stat-value">{fmtNum(value)}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

export function FailureList({ reasons }: { reasons: Record<string, number> | undefined }) {
  const items = Object.entries(reasons ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 8);
  if (!items.length) return <Empty />;
  const max = items[0][1];
  return (
    <div className="bars">
      {items.map(([k, v]) => (
        <div key={k} className="bar-row">
          <span className="bar-label">{k}</span>
          <span className="bar-track"><span className="bar-fill" style={{ width: `${(v / max) * 100}%` }} /></span>
          <span className="bar-value">{v}</span>
        </div>
      ))}
    </div>
  );
}

export function DataCard({ data }: { data: Json }) {
  const u = data.last_daily_update;
  return (
    <Card title="数据">
      <table className="kv">
        <tbody>
          <tr><td>dev 窗口</td><td>{data.splits.dev?.start} ~ {data.splits.dev?.end}</td></tr>
          <tr><td>holdout 窗口</td><td>{data.splits.holdout?.start} ~ {data.splits.holdout?.end}（agent 不可见）</td></tr>
          <tr><td>fresh 起点</td><td>{data.splits.fresh?.start}</td></tr>
          <tr><td>dev panel</td><td>{fmtNum(data.panels.dev?.rows)} 行 · {data.panels.dev?.codes} 只 · 构建于 {fmtTime(data.panels.dev?.built_at)}</td></tr>
          <tr><td>最近一次每日更新</td><td>{u ? `${u.status}，数据到 ${u.end}，新增 ${fmtNum(u.new_rows)} 行，${fmtTime(new Date(u.logged_at * 1000).toISOString())}` : "—"}</td></tr>
        </tbody>
      </table>
    </Card>
  );
}
