import { useApi, type Json } from "../api";
import { Badge, Card, Chart, Empty, fmtNum, fmtPct, fmtTime, link, Loading, Progress } from "../components";

const TITLES: Record<string, string> = {
  A: "中证 500 指数增强",
  B: "行业 ETF 轮动",
  C: "业绩超预期漂移（事件驱动）",
  D: "股指期货对冲（已降级为 A 的风险管理模块）",
};

const CHECK_LABEL: Record<string, string> = {
  annual_excess: "年化净超额",
  information_ratio: "信息比率",
  max_drawdown_excess: "超额最大回撤",
  positive_years: "正超额年份",
  capacity_drop_at_aum: "容量下降（无冲击 − 5 亿）",
  sharpe: "夏普",
  rank_ic: "RankIC",
  max_drawdown_vs_equal_weight: "最大回撤（对等权）",
  car_spread_t: "CAR 多空差 t 值",
  decile_monotonicity: "十分组单调性",
  annual_return: "年化收益",
  annual_vol: "年化波动",
  max_drawdown: "最大回撤",
  ex_2016_excess: "剔除 2016 净超额",
  ex_2016_ir: "剔除 2016 IR",
  excess_2021_2022: "2021–2022 净超额",
  excess_at_2e9: "20 亿净超额",
  ir_at_2e9: "20 亿 IR",
  no_impact_minus_2e9: "无冲击 − 20 亿",
  sub_periods_positive: "子段为正个数",
};

const RATIO_KEYS = ["information_ratio", "sharpe", "rank_ic", "car_spread_t", "decile_monotonicity", "ir"];

function fmtCheck(name: string, v: unknown): string {
  if (typeof v !== "number") return String(v ?? "—");
  if (RATIO_KEYS.some((k) => name.includes(k)) || name.endsWith("_positive")) return fmtNum(v, 3);
  return fmtPct(v, 2);
}

function headline(m: Json): { excess?: number; ratio?: number; ratioLabel: string } {
  if (!m) return { ratioLabel: "IR" };
  const ratio = m.information_ratio ?? m.sharpe;
  return { excess: m.annual_excess ?? m.annual_return, ratio, ratioLabel: m.information_ratio != null ? "IR" : "夏普" };
}

export function Mandates() {
  const { data, error } = useApi<Json>("/api/mandates", 30000);
  if (!data) return <Loading error={error} />;
  return (
    <div className="page">
      {data.mandates.map((m: Json) => (
        <Card key={m.mandate} title={`${m.mandate} · ${TITLES[m.mandate] ?? ""}`}>
          <div className="two-col">
            <Progress label="dev 策略 trial 预算" used={m.dev_trials} budget={m.budget} />
            <Progress label="holdout 读取（人工批准）" used={m.holdout_reads.used} budget={m.holdout_reads.budget} />
          </div>
          {m.trials.length === 0 ? <Empty /> : (
            <table className="table">
              <thead><tr><th>trial</th><th>配置</th><th>tier</th><th>结果</th><th>净超额</th><th>IR / 夏普</th><th>TE</th><th>超额回撤</th><th>折扣后</th><th>N</th><th>完成时间</th></tr></thead>
              <tbody>
                {m.trials.map((t: Json) => {
                  const h = headline(t.metrics);
                  const sd = t.metrics?.search_discount ?? {};
                  return (
                    <tr key={t.trial_id}>
                      <td className="small" style={{ whiteSpace: "nowrap" }}><a href={link(`/strategy/${t.trial_id}`)}>{t.trial_id}</a></td>
                      <td className="small">{t.task_id ?? "—"}</td>
                      <td><span className="tag">{t.tier ?? "—"}</span></td>
                      <td><Badge value={t.outcome ?? t.status} /></td>
                      <td>{fmtPct(h.excess, 2)}</td>
                      <td>{fmtNum(h.ratio, 2)}</td>
                      <td>{fmtPct(t.metrics?.tracking_error, 2)}</td>
                      <td>{fmtPct(t.metrics?.max_drawdown_excess, 1)}</td>
                      <td>{fmtNum(sd.deflated_ratio, 2)}</td>
                      <td>{sd.trials ?? "—"}</td>
                      <td className="small">{fmtTime(t.finished_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          <p className="muted small">折扣后 = 观测 IR / 夏普减去 N 次零假设试验的期望最大值；N 为该 trial 完成时 mandate 的累计 trial 数。</p>
          <HoldoutRequests rows={m.holdout_requests} />
        </Card>
      ))}
    </div>
  );
}

function HoldoutRequests({ rows }: { rows: Json[] }) {
  if (!rows.length) return <div className="muted small">没有 holdout 申请。申请与批准只走 CLI（train holdout-request / holdout-approve），且只能由人执行。</div>;
  return (
    <table className="table">
      <thead><tr><th>申请</th><th>trial</th><th>配置哈希</th><th>状态</th><th>申请人</th><th>决定人</th><th>理由</th><th>holdout 结果</th></tr></thead>
      <tbody>
        {rows.map((q: Json) => (
          <tr key={q.request_id}>
            <td className="small">{q.request_id}</td>
            <td className="small"><a href={link(`/strategy/${q.trial_id}`)}>{q.trial_id}</a></td>
            <td className="small mono">{q.config_hash}</td>
            <td><Badge value={q.status} /></td>
            <td>{q.created_by}</td><td>{q.decided_by ?? "—"}</td><td className="small">{q.reason ?? "—"}</td>
            <td className="small">{q.result ? <a href={link(`/strategy/${q.result.trial_id}`)}>{q.result.acceptance?.passed ? "通过" : "未通过"}</a> : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Checks({ checks }: { checks: Record<string, [unknown, unknown, boolean]> | undefined }) {
  const items = Object.entries(checks ?? {});
  if (!items.length) return <Empty />;
  return (
    <table className="table">
      <thead><tr><th>项</th><th>数值</th><th>门槛</th><th>通过</th></tr></thead>
      <tbody>
        {items.map(([k, [v, thr, ok]]) => (
          <tr key={k}>
            <td>{CHECK_LABEL[k] ?? k}</td><td>{fmtCheck(k, v)}</td><td>{fmtCheck(k, thr)}</td>
            <td><span className={`badge ${ok ? "green" : "red"}`}>{ok ? "通过" : "未通过"}</span></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function NavChart({ series }: { series: Json }) {
  if (!series?.month?.length) return <Empty />;
  const lines: [string, string][] = [["nav", "组合净值"], ["excess_nav", "超额净值"], ["hedged_nav", "对冲后净值"]];
  return (
    <Chart option={{
      tooltip: { trigger: "axis" },
      legend: { top: 0 },
      grid: { left: 50, right: 20, top: 30, bottom: 30 },
      xAxis: { type: "category", data: series.month },
      yAxis: { type: "value", scale: true },
      series: lines.filter(([k]) => series[k]).map(([k, name]) => ({ name, type: "line", showSymbol: false, data: series[k] })),
    }} />
  );
}

function YearBars({ byYear }: { byYear: Record<string, number> | undefined }) {
  const years = Object.keys(byYear ?? {});
  if (!years.length) return <Empty />;
  return (
    <Chart height={220} option={{
      tooltip: { trigger: "axis", valueFormatter: (v) => `${((v as number) * 100).toFixed(1)}%` },
      grid: { left: 50, right: 20, top: 20, bottom: 30 },
      xAxis: { type: "category", data: years },
      yAxis: { type: "value", axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%` } },
      series: [{ type: "bar", data: years.map((y) => ({ value: byYear![y], itemStyle: { color: byYear![y] >= 0 ? "#2f9e44" : "#e03131" } })) }],
    }} />
  );
}

function Capacity({ capacity }: { capacity: Record<string, Json> | undefined }) {
  const rows = Object.entries(capacity ?? {}).sort((a, b) => Number(a[0]) - Number(b[0]));
  if (!rows.length) return null;
  return (
    <Card title="容量（同一目标权重，按规模模拟成交）">
      <table className="table">
        <thead><tr><th>规模</th><th>净超额</th><th>IR</th><th>TE</th><th>超额回撤</th><th>总成本</th><th>冲击</th><th>年换手</th><th>截断交易占比</th></tr></thead>
        <tbody>
          {rows.map(([aum, c]) => (
            <tr key={aum}>
              <td>{`${Number(aum) / 1e8} 亿`}</td><td>{fmtPct(c.annual_excess, 2)}</td><td>{fmtNum(c.information_ratio, 2)}</td>
              <td>{fmtPct(c.tracking_error, 2)}</td><td>{fmtPct(c.max_drawdown_excess, 1)}</td><td>{fmtPct(c.annual_cost, 2)}</td>
              <td>{fmtPct(c.annual_impact_cost, 2)}</td><td>{fmtNum(c.annual_turnover, 2)}</td><td>{fmtPct(c.capped_trade_share, 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Robustness({ rob }: { rob: Json }) {
  if (!rob) return null;
  const periods = Object.entries(rob.periods ?? {}) as [string, Json][];
  return (
    <Card title={`稳健性筛选（docs/21 §4.3）：${rob.screens_passed ? "全部通过" : "未全部通过"}`}>
      <Checks checks={rob.screens} />
      {periods.length > 0 && (
        <table className="table">
          <thead><tr><th>区间</th><th>净超额</th><th>IR</th><th>TE</th><th>超额回撤</th><th>天数</th></tr></thead>
          <tbody>
            {periods.map(([k, p]) => (
              <tr key={k}>
                <td>{k === "all" ? "全部" : k === "ex_2016" ? "剔除 2016" : k}</td><td>{fmtPct(p.annual_excess, 2)}</td>
                <td>{fmtNum(p.information_ratio, 2)}</td><td>{fmtPct(p.tracking_error, 2)}</td>
                <td>{fmtPct(p.max_drawdown_excess, 1)}</td><td>{p.days}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

export function Strategy({ id }: { id: string }) {
  const { data, error } = useApi<Json>(`/api/strategy/${id}`);
  if (!data) return <Loading error={error} />;
  const d = data.detail;
  const result = data.records.find((r: Json) => r.record_kind === "completed" || r.record_kind === "failed");
  const started = data.records.find((r: Json) => r.record_kind === "started");
  const pf = d?.portfolio ?? {};
  const sd = result?.metrics?.search_discount ?? {};
  const scalars = Object.entries(pf.portfolio ?? {}).filter(([, v]) => typeof v === "number" || typeof v === "string");
  return (
    <div className="page">
      <Card title={`策略 trial ${id}`}>
        <table className="kv">
          <tbody>
            <tr><td>mandate</td><td>{started?.scope} · {TITLES[started?.scope] ?? ""}</td></tr>
            <tr><td>配置</td><td>{started?.metrics?.task_id} · 哈希 <span className="mono">{started?.candidate_hash}</span></td></tr>
            <tr><td>tier</td><td><span className="tag">{result?.evidence_tier ?? "未完成"}</span> {result?.data_window ?? ""}</td></tr>
            <tr><td>结果</td><td>{result ? <Badge value={result.outcome} /> : <Badge value="open" />} {result?.metrics?.error ?? ""}</td></tr>
            <tr><td>搜索折扣</td><td>N = {sd.trials ?? "—"}，零假设期望最大 {fmtNum(sd.null_expected_max_ratio, 3)}，折扣后 {fmtNum(sd.deflated_ratio, 3)}</td></tr>
            <tr><td>时间</td><td>{fmtTime(started?.created_at)} → {fmtTime(result?.created_at)}</td></tr>
            {d?.feature_summary?.score_source && (
              <tr><td>冻结分数来源</td><td>{d.feature_summary.score_source.task_id} / {d.feature_summary.score_source.trial_id}</td></tr>
            )}
          </tbody>
        </table>
      </Card>
      {!d ? <Empty text="没有结果 artifact" /> : (
        <>
          <Card title={`验收：${d.acceptance?.passed ? "通过" : "未通过"}${d.acceptance?.judged_on ? `（${d.acceptance.judged_on}）` : ""}`}>
            <Checks checks={d.acceptance?.checks} />
          </Card>
          <div className="two-col">
            <Card title="月末净值"><NavChart series={d.series} /></Card>
            <Card title="逐年超额"><YearBars byYear={pf.execution?.excess_by_year} /></Card>
          </div>
          <Capacity capacity={pf.capacity} />
          <Robustness rob={pf.robustness} />
          {scalars.length > 0 && (
            <Card title="组合诊断（目标权重）">
              <table className="kv"><tbody>
                {scalars.map(([k, v]) => <tr key={k}><td>{k}</td><td>{typeof v === "number" ? fmtNum(v, 4) : String(v)}</td></tr>)}
              </tbody></table>
            </Card>
          )}
          <Card title="holdout 申请"><HoldoutRequests rows={data.holdout_requests} /></Card>
          <Card title="锁定配置">
            <details><summary>展开 TrainingTask</summary><pre className="pre">{JSON.stringify(d.task, null, 2)}</pre></details>
          </Card>
        </>
      )}
    </div>
  );
}
