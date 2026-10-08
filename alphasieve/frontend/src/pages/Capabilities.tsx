import { useApi, type Json } from "../api";
import { Empty, fmtNum, fmtPct, link } from "../components";
import { LevelBadge } from "./RedFlags";
import { ruleLabel } from "./Decisions";

export function PersonalStrip() {
  const decisions = useApi<Json>("/api/decisions", 300000).data;
  const flags = useApi<Json>("/api/redflags", 300000).data;
  const news = useApi<Json>("/api/announcements?days=14&importance=held", 300000).data;
  const pool = decisions?.p3?.pools?.find((p: Json) => p.pool === "all");
  const best = pool?.rules?.find((r: Json) => r.rule_id === pool.selected);
  const heldFlags = (flags?.rows ?? []).filter((r: Json) => r.held) as Json[];
  const heldNews = (news?.held ?? []) as Json[];
  return <div className="three-col">
    <section className="card">
      <header><h3>仓位与调仓规则（P3）</h3><a className="small" href={link("/decisions")}>决策任务 →</a></header>
      {best ? <>
        <p className="small">dev 上 1 万个合成账户里表现最好的规则：<b>{ruleLabel(best.rule)}</b>。</p>
        <table className="kv small"><tbody>
          <tr><td>确定性等价 γ=4</td><td>{fmtNum(best.ce_gamma4)}（不动 {fmtNum(pool.no_action.ce_gamma4)}）</td></tr>
          <tr><td>回撤超 30% 概率</td><td>{fmtPct(best.p_mdd_30, 0)}（不动 {fmtPct(pool.no_action.p_mdd_30, 0)}）</td></tr>
          <tr><td>验收</td><td>{pool.selected_acceptance?.pass ? <span className="ok">通过</span> : <span className="error">未通过：同频随机基线 Holm p={fmtNum(best.random?.p_holm, 2)}</span>}</td></tr>
        </tbody></table>
      </> : <Empty text="P3 还没有运行结果" />}
    </section>
    <section className="card">
      <header><h3>持仓财报排雷</h3><a className="small" href={link("/redflags")}>全部 →</a></header>
      {heldFlags.length ? <table className="table compact"><tbody>
        {heldFlags.map((r) => <tr key={r.code}><td>{r.name ?? r.code}</td><td><LevelBadge level={r.level} /></td><td className="small muted">{r.period}</td></tr>)}
      </tbody></table> : <Empty text={flags?.asof ? "持仓不在扫描范围内" : "还没有扫描结果"} />}
      {flags?.asof && <p className="small muted">扫描日 {flags.asof}{flags.held_total > heldFlags.length && ` · 另 ${flags.held_total - heldFlags.length} 只持仓不在 ${flags.universe} 范围`} · 范围内红灯 {flags.levels?.red ?? 0}、黄灯 {flags.levels?.amber ?? 0}</p>}
    </section>
    <section className="card">
      <header><h3>持仓公告（近 14 天）</h3><a className="small" href={link("/announcements")}>全部 →</a></header>
      {heldNews.length ? <ul className="announcements">{heldNews.slice(0, 5).map((a) => <li key={a.announcement_id}>
        <span className="muted small">{a.published_at?.slice(5, 10)}</span> <b>{a.name}</b> <a href={a.pdf_url} target="_blank" rel="noreferrer" className="small">{a.title}</a></li>)}</ul>
        : <Empty text="近 14 天持仓没有公告" />}
    </section>
  </div>;
}

type Item = { name: string; href?: string; desc: string; cli?: string };
const MAP: [string, Item[]][] = [
  ["我的账户", [
    { name: "持仓与体检", href: "/book", desc: "导入券商/同花顺持仓，集中度、行业、回撤体检与再平衡建议" },
    { name: "交易行为偏差", href: "/book", desc: "处置效应、追涨、短期回购、换股价值，样本够 30 次才下结论" },
    { name: "虚拟账户", href: "/book", desc: "只允许用通过验收的规则开虚拟账户，按规则自动推进" },
    { name: "投资论点", href: "/theses", desc: "把买入理由写成可检验的情景与公式" },
    { name: "预测记录", href: "/forecasts", desc: "记录预测并到期打分" },
    { name: "告警", href: "/alerts", desc: "持仓排雷、公告、价格异动等告警" },
  ]],
  ["个人决策", [
    { name: "P3 仓位与再平衡", href: "/decisions", desc: "合成账户 + 决策回测 + 同频随机基线 + Holm 校正" },
    { name: "P1 大跌预警", href: "/decisions#task-P1", desc: "未来 20 日跌超 15% 的概率排序" },
    { name: "P2 换股", href: "/decisions#task-P2", desc: "卖 A 买 B 的胜率与成本" },
    { name: "P4 公告事件", href: "/decisions#task-P4", desc: "减持、回购等事件后的超额收益" },
    { name: "P6 周期股估值", href: "/decisions#task-P6", desc: "周期行业用市净率还是市盈率分位" },
    { name: "P7 候选股筛选", href: "/decisions#task-P7", desc: "便宜且高 ROE 的筛选能否提高胜率" },
  ]],
  ["市场", [
    { name: "财报排雷", href: "/redflags", desc: "9 条规则、同行业分位、公告日时点口径" },
    { name: "公司公告", href: "/announcements", desc: "巨潮全 A 公告、事件分类与重要度" },
  ]],
  ["研究", [
    { name: "策略任务", href: "/mandates", desc: "四个 mandate 的验收、试验预算与 holdout 申请" },
    { name: "行业口径敏感性", href: "/mandates", desc: "证监会 vs 申万历史行业下的组合偏离" },
    { name: "因子库", href: "/factors", desc: "因子 DSL、L0–L3 关卡与入库因子" },
    { name: "试验账本", href: "/ledger", desc: "所有试验只增不删，用于搜索折扣" },
    { name: "前瞻纸面跟踪", href: "/forward", desc: "锁定配置后的前瞻观察（需登录）" },
    { name: "研究 agent", desc: "LLM agent 在预算内提出并评估因子", cli: "alphasieve campaign / orchestrator" },
    { name: "程序化搜索", desc: "按搜索空间批量评估", cli: "alphasieve search" },
    { name: "策略训练", desc: "训练任务、模型层与指数增强组合回测", cli: "alphasieve train / strategy" },
    { name: "风险模型报告", desc: "独立的事前跟踪误差与偏差检验", cli: "alphasieve risk report" },
    { name: "关卡校准", desc: "零假设模拟与植入信号检出率", cli: "alphasieve gate calibrate" },
  ]],
  ["系统", [
    { name: "资源", href: "/resources", desc: "本机与远端算力、LLM 配额" },
    { name: "作业", href: "/jobs", desc: "作业队列、重试与日志" },
    { name: "健康", href: "/health", desc: "服务与数据健康检查" },
    { name: "数据", href: "/data", desc: "数据窗口、panel、每日更新与备份" },
    { name: "文档", href: "/docs", desc: "全部实现文档与状态" },
    { name: "评估服务", desc: "常驻评估 worker 与平台桥接", cli: "alphasieve evalsvc" },
  ]],
];

export function CapabilityMap() {
  return <div className="capability-map">
    {MAP.map(([group, items]) => <section className="card" key={group}>
      <header><h3>{group}</h3><span className="muted small">{items.length} 项</span></header>
      <ul className="capability-list">{items.map((it) => <li key={it.name}>
        {it.href ? <a href={link(it.href)}>{it.name}</a> : <span>{it.name}</span>}
        {it.cli && <span className="tag" title={it.cli}>仅命令行</span>}
        <div className="muted small">{it.desc}{it.cli && <> · <code>{it.cli}</code></>}</div>
      </li>)}</ul>
    </section>)}
  </div>;
}
