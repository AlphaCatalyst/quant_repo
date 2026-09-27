import { useApi, type Json } from "../api";
import { Badge, Card, Empty, fmtNum, fmtTime, link, Loading, Progress } from "../components";

const FUNNEL_KEYS: [string, string][] = [
  ["submitted", "提交"],
  ["l1", "L1"],
  ["l2", "L2"],
  ["l3", "L3"],
  ["holdout_passed", "holdout"],
];

export default function Overview() {
  const { data, error } = useApi<Json>("/api/overview", 30000);
  if (!data) return <Loading error={error} />;
  const running = data.campaigns.filter((c: Json) => c.status === "running").length;
  return (
    <div className="page">
      <div className="stats-row">
        <Stat label="运行中的 campaign" value={running} />
        <Stat label="dev trial 总数" value={data.ledger.completed_trials} />
        <Stat label="不同候选" value={data.ledger.distinct_candidates} />
        <Stat label="因子库" value={data.library_size} />
        <Stat label="待回复请求" value={data.inbox.open_requests} tone={data.inbox.open_requests ? "amber" : undefined} />
        <Stat label="待批准 holdout" value={data.inbox.pending_holdout} tone={data.inbox.pending_holdout ? "amber" : undefined} />
        <Stat label="待评审" value={data.inbox.open_reviews} tone={data.inbox.open_reviews ? "amber" : undefined} />
      </div>

      <Card title="Campaigns">
        {data.campaigns.length === 0 ? (
          <Empty />
        ) : (
          <div className="campaign-grid">
            {[...data.campaigns].reverse().map((c: Json) => (
              <a key={c.campaign_id} className="campaign-card" href={link(`/campaign/${c.campaign_id}`)}>
                <div className="campaign-head">
                  <strong>{c.campaign_id}</strong>
                  <Badge value={c.status} />
                </div>
                <div className="muted small">{c.title}</div>
                <Progress label="trial" used={c.budgets.trials.used} budget={c.budgets.trials.budget} />
                <Progress label="turn" used={c.budgets.turns.used} budget={c.budgets.turns.budget} />
                <div className="funnel-mini">
                  {FUNNEL_KEYS.map(([k, label]) => (
                    <span key={k}>
                      {label} <b>{c.funnel[k] ?? 0}</b>
                    </span>
                  ))}
                </div>
                <div className="muted small">
                  开始 {fmtTime(c.started_at)} · 费用 ${fmtNum(c.budgets.usage.cost_usd, 2)}
                </div>
              </a>
            ))}
          </div>
        )}
      </Card>

      <div className="two-col">
        <Card title="Ledger">
          <table className="kv">
            <tbody>
              <tr><td>completed trials</td><td>{data.ledger.completed_trials}</td></tr>
              <tr><td>评估出错的 trial</td><td>{data.ledger.outcomes?.error ?? 0}</td></tr>
              <tr><td>distinct candidates</td><td>{data.ledger.distinct_candidates}</td></tr>
            </tbody>
          </table>
          <h4>主要失败原因</h4>
          <FailureList reasons={data.ledger.failure_reasons} />
        </Card>
        <DataCard data={data.data} />
      </div>
    </div>
  );
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
