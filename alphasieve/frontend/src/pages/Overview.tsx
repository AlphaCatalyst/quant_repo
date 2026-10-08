import { useApi, type Json } from "../api";
import { Badge, Card, DataTable, Empty, fmtNum, fmtTime, link, Loading } from "../components";
import { InboxSummary } from "./Inbox";
import ResearchProgress from "./Progress";
import { ControlStrip } from "./Control";
import { MandateSummary } from "./Mandates";
import { BookSummary } from "./Book";
import { CapabilityMap, PersonalStrip } from "./Capabilities";
const FAILURE_LABELS: Record<string, string> = {
  "l0.structure": "L0 · 结构检查未通过",
  "l0.campaign_horizon": "L0 · 预测周期不符",
  "l1.coverage": "L1 · 覆盖率不足",
  "l1.valid_dates": "L1 · 有效交易日不足",
  "l1.ic_mean": "L1 · RankIC 不足",
  "l1.icir": "L1 · ICIR 不足",
  "l1.library_corr": "L1 · 与因子库相关过高",
  "l2.subwindows_same_sign": "L2 · 子窗口方向不稳定",
  "l2.neutral_ratio": "L2 · 中性化后信号不足",
  "l2.cost_adjusted_excess": "L2 · 成本后超额不足",
  "l2.marginal_ic": "L2 · 边际 IC 不足",
  "l2.neighborhood_trials": "L2 · 邻域试验数超限",
  "l3.dsr": "L3 · 搜索折扣未通过",
};

export function failureLabel(code: string): string {
  return FAILURE_LABELS[code] ?? `${code.split(".")[0]?.toUpperCase() || "检查"} · 其他检查未通过`;
}

export default function Overview() {
  const { data, error } = useApi<Json>("/api/overview", 30000);
  const mandates = useApi<Json>("/api/mandates", 30000).data?.mandates ?? [];
  const activity = useApi<Json>("/api/ledger?limit=8", 30000).data?.trials ?? [];
  if (!data) return <Loading error={error} />;
  const passed = mandates.filter((m: Json) => m.trials.some((t: Json) => t.acceptance?.passed)).length;
  const exhausted = mandates.filter((m: Json) => m.dev_trials >= m.budget).length;
  return (
    <div className="page">
      <InboxSummary />

      <h3 className="section-title">我的账户</h3>
      <BookSummary />
      <PersonalStrip />

      <h3 className="section-title">策略任务
        <span className="section-sub">{mandates.length} 个任务 · {passed} 个有试验通过验收 · {exhausted} 个开发期预算已用完</span>
        <a className="small" href={link("/mandates")}>全部明细 →</a>
      </h3>
      <div className="mandate-grid">
        {mandates.map((m: Json) => <section className="card" key={m.mandate}><MandateSummary m={m} compact /></section>)}
      </div>

      <h3 className="section-title">因子研究</h3>
      <ResearchProgress />
      <div className="metric-strip">
        <Stat label="已完成试验" value={data.ledger.completed_trials} />
        <Stat label="不同候选" value={data.ledger.distinct_candidates} />
        <Stat label="因子库" value={data.library_size} />
        <Stat label="待回复请求" value={data.inbox.open_requests} tone={data.inbox.open_requests ? "amber" : undefined} />
        <Stat label="待批准留出集" value={data.inbox.pending_holdout} tone={data.inbox.pending_holdout ? "amber" : undefined} />
        <Stat label="待评审" value={data.inbox.open_reviews} tone={data.inbox.open_reviews ? "amber" : undefined} />
      </div>

      <Card title={`研究活动（${data.campaigns.length}）`}>
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

      <h3 className="section-title">能力地图<span className="section-sub">仓库里已有的能力和它们在看板上的入口；标“仅命令行”的暂无页面</span></h3>
      <CapabilityMap />

      <h3 className="section-title">系统</h3>
      <ControlStrip />
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

export function FailureList({ reasons, level = "" }: { reasons: Record<string, number> | undefined; level?: string }) {
  const items = Object.entries(reasons ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 8);
  if (!items.length) return <Empty />;
  const max = items[0][1];
  return (
    <div className="bars">
      {items.map(([k, v]) => (
        <div key={k} className="bar-row">
          <span className="bar-label" title={level ? `${level}.${k}` : k}>{failureLabel(level ? `${level}.${k}` : k)}</span>
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
