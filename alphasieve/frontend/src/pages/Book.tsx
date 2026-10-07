import { useState } from "react";
import { useApi, type BookAttributionReport, type BookAttributionResponse, type BookBehaviorReport, type BookBehaviorResponse, type BookHistoryReport, type BookHistoryResponse, type BookRebalanceReport, type BookRebalanceResponse, type BookReport, type BookSnapshot } from "../api";
import { Card, Chart, Empty, fmtNum, fmtPct, link, Loading } from "../components";

const ANNOUNCEMENT_TYPE: Record<string, string> = {
  shareholding_change: "股东增减持", repurchase: "回购", earnings: "业绩", earnings_forecast: "业绩预告",
  dividend: "分红", litigation: "诉讼", regulatory: "监管", major_contract: "重大合同", restructuring: "重组",
};
const signed = (v: number | null | undefined, digits = 2) => v == null ? "—" : `${v > 0 ? "+" : ""}${fmtNum(v, digits)}`;
const signedPct = (v: number | null | undefined, digits = 2) => v == null ? "—" : `${v > 0 ? "+" : ""}${fmtPct(v, digits)}`;
const pnlClass = (v: number | null | undefined) => v == null || v === 0 ? "" : v > 0 ? "pnl-up" : "pnl-down";

function positionRows(snapshot: BookSnapshot) {
  return snapshot.positions.map((p) => {
    const cash = p.code === "CASH";
    const cost = !cash && p.cost_price != null ? p.cost_price * p.quantity : null;
    const pnl = cost != null ? p.market_value - cost : null;
    return { ...p, cash, cost, pnl, pnlPct: cost ? pnl! / cost : null, weight: snapshot.total_value ? p.market_value / snapshot.total_value : null };
  }).sort((a, b) => Number(a.cash) - Number(b.cash) || b.market_value - a.market_value);
}

function pnlTotals(rows: ReturnType<typeof positionRows>) {
  const priced = rows.filter((r) => r.cost != null);
  const cost = priced.reduce((s, r) => s + r.cost!, 0);
  const pnl = priced.reduce((s, r) => s + r.pnl!, 0);
  return { cost, pnl, pnlPct: cost ? pnl / cost : null };
}

export function latestByAccount(snapshots: BookSnapshot[]): BookSnapshot[] {
  const latest = new Map<string, BookSnapshot>();
  for (const s of snapshots) {
    const cur = latest.get(s.account);
    if (!cur || s.as_of > cur.as_of || (s.as_of === cur.as_of && (s.created_at ?? "") > (cur.created_at ?? ""))) latest.set(s.account, s);
  }
  return [...latest.values()].sort((a, b) => b.total_value - a.total_value);
}

function PositionsTable({ snapshot, compact = false }: { snapshot: BookSnapshot; compact?: boolean }) {
  const rows = positionRows(snapshot);
  const totals = pnlTotals(rows);
  return <div className="table-scroll"><table className={`table ${compact ? "compact" : ""}`}>
    <thead><tr><th>持仓</th><th className="num">数量</th><th className="num">成本价</th><th className="num">现价</th><th className="num">市值（元）</th><th className="num">占比</th><th className="num">浮动盈亏（元）</th><th className="num">盈亏比例</th></tr></thead>
    <tbody>
      {rows.map((r) => <tr key={r.code}>
        <td>{r.name || r.code}{!r.cash && <div className="muted small mono">{r.code}</div>}</td>
        <td className="num">{r.cash ? "—" : fmtNum(r.quantity, 0)}</td>
        <td className="num">{r.cash ? "—" : value(r.cost_price, 3)}</td>
        <td className="num">{r.cash ? "—" : value(r.price, 2)}</td>
        <td className="num">{value(r.market_value)}</td>
        <td className="num">{fmtPct(r.weight, 1)}</td>
        <td className={`num ${pnlClass(r.pnl)}`}>{signed(r.pnl)}</td>
        <td className={`num ${pnlClass(r.pnl)}`}>{signedPct(r.pnlPct)}</td>
      </tr>)}
      <tr className="total-row"><td>合计</td><td /><td /><td /><td className="num">{value(snapshot.total_value)}</td><td className="num">100%</td>
        <td className={`num ${pnlClass(totals.pnl)}`}>{signed(totals.pnl)}</td><td className={`num ${pnlClass(totals.pnl)}`}>{signedPct(totals.pnlPct)}</td></tr>
    </tbody>
  </table></div>;
}

function RiskNotes({ snapshot }: { snapshot: BookSnapshot }) {
  const report = snapshot.report;
  if (!report) return <p className="small muted">这份快照还没有体检报告；在本机运行 <code>alphasieve book check {snapshot.snapshot_id}</code> 生成。</p>;
  const names = Object.fromEntries(snapshot.positions.map((p) => [p.code, p.name || p.code]));
  const notes: string[] = [];
  const top = Object.entries(report.weights ?? {}).sort((a, b) => b[1] - a[1])[0];
  if (top && top[1] > 0.3) notes.push(`单一持仓集中：${names[top[0]] ?? top[0]} 占 ${fmtPct(top[1], 1)}`);
  if (report.portfolio_beta_60d != null && report.portfolio_beta_60d > 1.2) notes.push(`波动高于大盘：60 日 Beta ${fmtNum(report.portfolio_beta_60d, 2)}（相对 ${report.benchmark}）`);
  const flagged = report.red_flags?.holdings ?? [];
  if (flagged.length) notes.push(`财务风险标记 ${flagged.length} 只：${flagged.map((h) => h.name || h.code).join("、")}`);
  const announcements = report.announcements?.items ?? [];
  return <>
    <div className="stats-row">
      <div className="stat"><div className="stat-value">{fmtNum(report.portfolio_beta_60d, 2)}</div><div className="stat-label">60 日 Beta</div></div>
      <div className="stat"><div className="stat-value">{signedPct(report.stress_returns?.benchmark_down_10pct, 1)}</div><div className="stat-label">大盘跌 10% 时估算</div></div>
      <div className="stat"><div className="stat-value">{signedPct(report.stress_returns?.largest_position_down_30pct, 1)}</div><div className="stat-label">最大持仓跌 30% 时估算</div></div>
      <div className="stat"><div className="stat-value">{flagged.length}</div><div className="stat-label">财务风险标记</div></div>
    </div>
    {notes.length > 0 && <ul className="risk-notes">{notes.map((n) => <li key={n}>{n}</li>)}</ul>}
    {announcements.length > 0 && <>
      <h4>近期公告（{report.announcements?.since} 至 {report.announcements?.as_of}）</h4>
      <ul className="announcements">{announcements.slice(0, 6).map((a) => <li key={`${a.code}-${a.date}-${a.title}`}>
        <span className="muted small nowrap">{a.date}</span> <span className="tag">{ANNOUNCEMENT_TYPE[a.type] ?? a.type}</span> <b>{names[a.code] ?? a.code}</b>{" "}
        {a.url ? <a href={a.url} target="_blank" rel="noreferrer">{a.title}</a> : a.title}
      </li>)}</ul>
    </>}
  </>;
}

function AccountSummary({ snapshot, compact = false }: { snapshot: BookSnapshot; compact?: boolean }) {
  const totals = pnlTotals(positionRows(snapshot));
  return <div className="account-summary">
    <div className="account-head">
      <div><strong>{snapshot.account}</strong> <span className="muted small">截至 {snapshot.as_of} · {snapshot.positions.filter((p) => p.code !== "CASH").length} 只持仓</span></div>
      <div className="account-figures">
        <span><span className="muted small">总市值</span> <b>{value(snapshot.total_value)}</b></span>
        <span><span className="muted small">浮动盈亏</span> <b className={pnlClass(totals.pnl)}>{signed(totals.pnl)}（{signedPct(totals.pnlPct)}）</b></span>
      </div>
    </div>
    <PositionsTable snapshot={snapshot} compact={compact} />
    <RiskNotes snapshot={snapshot} />
  </div>;
}

export function BookSummary() {
  const { data, error } = useApi<{ snapshots: BookSnapshot[] }>("/api/book", 60000);
  const extra = <a className="small" href={link("/book")}>完整体检 →</a>;
  if (!data) return <Card title="我的持仓" extra={extra}><Loading error={error} /></Card>;
  const accounts = latestByAccount(data.snapshots.filter((s) => s.source !== "virtual"));
  return <Card title="我的持仓" extra={extra}>
    {accounts.length ? accounts.map((s) => <AccountSummary key={s.snapshot_id} snapshot={s} compact />)
      : <Empty text="还没有导入持仓；在本机运行 alphasieve book import。" />}
  </Card>;
}

const ASSET_CLASS: Record<string, string> = { stock: "股票", etf: "ETF", convertible_bond: "可转债", cash: "现金", other: "其他" };
const BOND_FLAG: Record<string, string> = {
  high_conversion_premium: "转股溢价率超过 50%",
  small_remaining_size: "剩余规模低于 3 亿元",
  short_maturity: "剩余期限不足 1 年",
  low_rating: "评级低于 AA-",
};
const value = (v: number | null | undefined, digits = 2) => v == null ? "—" : fmtNum(v, digits);
const percentPoints = (v: number | null | undefined, digits = 1) => v == null ? "—" : `${fmtNum(v, digits)}%`;
const capYi = (v: number | null | undefined) => v == null ? "—" : `${fmtNum(v / 1e8, 1)} 亿`;
const labelClass = (name: string) => ASSET_CLASS[name] ?? name;
const reportMissing = "暂无该区间的已保存报告；请在本机运行相应的 book 命令生成报告。";

function History({ report }: { report: BookHistoryReport | null }) {
  if (!report?.rows?.length) return <Card title="净值与收益"><Empty text={reportMissing} /></Card>;
  const daily = report.rows;
  const summary = report.summary;
  let benchmarkNav = 1;
  const benchmarkSeries = daily.map((row) => {
    if (row.benchmark_return != null) benchmarkNav *= 1 + row.benchmark_return;
    return row.benchmark_return == null && row.date !== daily[0].date ? null : benchmarkNav;
  });
  const hasBenchmark = daily.some((row) => row.benchmark_return != null);
  const inferredDates = new Set(report.trades.filter((trade) => trade.inferred).map((trade) => trade.date));
  return <Card title="净值与收益" extra={<span className="small muted">基准：{report.benchmark.code} · {report.benchmark.source}</span>}>
    <div className="stats-row">
      <div className="stat"><div className="stat-value">{fmtPct(summary.total_return)}</div><div className="stat-label">区间收益</div></div>
      <div className="stat"><div className="stat-value">{fmtPct(summary.benchmark_return == null ? null : summary.total_return - summary.benchmark_return)}</div><div className="stat-label">超额收益</div></div>
      <div className="stat"><div className="stat-value">{fmtPct(summary.max_drawdown)}</div><div className="stat-label">最大回撤</div></div>
      <div className="stat"><div className="stat-value">{fmtPct(summary.volatility_annualized)}</div><div className="stat-label">年化波动率</div></div>
      <div className="stat"><div className="stat-value">{fmtPct(summary.turnover)}</div><div className="stat-label">区间换手率</div></div>
    </div>
    <Chart option={{ tooltip: { trigger: "axis" }, legend: { data: hasBenchmark ? ["组合净值", "基准净值"] : ["组合净值"] },
      grid: { left: 55, right: 20, top: 35, bottom: 35 }, xAxis: { type: "category", data: daily.map((row) => row.date) },
      yAxis: { type: "value", scale: true }, series: [
        { name: "组合净值", type: "line", data: daily.map((row) => row.unit_nav), showSymbol: false },
        ...(hasBenchmark ? [{ name: "基准净值", type: "line" as const, data: benchmarkSeries, showSymbol: false }] : []),
      ] }} />
    <div className="table-scroll"><table className="table"><thead><tr><th>日期</th><th>净值</th><th>日收益</th><th>基准日收益</th><th>超额收益</th><th>回撤</th><th>外部现金流（元）</th><th>记录</th></tr></thead><tbody>
      {daily.map((row) => <tr key={row.date}><td>{row.date}</td><td>{value(row.unit_nav, 4)}</td><td>{fmtPct(row.return)}</td><td>{fmtPct(row.benchmark_return)}</td><td>{fmtPct(row.excess_return)}</td><td>{fmtPct(row.drawdown)}</td><td>{value(row.cash_flow)}</td><td>{inferredDates.has(row.date) ? "数量变动（推断交易）" : row.missing_prices?.length ? `缺报价：${row.missing_prices.join("、")}` : "—"}</td></tr>)}
    </tbody></table></div>
    <p className="small muted">两个导入日期之间按持仓数量不变估算；数量变化记在后一个快照日。未登记的外部现金流可能影响收益率。</p>
  </Card>;
}

function Attribution({ report, by }: { report: BookAttributionReport | null; by: string }) {
  const rows = report?.rows ?? [];
  const brinson = by === "industry";
  return <Card title="收益归因" extra={<span className="small muted">{report?.start && report?.end ? `${report.start} 至 ${report.end}` : ""}</span>}>
    {rows.length ? <div className="table-scroll"><table className="table"><thead><tr><th>{by === "position" ? "持仓" : by === "industry" ? "申万一级行业" : "投资论点"}</th>{brinson && <><th>期初组合权重</th><th>基准权重</th><th>配置效应</th><th>选择效应</th></>}<th>收益贡献</th></tr></thead><tbody>
      {rows.map((row) => <tr key={row.name}><td>{row.name === "core" ? "未关联论点（核心持仓）" : row.name}</td>{brinson && <><td>{fmtPct(row.portfolio_weight_start)}</td><td>{fmtPct(row.benchmark_weight)}</td><td>{fmtPct(row.allocation)}</td><td>{fmtPct(row.selection)}</td></>}<td>{fmtPct(row.contribution)}</td></tr>)}
    </tbody></table></div> : <Empty text={reportMissing} />}
    {report && <p className="small muted">组合收益 {fmtPct(report.summary.portfolio_return)}；持仓贡献合计 {fmtPct(report.summary.position_contribution_sum)}；未分解差额 {fmtPct(report.summary.residual)}。</p>}
    {report?.industry_meta && brinson && <p className="small muted">行业：{report.industry_meta.source}（截至 {report.industry_meta.as_of}）{report.industry_meta.fallback_codes.length ? `；${report.industry_meta.fallback_codes.length} 个代码使用当前行业快照` : ""}。基准行业权重：{report.benchmark_industry_meta.snapshot_date ?? "不可用"}，覆盖状态 {report.benchmark_industry_meta.status}。</p>}
    {brinson && <p className="small muted">配置与选择效应按申万一级行业计算；行业和基准权重以报告标注的数据日期为准。</p>}
  </Card>;
}

function Rebalance({ report }: { report: BookRebalanceReport | null }) {
  const rows = report?.rows ?? [];
  return <Card title="持仓偏离提示" extra={<span className="small muted">{report?.as_of ?? ""}</span>}>
    {rows.length ? <div className="table-scroll"><table className="table"><thead><tr><th>持仓</th><th>行业</th><th>提示</th><th>当前权重</th><th>适用上限</th><th>目标权重</th><th>参考减持金额（元）</th><th>参考增加金额（元）</th><th>原因</th></tr></thead><tbody>
      {rows.map((row) => <tr key={row.code}><td>{row.name}（{row.code}）</td><td>{row.industry}</td><td>{row.action === "trim" ? "超出约束，考虑减持" : row.action === "add" ? "低于已登记目标，考虑增加" : "未超出约束"}</td><td>{fmtPct(row.weight)}</td><td>{fmtPct(row.cap)}</td><td>{fmtPct(row.target_weight)}</td><td>{value(row.suggested_trim_value)}</td><td>{value(row.suggested_add_value)}</td><td>{row.reasons.join("；") || "—"}</td></tr>)}
    </tbody></table></div> : <Empty text={report ? "暂无超过已配置上限的偏离" : "暂无已保存的调仓提示；请在本机运行 book rebalance。"} />}
    {report && <p className="small muted">超出约束 {report.summary.flagged} 项，参考减持金额合计 {value(report.summary.suggested_trim_total)} 元，参考增加金额合计 {value(rows.reduce((sum, row) => sum + row.suggested_add_value, 0))} 元。单一证券上限 {fmtPct(report.limits.single_name_max)}，行业上限 {fmtPct(report.limits.industry_max)}。</p>}
    <p className="small muted">仅按已登记的持仓与约束计算差额，供人工复核；不会生成或执行交易指令。</p>
  </Card>;
}

const VERDICT: Record<string, string> = { positive: "换仓整体有正价值", negative: "换仓整体有负价值", inconclusive: "无法区分于零" };
const SOURCE: Record<string, string> = { broker_export: "券商导出", derived_trades: "交易推算", virtual: "虚拟账户" };
function Behavior({ report, names }: { report: BookBehaviorReport; names: Record<string, string> }) {
  const sw = report.switch_value_h;
  const label = (code: string) => names[code] || code;
  return <Card title="交易行为" extra={<span className="small muted">{report.trades} 条记录 · 买 {report.buys} 卖 {report.sells} · 观察期 {report.horizon_days} 个交易日</span>}>
    <div className="stats-row">
      <div className="stat"><div className="stat-value">{sw.mean == null ? "—" : fmtPct(sw.mean, 1)}</div><div className="stat-label">换仓的事后价值（买入减卖出，{sw.n} 次）</div></div>
      <div className="stat"><div className="stat-value">{report.repurchase_within_h.share == null ? "—" : fmtPct(report.repurchase_within_h.share, 0)}</div><div className="stat-label">卖出后 {report.horizon_days} 日内买回</div></div>
      <div className="stat"><div className="stat-value">{report.chasing.mean_prior_excess_h == null ? "—" : fmtPct(report.chasing.mean_prior_excess_h, 1)}</div><div className="stat-label">买入前 {report.horizon_days} 日超额（正值为追涨）</div></div>
      <div className="stat"><div className="stat-value">{report.disposition.pgr_minus_plr == null ? "—" : fmtNum(report.disposition.pgr_minus_plr, 2)}</div><div className="stat-label">处置效应 PGR − PLR</div></div>
      <div className="stat"><div className="stat-value">{report.costs.fee_rate == null ? "—" : fmtPct(report.costs.fee_rate, 3)}</div><div className="stat-label">费用占成交额</div></div>
    </div>
    {!!sw.decisions.length && <div className="table-scroll"><table className="table"><thead><tr><th>日期</th><th>卖出</th><th>买入</th><th>{report.horizon_days} 日后差值</th></tr></thead><tbody>
      {sw.decisions.map((d) => <tr key={d.date + d.sold}><td>{d.date}</td><td>{label(d.sold)}</td><td>{d.bought.map(label).join("、")}</td><td className={`num ${pnlClass(d.value)}`}>{signedPct(d.value)}</td></tr>)}
    </tbody></table></div>}
    <p className="small muted">{sw.verdict ? `结论：${VERDICT[sw.verdict] ?? sw.verdict}。` : `决策数不足 30，只报告数字，不判断有无判断力。`}{sw.bootstrap_ci95 ? ` 均值 95% 区间 ${fmtPct(sw.bootstrap_ci95[0], 1)} 至 ${fmtPct(sw.bootstrap_ci95[1], 1)}。` : ""}</p>
  </Card>;
}

function Weights({ title, weights }: { title: string; weights: Record<string, number> }) {
  const rows = Object.entries(weights).sort((a, b) => b[1] - a[1]);
  return <Card title={title}>{rows.length ? <div className="bars">{rows.map(([name, value]) => <div className="bar-row" key={name}><span className="bar-label" title={name}>{name}</span><span className="bar-track"><span className="bar-fill" style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }} /></span><span className="bar-value">{fmtPct(value, 1)}</span></div>)}</div> : <Empty text="暂无权重" />}</Card>;
}
function Report({ report }: { report: BookReport }) {
  const stress = report.stress_returns;
  const industry = Object.entries(stress?.industry_down_20pct ?? {});
  return <>
    <div className="stats-row"><div className="stat"><div className="stat-value">{fmtPct(report.top_n_concentration, 1)}</div><div className="stat-label">前几大持仓集中度</div></div>
      <div className="stat"><div className="stat-value">{fmtNum(report.hhi, 3)}</div><div className="stat-label">持仓集中度 HHI</div></div>
      <div className="stat"><div className="stat-value">{fmtNum(report.portfolio_beta_60d, 2)}</div><div className="stat-label">60 日组合 Beta · 基准 {report.benchmark}</div></div></div>
    <div className="two-col"><Weights title="持仓权重" weights={report.weights ?? {}} /><Weights title="资产类别" weights={Object.fromEntries(Object.entries(report.asset_class_weights ?? {}).map(([name, weight]) => [labelClass(name), weight]))} /></div>
    <div className="two-col"><Weights title="行业权重" weights={report.industry_weights ?? {}} /><Weights title="股票流通市值分组" weights={report.market_cap_buckets ?? {}} /></div>
    <p className="small muted">行业采用当前快照，并非持仓日的历史行业；优先使用申万一级，缺失时回退证监会行业。市值分组仅适用于股票。</p>
    {!!report.holdings?.length && <Card title="持仓明细" extra={<span className="small muted">报价为当日快照；持仓市值来自导入文件</span>}><div className="table-scroll"><table className="table"><thead><tr><th>代码</th><th>名称</th><th>类别</th><th>权重</th><th>持仓市值（元）</th><th>行业</th><th>市值分组</th><th>现价（元）</th><th>报价时间</th><th>市盈率</th><th>市净率</th><th>流通市值</th><th>总市值</th><th>52 周位置</th></tr></thead><tbody>
      {report.holdings.map((position) => <tr key={position.code}><td>{position.code}</td><td>{position.name || "—"}</td><td>{labelClass(position.asset_class)}</td><td>{fmtPct(position.weight, 1)}</td><td>{value(position.market_value ?? position.weight * report.total_value)}</td><td>{position.industry || "—"}</td><td>{position.market_cap_bucket || "—"}</td><td>{value(position.quote?.price)}</td><td>{position.quote?.time || "—"}</td><td>{value(position.quote?.pe_ratio)}</td><td>{value(position.quote?.pb_ratio)}</td><td>{capYi(position.quote?.circulating_market_cap)}</td><td>{capYi(position.quote?.total_market_cap)}</td><td>{position.quote?.position_52week == null ? "—" : fmtPct(position.quote.position_52week, 1)}</td></tr>)}
    </tbody></table></div></Card>}
    {!!report.convertible_bonds?.length && <Card title="可转债指标" extra={<span className="small muted">触发价距离为正值表示正股现价高于触发价</span>}><div className="table-scroll"><table className="table"><thead><tr><th>代码</th><th>名称</th><th>转股溢价率</th><th>纯债溢价率</th><th>双低值</th><th>到期收益率</th><th>评级</th><th>剩余规模（亿元）</th><th>剩余期限（年）</th><th>强赎触发价距离</th><th>回售触发价距离</th><th>客观标记</th></tr></thead><tbody>
      {report.convertible_bonds.map((bond) => <tr key={bond.code}><td>{bond.code}</td><td>{bond.name || report.holdings?.find((item) => item.code === bond.code)?.name || "—"}</td><td>{percentPoints(bond.conversion_premium_pct)}</td><td>{percentPoints(bond.pure_bond_premium_pct)}</td><td>{value(bond.double_low)}</td><td>{percentPoints(bond.ytm_pct)}</td><td>{bond.rating || "—"}</td><td>{value(bond.remaining_size_yi)}</td><td>{value(bond.remaining_term_years)}</td><td>{percentPoints(bond.redeem_distance_pct)}</td><td>{percentPoints(bond.buyback_distance_pct)}</td><td>{bond.flags?.length ? bond.flags.map((flag) => BOND_FLAG[flag] ?? flag).join("；") : "—"}</td></tr>)}
    </tbody></table></div></Card>}
    <Card title="压力情景" extra={<span className="small muted">估算组合收益率</span>}><div className="table-scroll"><table className="table"><thead><tr><th>情景</th><th>估算影响</th></tr></thead><tbody>
      <tr><td>基准下跌 10%</td><td>{fmtPct(stress?.benchmark_down_10pct)}</td></tr>
      <tr><td>最大持仓下跌 30%</td><td>{fmtPct(stress?.largest_position_down_30pct)}</td></tr>
      {industry.map(([name, value]) => <tr key={name}><td>{name}行业下跌 20%</td><td>{fmtPct(value)}</td></tr>)}
    </tbody></table></div><p className="small muted">{report.stress_note ?? "线性冲击估算，仅供体检参考。"}</p></Card>
  </>;
}
export default function Book() {
  const { data, error } = useApi<{ snapshots: BookSnapshot[] }>("/api/book", 30000);
  const [selected, setSelected] = useState<string | null>(null);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [by, setBy] = useState("position");
  const rows = data?.snapshots ?? [];
  const dates = [...new Set(rows.map((row) => row.as_of))].sort();
  const effectiveStart = start || dates[0] || "";
  const effectiveEnd = end || dates[dates.length - 1] || "";
  const canCompare = !!data && !!effectiveStart && !!effectiveEnd && effectiveStart <= effectiveEnd;
  const query = canCompare ? `start=${encodeURIComponent(effectiveStart)}&end=${encodeURIComponent(effectiveEnd)}` : "";
  const { data: history, error: historyError } = useApi<BookHistoryResponse>(data ? `/api/book/history${query ? `?${query}` : ""}` : null);
  const { data: attribution, error: attributionError } = useApi<BookAttributionResponse>(canCompare ? `/api/book/attribution?${query}&by=${by}` : null);
  const { data: rebalance, error: rebalanceError } = useApi<BookRebalanceResponse>(data ? "/api/book/rebalance" : null);
  const { data: behavior } = useApi<BookBehaviorResponse>(data ? "/api/book/behavior" : null);
  if (!data) return <Loading error={error} />;
  const real = rows.filter((row) => row.source !== "virtual");
  const virtualAccounts = latestByAccount(rows.filter((row) => row.source === "virtual"));
  const active = rows.find((row) => row.snapshot_id === selected) ?? latestByAccount(real)[0];
  return <div className="page book-page"><div className="page-head"><div><h2>我的持仓</h2><p className="muted small">先看当前持仓和盈亏，再看风险体检；下方是历史净值、收益归因和偏离提示。数据只保存在本机，不进 git。</p></div></div>
    {active && <Card title={`持仓与盈亏 · ${active.account}`} extra={<span className="small muted">成本价、现价来自券商导出（{active.as_of}）</span>}>
      <AccountSummary snapshot={active} />
    </Card>}
    <Card title={`持仓快照（${rows.length}）`}>{rows.length ? <div className="table-scroll"><table className="table"><thead><tr><th>账户</th><th>截至日期</th><th>来源</th><th>持仓数</th><th>总市值</th><th>体检报告</th></tr></thead><tbody>
      {rows.map((row) => <tr key={row.snapshot_id} className={active?.snapshot_id === row.snapshot_id ? "best-row" : ""}><td><button className="table-sort" onClick={() => setSelected(row.snapshot_id)}>{row.account}</button></td><td>{row.as_of}</td><td>{SOURCE[row.source ?? ""] ?? row.source ?? "—"}</td><td>{row.positions_count}</td><td>{fmtNum(row.total_value, 2)}</td><td>{row.report ? "已保存" : "暂无"}</td></tr>)}
    </tbody></table></div> : <Empty text="暂无已导入持仓" />}</Card>
    {!!virtualAccounts.length && <Card title={`虚拟账户（${virtualAccounts.length}）`} extra={<span className="small muted">按通过 dev 验收的规则从真实持仓复制后前瞻记账，不是实际交易</span>}><div className="table-scroll"><table className="table"><thead><tr><th>账户</th><th>截至日期</th><th>持仓数</th><th>总市值</th></tr></thead><tbody>
      {virtualAccounts.map((row) => <tr key={row.snapshot_id}><td><button className="table-sort" onClick={() => setSelected(row.snapshot_id)}>{row.account}</button></td><td>{row.as_of}</td><td>{row.positions_count}</td><td>{fmtNum(row.total_value, 2)}</td></tr>)}
    </tbody></table></div></Card>}
    {active?.report && <><h3 className="section-title">风险体检 · {active.account} · {active.as_of}</h3><Report report={active.report} /></>}
    <Card title="历史区间"><div className="filters">
      <label>开始日期 <input aria-label="开始日期" type="date" value={start || effectiveStart} onChange={(event) => setStart(event.target.value)} /></label>
      <label>结束日期 <input aria-label="结束日期" type="date" value={end || effectiveEnd} onChange={(event) => setEnd(event.target.value)} /></label>
      <label>归因维度 <select aria-label="归因维度" value={by} onChange={(event) => setBy(event.target.value)}><option value="position">持仓</option><option value="industry">行业</option><option value="thesis">投资论点</option></select></label>
    </div>{!canCompare && <p className="small muted">至少需要一个已导入日期才能查看历史；归因需选择有效区间。</p>}</Card>
    {historyError ? <Card title="净值与收益"><Loading error={historyError} /></Card> : history ? <History report={history.report} /> : canCompare && <Card title="净值与收益"><Loading /></Card>}
    {attributionError ? <Card title="收益归因"><Loading error={attributionError} /></Card> : attribution ? <Attribution report={attribution.report} by={by} /> : canCompare && <Card title="收益归因"><Loading /></Card>}
    {behavior?.report && <Behavior report={behavior.report} names={Object.fromEntries(rows.flatMap((row) => row.positions.map((p) => [p.code, p.name ?? ""])))} />}
    {rebalanceError ? <Card title="持仓偏离提示"><Loading error={rebalanceError} /></Card> : rebalance ? <Rebalance report={rebalance.report} /> : <Card title="持仓偏离提示"><Loading /></Card>}
  </div>;
}
