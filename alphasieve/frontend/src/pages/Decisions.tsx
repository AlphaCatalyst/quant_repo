import { useEffect, useMemo, useState } from "react";
import { useApi, type Json } from "../api";
import { Card, Chart, DataTable, Empty, fmtNum, fmtPct, fmtTime, link, Loading, Progress } from "../components";

const POOL: Record<string, string> = { all: "全 A", large: "大盘", growth: "成长" };
const CHECK: Record<string, [string, (v: Json) => string]> = {
  cohort_win_share: ["各起始年份都不差于不动", (v) => `胜出年份 ${fmtPct(v, 0)}`],
  stress_cohorts: ["压力年份（2008/2015/2018/2021）不落后", (v) => Object.entries(v ?? {}).map(([y, d]) => `${y} ${signed(d as number)}`).join(" · ")],
  annual_cost: ["年化交易成本 ≤ 0.5%", (v) => fmtPct(v, 2)],
  beats_matched_random: ["胜过同频随机调仓（Holm 校正后 p<0.05）", (v) => `p=${fmtNum(v, 3)}`],
  not_worse_than_mechanical: ["不差于每月机械调回等权", (v) => signed(v)],
};
const TASK_STATUS: Record<string, string> = { run: "已在 dev 运行", probe: "已做可行性探查", book: "已上线（持仓页）" };

function signed(v: unknown, digits = 3) {
  return typeof v === "number" && isFinite(v) ? `${v >= 0 ? "+" : ""}${v.toFixed(digits)}` : "—";
}

export function ruleLabel(rule: Json | undefined): string {
  if (!rule) return "—";
  const parts = [rule.target === "inverse_vol" ? "按波动倒数配权" : "等权"];
  parts.push(rule.cap == null ? "不设单票上限" : `单票 ≤ ${fmtPct(rule.cap, 0)}`);
  parts.push(rule.band ? `偏离 ≥ ${fmtPct(rule.band, 0)} 才调` : "到期即调");
  parts.push(rule.review_days === 5 ? "每周检查" : rule.review_days === 21 ? "每月检查" : `每 ${rule.review_days} 日检查`);
  return parts.join(" · ");
}

export default function Decisions({ anchor }: { anchor?: string }) {
  const { data, error } = useApi<Json>("/api/decisions", 60000);
  const [pool, setPool] = useState("all");
  const loaded = !!data;
  useEffect(() => { if (loaded && anchor) document.getElementById(anchor)?.scrollIntoView(); }, [loaded, anchor]);
  if (!data) return <Loading error={error} />;
  const p3 = data.p3;
  const current = p3?.pools?.find((p: Json) => p.pool === pool) ?? p3?.pools?.[0];
  return (
    <div className="page">
      <div className="page-head"><div>
        <h2>个人账户决策任务</h2>
        <p className="muted question">“拿着不动”本身也是一个决策。这里把个人账户里常见的动作（调仓、止损、换股、看公告、选股）定义成可检验的量化任务：
          在大量合成账户上和“不动”“随机动”比较，只有稳定胜出、且能扛住多重比较校正的规则才算通过。全部只用 dev 数据（截至 {p3?.data_end ?? "2022-12-31"}）。
          <a href={link("/docs/personal-decision-tasks.md")}>任务定义文档 →</a></p>
      </div></div>

      <div className="decision-tasks">
        {data.tasks.map((t: Json) => <a key={t.task} className="decision-task" href={link(t.task === "P5" ? "/book" : `/decisions#task-${t.task}`)}>
          <strong>{t.task}</strong><span>{t.title}</span><small className="muted">{TASK_STATUS[t.kind]}</small></a>)}
      </div>

      {p3 ? <section id="task-P3" className="card">
        <header><h3>P3 · 仓位与再平衡：该不该调、多久调一次</h3>
          <div className="progress-switch">{p3.pools.map((p: Json) => <button key={p.pool} aria-pressed={p.pool === current.pool} onClick={() => setPool(p.pool)}>{POOL[p.pool] ?? p.pool}</button>)}</div>
        </header>
        <P3Pool p3={p3} pool={current} />
      </section> : <Card title="P3 · 仓位与再平衡"><Empty text="还没有运行结果：alphasieve decision p3" /></Card>}

      <h3 className="section-title">可行性探查<span className="section-sub">只回答“值不值得立项做”，不做调参与验收</span></h3>
      <div className="two-col">
        <ProbeP1 r={data.probes.p1} />
        <ProbeP2 r={data.probes.p2} />
        <ProbeP4 r={data.probes.p4} />
        <ProbeP6 r={data.probes.p6} />
        <ProbeP7 r={data.probes.p7} />
        <Card title="决策试验账本">
          <p className="small muted">每条被评估过的规则都记一次试验，用于多重比较校正，不能删除。</p>
          <Progress label="P3 试验预算" used={data.trials.by_task?.P3 ?? 0} budget={data.trials.p3_budget ?? 0} />
          <table className="kv"><tbody>
            <tr><td>试验总数</td><td>{data.trials.count}</td></tr>
            {Object.entries(data.trials.by_task ?? {}).map(([k, v]) => <tr key={k}><td>{k}</td><td>{String(v)}</td></tr>)}
            <tr><td>最近一次</td><td>{fmtTime(data.trials.latest)}</td></tr>
          </tbody></table>
        </Card>
      </div>
    </div>
  );
}

function P3Pool({ p3, pool }: { p3: Json; pool: Json }) {
  const selected = pool.rules.find((r: Json) => r.rule_id === pool.selected);
  const checks = Object.entries(pool.selected_acceptance?.checks ?? {}) as [string, Json][];
  const passed = pool.selected_acceptance?.pass;
  const baseline = [
    { label: "不动（买入后一直拿着）", r: pool.no_action },
    { label: "每月机械调回等权", r: pool.mechanical },
    { label: `最优规则：${ruleLabel(selected?.rule)}`, r: selected },
  ];
  const cohorts = pool.selected_cohorts ?? {};
  const years = Object.keys(cohorts).sort();
  const chart = useMemo(() => ({
    tooltip: { trigger: "axis" as const }, legend: { data: ["最优规则", "不动"] },
    grid: { left: 48, right: 16, top: 30, bottom: 28 },
    xAxis: { type: "category" as const, data: years },
    yAxis: { type: "value" as const, name: "确定性等价 γ=4", scale: true },
    series: [
      { name: "最优规则", type: "line" as const, data: years.map((y) => cohorts[y].ce_gamma4) },
      { name: "不动", type: "line" as const, data: years.map((y) => cohorts[y].hold_ce_gamma4) },
    ],
  }), [pool.pool]);
  return <>
    <p className="mandate-verdict">
      {fmtNum(pool.accounts)} 个合成账户（每户 2–5 只、随机权重，起始 {pool.start_range?.join(" ~ ")}，持有一年）。
      最优规则把一年后的确定性等价（γ=4）从 <b className="plain">{fmtNum(pool.no_action.ce_gamma4)}</b> 提到 <b className="plain">{fmtNum(selected?.ce_gamma4)}</b>，
      回撤超 30% 的概率从 {fmtPct(pool.no_action.p_mdd_30, 0)} 降到 {fmtPct(selected?.p_mdd_30, 0)}，年化成本约 {fmtPct(selected?.mean_cost, 2)}。
      验收结论：{passed ? <span className="ok">通过</span> : <b>未通过</b>}{!passed && "（同频随机基线这一项没过，详见下方说明）"}。
    </p>
    <div className="check-chips">
      {checks.map(([k, c]) => <span key={k} className={`check-chip ${c.pass ? "ok" : "bad"}`} title={k}>{c.pass ? "✓" : "✗"} {CHECK[k]?.[0] ?? k} <span className="muted">{CHECK[k]?.[1](c.value)}</span></span>)}
    </div>
    <div className="table-scroll"><table className="table compact">
      <thead><tr><th>方案</th><th className="num">确定性等价 γ=4</th><th className="num">γ=2</th><th className="num">平均终值</th><th className="num">中位终值</th><th className="num">P(回撤&gt;30%)</th><th className="num">P(亏损&gt;30%)</th><th className="num">成本</th><th className="num">年调仓次数</th></tr></thead>
      <tbody>{baseline.map(({ label, r }) => <tr key={label} className={r === selected ? "best-row" : ""}>
        <td>{label}</td><td className="num">{fmtNum(r?.ce_gamma4)}</td><td className="num">{fmtNum(r?.ce_gamma2)}</td>
        <td className="num">{fmtNum(r?.mean_wealth)}</td><td className="num">{fmtNum(r?.median_wealth)}</td>
        <td className="num">{fmtPct(r?.p_mdd_30)}</td><td className="num">{fmtPct(r?.p_loss_30)}</td>
        <td className="num">{fmtPct(r?.mean_cost, 2)}</td><td className="num">{fmtNum(r?.mean_rebalances, 1)}</td></tr>)}</tbody>
    </table></div>

    <div className="two-col decision-detail">
      <div>
        <h4>按起始年份：最优规则 vs 不动</h4>
        {years.length ? <Chart option={chart} height={240} /> : <Empty />}
      </div>
      <div>
        <h4>为什么“同频随机”没过</h4>
        <p className="small">同频随机基线让每个账户保持同样的调仓次数和目标权重，只是把调仓日随机打乱，重复 {p3.config?.random_reps} 次。
          它检验的是“规则选的时机”有没有价值，而不只是“调仓”本身有价值。</p>
        <p className="small">最优规则胜过全部 {p3.config?.random_reps} 次随机（单项 p={fmtNum(selected?.random?.p, 4)}），
          但 24 条规则一起做 Holm 校正后 p={fmtNum(selected?.random?.p_holm, 3)}。{p3.config?.random_reps} 次随机能给出的最小 p 约为 1/{(p3.config?.random_reps ?? 0) + 1}，
          乘以 24 后不可能低于 0.05，这是试验设计的分辨率不够，不是规则被否定；按事先约定不改判。</p>
        <p className="small muted">{p3.method}</p>
        <p className="small muted">运行 {p3.run_id?.slice(0, 8)} · {fmtTime(p3.created_at)} · 配置 {p3.config_hash?.slice(0, 12)}</p>
      </div>
    </div>

    <h4>全部 24 条规则</h4>
    <DataTable rows={pool.rules as Json[]} filename={`p3-${pool.pool}.csv`} searchPlaceholder="搜索规则"
      filters={[{ label: "配权", value: (r: Json) => r.rule.target, options: [{ value: "equal", label: "等权" }, { value: "inverse_vol", label: "波动倒数" }] },
        { label: "检查频率", value: (r: Json) => String(r.rule.review_days), options: [{ value: "5", label: "每周" }, { value: "21", label: "每月" }] }]}
      columns={[
        { key: "rule", label: "规则", value: (r: Json) => ruleLabel(r.rule), render: (r: Json) => <span className={r.rule_id === pool.selected ? "ok" : ""}>{ruleLabel(r.rule)}{r.rule_id === pool.selected && " ★"}</span> },
        { key: "ce4", label: "CE γ=4", value: (r: Json) => r.ce_gamma4, render: (r: Json) => fmtNum(r.ce_gamma4) },
        { key: "vs", label: "比不动", value: (r: Json) => r.ce_gamma4 - pool.no_action.ce_gamma4, render: (r: Json) => signed(r.ce_gamma4 - pool.no_action.ce_gamma4) },
        { key: "mdd", label: "P(回撤>30%)", value: (r: Json) => r.p_mdd_30, render: (r: Json) => fmtPct(r.p_mdd_30) },
        { key: "cost", label: "成本", value: (r: Json) => r.mean_cost, render: (r: Json) => fmtPct(r.mean_cost, 2) },
        { key: "reb", label: "调仓次数", value: (r: Json) => r.mean_rebalances, render: (r: Json) => fmtNum(r.mean_rebalances, 1) },
        { key: "p", label: "随机 p", value: (r: Json) => r.random?.p, render: (r: Json) => fmtNum(r.random?.p, 4) },
        { key: "holm", label: "Holm p", value: (r: Json) => r.random?.p_holm, render: (r: Json) => fmtNum(r.random?.p_holm, 3) },
        { key: "pass", label: "验收", value: (r: Json) => r.acceptance?.pass ? "通过" : "未通过" },
      ]} />
  </>;
}

function Probe({ id, title, verdict, good, children }: { id: string; title: string; verdict: string; good?: boolean; children: React.ReactNode }) {
  return <section className="card" id={`task-${id}`}>
    <header><h3>{id} · {title}</h3><span className={`badge ${good ? "green" : "amber"}`}>{verdict}</span></header>
    {children}
  </section>;
}

function ProbeP1({ r }: { r: Json }) {
  if (!r) return <Card title="P1 · 大跌预警"><Empty /></Card>;
  const h = r.halve_on_warning_vs_random_halving;
  const years = Object.keys(r.by_year ?? {});
  return <Probe id="P1" title="持有中的大跌预警" verdict="有预测力，值得做" good>
    <p className="small">预测“未来 20 个交易日跌超 15%”。2016 年前训练，之后检验：AUC {fmtNum(r.auc, 2)}（只用波动率 {fmtNum(r.auc_vol_only, 2)}），
      风险最高的 10% 股票出事概率 {fmtPct(r.top_decile_rate)}，是基准 {fmtPct(r.base_rate)} 的 {fmtNum(r.top_decile_lift, 1)} 倍。</p>
    <p className="small">预警时减半仓位 vs 随机减半：每月多 {fmtPct(h?.mean_per_month, 2)}（{h?.months} 个月，t={fmtNum(h?.t, 1)}）。</p>
    <table className="table compact"><thead><tr><th>年份</th>{years.map((y) => <th key={y} className="num">{y}</th>)}</tr></thead><tbody>
      <tr><td>基准率</td>{years.map((y) => <td key={y} className="num">{fmtPct(r.by_year[y].base_rate, 0)}</td>)}</tr>
      <tr><td>前 10% 倍数</td>{years.map((y) => <td key={y} className="num">{fmtNum(r.by_year[y].top_decile_lift, 1)}</td>)}</tr>
    </tbody></table>
  </Probe>;
}

function ProbeP2({ r }: { r: Json }) {
  if (!r) return <Card title="P2 · 换股"><Empty /></Card>;
  const q = r.by_gap_quintile ?? [];
  return <Probe id="P2" title="换股：卖 A 买 B 是否更好" verdict="信号弱，只在分差大时有用" good={false}>
    <p className="small">事先固定的打分（{r.score}）。{fmtNum(r.pairs)} 对比较中，高分那只跑赢的比例 {fmtPct(r.hit_rate)}；
      但两只股票的收益差标准差有 {fmtPct(r.std_difference, 0)}，远大于一次换股的成本 {fmtPct(r.switch_cost_round_trip, 2)}，单次换股基本是掷硬币。</p>
    <div className="bars">{q.map((x: Json) => <div key={x.quintile_of_score_gap} className="bar-row">
      <span className="bar-label">分差第 {x.quintile_of_score_gap} 档</span>
      <span className="bar-track"><span className="bar-fill" style={{ width: `${Math.max(0, (x.hit_rate - 0.45) / 0.15) * 100}%` }} /></span>
      <span className="bar-value">{fmtPct(x.hit_rate, 0)}</span></div>)}</div>
  </Probe>;
}

function ProbeP4({ r }: { r: Json }) {
  if (!r) return <Card title="P4 · 公告事件"><Empty /></Card>;
  const events = Object.entries(r.events ?? {}) as [string, Json][];
  return <Probe id="P4" title="公告事件后的处理" verdict={r.announcement_days < 200 ? "等待公告回补" : "已有样本"} good={r.announcement_days >= 200}>
    <p className="small">用了 {r.announcement_days} 个有公告的 dev 交易日（{r.first_day} ~ {r.last_day}）。下表是事件后相对全 A 等权的累计超额（CAR），样本少时只看方向。</p>
    <table className="table compact"><thead><tr><th>事件</th><th className="num">次数</th><th className="num">5 日</th><th className="num">20 日</th><th className="num">60 日</th></tr></thead><tbody>
      {events.map(([k, e]) => <tr key={k}><td>{k}</td><td className="num">{e.events}</td>
        {["car5", "car20", "car60"].map((c) => <td key={c} className={`num ${e[c]?.mean > 0 ? "pnl-up" : "pnl-down"}`}>{fmtPct(e[c]?.mean, 2)}</td>)}</tr>)}
    </tbody></table>
    <p className="small muted">{r.note}</p>
  </Probe>;
}

function ProbeP6({ r }: { r: Json }) {
  if (!r) return <Card title="P6 · 周期股"><Empty /></Card>;
  const rows = Object.entries(r.industries ?? {}) as [string, Json][];
  return <Probe id="P6" title="周期股估值位置" verdict={r.bp_beats_ep_share >= 2 / 3 ? "市净率分位更好用" : "未达 2/3 门槛"} good={r.bp_beats_ep_share >= 2 / 3}>
    <p className="small">周期行业里，用市净率分位还是市盈率分位判断“便宜”？{r.industries_scored} 个行业中市净率更好的占 {fmtPct(r.bp_beats_ep_share, 0)}（门槛 2/3）。
      相关系数为负表示“越便宜、后 12 个月越好”。</p>
    <div className="table-scroll"><table className="table compact"><thead><tr><th>行业</th><th className="num">月数</th><th className="num">市净率分位 ρ</th><th className="num">市盈率分位 ρ</th><th>更好</th></tr></thead><tbody>
      {rows.map(([k, x]) => <tr key={k}><td>{k}</td><td className="num">{x.months}</td><td className="num">{fmtNum(x.spearman_bp_pct_vs_12m_excess, 2)}</td>
        <td className="num">{fmtNum(x.spearman_ep_pct_vs_12m_excess, 2)}</td><td>{x.bp_beats_ep ? "市净率" : "市盈率"}</td></tr>)}
    </tbody></table></div>
    <p className="small muted">{r.note}</p>
  </Probe>;
}

function ProbeP7({ r }: { r: Json }) {
  if (!r) return <Card title="P7 · 候选股筛选"><Empty /></Card>;
  const ks = Object.entries(r.results ?? {}) as [string, Json][];
  return <Probe id="P7" title="候选股筛选" verdict="不提高胜率，只降低大亏概率" good={false}>
    <p className="small">筛选：{r.screen}。和从同一池子随机挑同样数量相比，一年后跑赢市场的概率与亏超 30% 的概率：</p>
    <table className="table compact"><thead><tr><th>持股数</th><th className="num">随机 跑赢率</th><th className="num">筛选 跑赢率</th><th className="num">随机 大亏率</th><th className="num">筛选 大亏率</th></tr></thead><tbody>
      {ks.map(([k, x]) => <tr key={k}><td>{k.replace("k", "")} 只</td><td className="num">{fmtPct(x.random?.p_beat_market)}</td><td className="num">{fmtPct(x.screen?.p_beat_market)}</td>
        <td className="num">{fmtPct(x.random?.p_loss_30)}</td><td className="num">{fmtPct(x.screen?.p_loss_30)}</td></tr>)}
    </tbody></table>
    {r.missing && <p className="small muted">未包含：{r.missing}</p>}
  </Probe>;
}
