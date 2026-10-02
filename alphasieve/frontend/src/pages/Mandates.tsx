import { useEffect, useState } from "react";
import { useApi, type Json } from "../api";
import { Badge, Card, Chart, DataTable, Empty, fmtNum, fmtPct, fmtTime, link, Loading, Progress } from "../components";

const TITLES: Record<string, string> = {
  A: "中证 500 指数增强",
  B: "行业 ETF 轮动",
  C: "业绩超预期漂移（事件驱动）",
  D: "股指期货对冲（已降级为 A 的风险管理模块）",
};

export const CHECK_LABEL: Record<string, string> = {
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

export function fmtCheck(name: string, v: unknown): string {
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
  const [filters, setFilters] = useState(() => new URLSearchParams(window.location.hash.split("?")[1] ?? ""));
  const [selected, setSelected] = useState<string[]>([]);
  useEffect(() => {
    const update = () => setFilters(new URLSearchParams(window.location.hash.split("?")[1] ?? ""));
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(filters);
    if (value) next.set(key, value); else next.delete(key);
    window.location.hash = `/mandates${next.size ? `?${next}` : ""}`;
    setFilters(next);
  };
  if (!data) return <Loading error={error} />;
  const selectedMandate = filters.get("mandate") ?? "";
  const selectedResult = filters.get("result") ?? "";
  const view = ["matrix", "list", "holdout"].includes(filters.get("view") ?? "") ? filters.get("view") : "matrix";
  const visible = data.mandates.filter((m: Json) => !selectedMandate || m.mandate === selectedMandate);
  const toggle = (id: string) => setSelected((ids) => ids.includes(id) ? ids.filter((x) => x !== id) : [...ids.slice(-1), id]);
  return (
    <div className="page">
      <div className="filters">
        <label>范围 <select value={selectedMandate} onChange={(e) => setFilter("mandate", e.target.value)}>
          <option value="">全部（{data.mandates.length}）</option>
          {data.mandates.map((m: Json) => <option key={m.mandate} value={m.mandate}>{m.mandate} · {TITLES[m.mandate]}</option>)}
        </select></label>
        <label>结果 <select value={selectedResult} onChange={(e) => setFilter("result", e.target.value)}>
          <option value="">全部</option><option value="passed">验收通过</option><option value="failed">验收未通过</option><option value="open">未有验收</option>
        </select></label>
      </div>
      <div className="subnav" aria-label="策略视图">
        {[["matrix", "验收矩阵"], ["list", "指标列表"], ["holdout", "holdout 申请"]].map(([key, label]) =>
          <button key={key} aria-current={view === key ? "page" : undefined} style={{ fontWeight: view === key ? 700 : 400 }} onClick={() => setFilter("view", key)}>{label}</button>)}
        {view !== "holdout" && <span className="muted small" style={{ marginLeft: "auto" }}>
          {selected.length ? `已选 ${selected.length}/2` : "勾选两个 trial 可对比"}
          {selected.length === 2 && <> · <a href={link(`/compare?a=${encodeURIComponent(selected[0])}&b=${encodeURIComponent(selected[1])}`)}>并排对比 →</a></>}
        </span>}
      </div>
      {visible.map((m: Json) => (
        <Card key={m.mandate} title={`${m.mandate} · ${TITLES[m.mandate] ?? ""}`}>
          <div className="two-col">
            <Progress label="dev 策略 trial 预算" used={m.dev_trials} budget={m.budget} />
            <Progress label="holdout 读取（人工批准）" used={m.holdout_reads.used} budget={m.holdout_reads.budget} />
          </div>
          {view === "matrix" && <TrialMatrix trials={m.trials} result={selectedResult} selected={selected} toggle={toggle} />}
          {view === "list" && (m.trials.length === 0 ? <Empty /> : <DataTable rows={m.trials.filter((t: Json) => matchesResult(t, selectedResult))}
            filename={`mandate-${m.mandate}-trials.csv`} searchPlaceholder="搜索 trial、配置"
            filters={[{ label: "证据", value: (t: Json) => t.tier ?? "", options: [{ value: "dev", label: "开发" }, { value: "holdout", label: "留出集" }] }]}
            columns={[
              { key: "select", label: "对比", value: (t: Json) => t.trial_id, sortable: false, render: (t: Json) => <input type="checkbox" aria-label={`选择 ${t.trial_id} 对比`} checked={selected.includes(t.trial_id)} onChange={() => toggle(t.trial_id)} /> },
              { key: "id", label: "trial", value: (t: Json) => t.trial_id, render: (t: Json) => <a className="mono nowrap" href={link(`/strategy/${t.trial_id}`)}>{t.trial_id}</a> },
              { key: "config", label: "配置", value: (t: Json) => t.config_label ?? t.task_id, render: (t: Json) => <span title={t.task_description ?? t.task_id}>{t.config_label ?? t.task_id ?? "—"}</span> },
              { key: "tier", label: "证据", value: (t: Json) => t.tier, render: (t: Json) => <span className="tag">{t.tier ?? "—"}</span> },
              { key: "outcome", label: "结果", value: (t: Json) => t.outcome ?? t.status, render: (t: Json) => <Badge value={t.outcome ?? t.status} /> },
              { key: "excess", label: "净超额", value: (t: Json) => t.metrics?.annual_excess ?? t.metrics?.annual_return, render: (t: Json) => fmtPct(headline(t.metrics).excess, 2) },
              { key: "ratio", label: "IR / 夏普", value: (t: Json) => t.metrics?.information_ratio ?? t.metrics?.sharpe, render: (t: Json) => fmtNum(headline(t.metrics).ratio, 2) },
              { key: "te", label: "TE", value: (t: Json) => t.metrics?.tracking_error, render: (t: Json) => fmtPct(t.metrics?.tracking_error, 2) },
              { key: "mdd", label: "超额回撤", value: (t: Json) => t.metrics?.max_drawdown_excess, render: (t: Json) => fmtPct(t.metrics?.max_drawdown_excess, 1) },
              { key: "discount", label: "折扣后", value: (t: Json) => t.metrics?.search_discount?.deflated_ratio, render: (t: Json) => fmtNum(t.metrics?.search_discount?.deflated_ratio, 2) },
              { key: "n", label: "N", value: (t: Json) => t.metrics?.search_discount?.trials },
              { key: "time", label: "完成时间", value: (t: Json) => t.finished_at, render: (t: Json) => fmtTime(t.finished_at) },
            ]} />)}
          {view === "matrix" && <p className="muted small">颜色来自逐项验收判定。门槛按原始验收口径显示。</p>}
          {view === "list" && <p className="muted small">折扣后 = 观测 IR / 夏普减去 N 次零假设试验的期望最大值；N 为该 trial 完成时 mandate 的累计 trial 数。</p>}
          {view === "holdout" && <HoldoutRequests rows={m.holdout_requests} />}
        </Card>
      ))}
    </div>
  );
}

function matchesResult(t: Json, result: string) {
  if (!result) return true;
  if (result === "open") return t.acceptance?.passed == null;
  return t.acceptance?.passed === (result === "passed");
}

export function thresholdText(name: string, value: unknown, threshold: unknown): string {
  if (name === "positive_years" && typeof value === "string" && typeof threshold === "number") {
    const denominator = Number(value.split("/")[1]);
    return denominator > 0 ? `${Math.ceil(threshold * denominator - 1e-9)}/${denominator}` : `${fmtPct(threshold, 0)}（年份占比）`;
  }
  if (name === "sub_periods_positive" && typeof value === "string") return `${threshold}/${value.split("/")[1]}`;
  return fmtCheck(name, threshold);
}

export function checkDirection(name: string): string {
  return ["capacity_drop_at_aum", "annual_vol", "no_impact_minus_2e9"].includes(name) ? "≤" : "≥";
}

function TrialMatrix({ trials, result, selected, toggle }: { trials: Json[]; result: string; selected: string[]; toggle: (id: string) => void }) {
  const rows = trials.filter((t: Json) => matchesResult(t, result));
  const metrics = Array.from(new Set(rows.flatMap((t: Json) => Object.keys(t.acceptance?.checks ?? {})))) as string[];
  if (!rows.length) return <Empty text="没有符合筛选条件的 trial" />;
  if (!metrics.length) return <p className="muted small">尚无逐项验收数据。</p>;
  const best = rows.filter((t: Json) => t.tier === "dev" && typeof t.metrics?.search_discount?.deflated_ratio === "number")
    .sort((a: Json, b: Json) => b.metrics.search_discount.deflated_ratio - a.metrics.search_discount.deflated_ratio)[0]?.trial_id;
  return <div className="table-scroll matrix-scroll">
    <h4>trial × 验收指标</h4>
    <table className="table compact">
      <thead><tr><th>对比</th><th>trial</th><th>配置</th><th>证据</th><th>总验收</th>{metrics.map((k) => {
        const checks = rows.map((t: Json) => t.acceptance?.checks?.[k]).filter(Boolean);
        const threshold = checks[0]?.[1];
        const value = checks[0]?.[0];
        return <th key={k} title={k}>{CHECK_LABEL[k] ?? k}<span className="threshold">{threshold != null ? `${checkDirection(k)} ${thresholdText(k, value, threshold)}` : ""}</span></th>;
      })}</tr></thead>
      <tbody>{rows.map((t: Json) => <tr key={t.trial_id} className={t.trial_id === best ? "best-row" : ""}>
        <td><input type="checkbox" aria-label={`选择 ${t.trial_id} 对比`} checked={selected.includes(t.trial_id)} onChange={() => toggle(t.trial_id)} /></td>
        <td className="small mono nowrap"><a href={link(`/strategy/${t.trial_id}`)}>{t.trial_id}</a>{t.trial_id === best && <span className="tag">折扣后最佳</span>}</td>
        <td className="small nowrap" title={t.task_description ?? t.task_id}>{t.config_label ?? t.task_id ?? "—"}</td>
        <td className="small">{t.tier === "holdout" ? "留出集" : t.tier === "dev" ? "开发" : "—"}</td>
        <td><span className={`badge ${t.acceptance?.passed == null ? "grey" : t.acceptance.passed ? "green" : "red"}`}>{t.acceptance?.passed == null ? "—" : t.acceptance.passed ? "通过" : "未通过"}</span></td>
        {metrics.map((k) => {
          const check = t.acceptance?.checks?.[k];
          const value = check?.[0];
          const threshold = check?.[1];
          const passed = check?.[2];
          return <td key={k} title={check ? `门槛 ${checkDirection(k)} ${thresholdText(k, value, threshold)}` : "无数据"}>
            <span className={`badge ${passed == null ? "grey" : passed ? "green" : "red"}`}>{check ? fmtCheck(k, value) : "—"}</span>
          </td>;
        })}
      </tr>)}</tbody>
    </table>
  </div>;
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
            <td>{CHECK_LABEL[k] ?? k}</td><td>{fmtCheck(k, v)}</td><td>{checkDirection(k)} {thresholdText(k, v, thr)}</td>
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
  const peers = useApi<Json>("/api/mandates").data?.mandates ?? [];
  if (!data) return <Loading error={error} />;
  const d = data.detail;
  const result = data.records.find((r: Json) => r.record_kind === "completed" || r.record_kind === "failed");
  const started = data.records.find((r: Json) => r.record_kind === "started");
  const pf = d?.portfolio ?? {};
  const sd = result?.metrics?.search_discount ?? {};
  const mine = peers.find((m: Json) => m.mandate === started?.scope)?.trials ?? [];
  const ranked = mine.filter((t: Json) => t.tier === "dev" && typeof t.metrics?.search_discount?.deflated_ratio === "number")
    .sort((a: Json, b: Json) => b.metrics.search_discount.deflated_ratio - a.metrics.search_discount.deflated_ratio);
  const rank = ranked.findIndex((t: Json) => t.trial_id === id);
  const scalars = Object.entries(pf.portfolio ?? {}).filter(([, v]) => typeof v === "number" || typeof v === "string");
  return (
    <div className="page">
      <div className="subnav"><a href="#/mandates">← 策略列表</a>
        {d && ["验收", "净值", "容量", "稳健性"].map((label) => <button key={label} onClick={() => document.getElementById(`strategy-${label}`)?.scrollIntoView({ behavior: "smooth" })}>{label}</button>)}
      </div>
      <Card title={`策略 trial ${id}`}>
        <table className="kv">
          <tbody>
            <tr><td>mandate</td><td>{started?.scope} · {TITLES[started?.scope] ?? ""}</td></tr>
            <tr><td>配置</td><td><span title={data.task_description ?? undefined}>{started?.metrics?.task_id}</span>{data.task_description && <div className="muted small" style={{ maxWidth: 720 }}>{data.task_description}</div>}哈希 <span className="mono">{started?.candidate_hash}</span></td></tr>
            <tr><td>tier</td><td><span className="tag">{result?.evidence_tier ?? "未完成"}</span> {result?.data_window ?? ""}</td></tr>
            <tr><td>结果</td><td>{result ? <Badge value={result.outcome} /> : <Badge value="open" />} {result?.metrics?.error ?? ""}</td></tr>
            <tr><td>搜索折扣</td><td>N = {sd.trials ?? "—"}，零假设期望最大 {fmtNum(sd.null_expected_max_ratio, 3)}，折扣后 {fmtNum(sd.deflated_ratio, 3)}</td></tr>
            {rank >= 0 && <tr><td>同策略对比</td><td>折扣后第 {rank + 1} / {ranked.length} 名 · 同 mandate 的已完成 dev trial</td></tr>}
            <tr><td>时间</td><td>{fmtTime(started?.created_at)} → {fmtTime(result?.created_at)}</td></tr>
            {d?.feature_summary?.score_source && (
              <tr><td>冻结分数来源</td><td>{d.feature_summary.score_source.task_id} / {d.feature_summary.score_source.trial_id}</td></tr>
            )}
          </tbody>
        </table>
      </Card>
      {!d ? <Empty text="没有结果 artifact" /> : (
        <>
          <div id="strategy-验收"><Card title={`验收：${d.acceptance?.passed ? "通过" : "未通过"}${d.acceptance?.judged_on ? `（${d.acceptance.judged_on}）` : ""}`}>
            <Checks checks={d.acceptance?.checks} />
          </Card></div>
          <div id="strategy-净值" className="two-col">
            <Card title="月末净值"><NavChart series={d.series} /></Card>
            <Card title="逐年超额"><YearBars byYear={pf.execution?.excess_by_year} /></Card>
          </div>
          <div id="strategy-容量"><Capacity capacity={pf.capacity} /></div>
          <div id="strategy-稳健性"><Robustness rob={pf.robustness} /></div>
          {scalars.length > 0 && (
            <Card title="组合诊断（目标权重）">
              <table className="kv"><tbody>
                {scalars.map(([k, v]) => <tr key={k}><td>{k}</td><td>{typeof v === "number" ? fmtNum(v, 4) : String(v)}</td></tr>)}
              </tbody></table>
            </Card>
          )}
          <Card title="holdout 申请"><HoldoutRequests rows={data.holdout_requests} /></Card>
          <Card title="锁定配置">
            <details><summary>展开配置详情</summary><pre className="pre">{JSON.stringify(d.task, null, 2)}</pre></details>
          </Card>
        </>
      )}
    </div>
  );
}
