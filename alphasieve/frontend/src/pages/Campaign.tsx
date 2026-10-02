import { useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import { getText, useApi, type Json } from "../api";
import { Badge, Card, Chart, duration, Empty, fmtNum, fmtPct, fmtTime, link, Loading, Progress } from "../components";
import { FailureList, failureLabel } from "./Overview";

const FUNNEL: [string, string][] = [
  ["submitted", "提交"],
  ["l0", "L0 结构"],
  ["l1", "L1 开发窗口"],
  ["l2", "L2 稳健性"],
  ["l3", "L3 搜索折扣"],
  ["holdout_passed", "holdout 通过"],
];

export default function Campaign({ id }: { id: string }) {
  const { data, error } = useApi<Json>(`/api/campaigns/${id}`, 30000);
  const [turnId, setTurnId] = useState<string | null>(null);
  const [report, setReport] = useState<string | null>(null);

  const funnelOption = useMemo<EChartsOption>(() => {
    if (!data) return {};
    const counts = data.funnel.counts;
    return {
      grid: { left: 110, right: 40, top: 10, bottom: 20 },
      xAxis: { type: "value", minInterval: 1 },
      yAxis: { type: "category", inverse: true, data: FUNNEL.map((f) => f[1]) },
      series: [{ type: "bar", data: FUNNEL.map((f) => counts[f[0]] ?? 0), label: { show: true, position: "right" },
                 itemStyle: { color: "#3b82f6" } }],
      tooltip: {},
    };
  }, [data]);

  const intensityOption = useMemo<EChartsOption>(() => {
    if (!data) return {};
    const pts = data.intensity as Json[];
    return {
      grid: { left: 50, right: 20, top: 30, bottom: 40 },
      legend: { data: ["当前最佳 ICIR", "零假设下的期望最大 ICIR"] },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", name: "trial 数", nameLocation: "middle", nameGap: 26, data: pts.map((p) => p.n) },
      yAxis: { type: "value", name: "ICIR" },
      series: [
        { name: "当前最佳 ICIR", type: "line", step: "end", data: pts.map((p) => p.best_icir), showSymbol: false },
        { name: "零假设下的期望最大 ICIR", type: "line", data: pts.map((p) => p.threshold), showSymbol: false,
          lineStyle: { type: "dashed" } },
      ],
    };
  }, [data]);

  if (!data) return <Loading error={error} />;
  const c = data.campaign;
  const b = data.budgets;
  const derived = data.memory.derived;

  return (
    <div className="page">
      <div className="subnav"><a href="#/">← 研究列表</a>
        <button onClick={() => document.getElementById("campaign-progress")?.scrollIntoView({ behavior: "smooth" })}>进展与失败</button>
        <button onClick={() => document.getElementById("campaign-trials")?.scrollIntoView({ behavior: "smooth" })}>轮次与评估</button>
        <button onClick={() => document.getElementById("campaign-holdout")?.scrollIntoView({ behavior: "smooth" })}>候选与留出集</button>
      </div>
      <div className="page-head">
        <div>
          <h2>{c.campaign_id} <Badge value={c.status} /></h2>
          <div className="muted">{c.title}</div>
          <p className="question">{c.spec.question}</p>
          <div className="muted small">
            股票池 {c.spec.universe} · 预测周期 {c.spec.horizon} 日 · 领域 {c.spec.domains.join(", ")} · agent{" "}
            {c.spec.agents.map((a: Json) => `${a.harness}/${a.model}`).join(" 与 ")}
          </div>
          {Object.entries((c.stats?.disabled_agents ?? {}) as Record<string, Json>).map(([h, e]) => {
            const why = typeof e === "string" ? e : e.reason ?? "";
            const until = typeof e === "object" && e.until ? new Date(e.until * 1000) : null;
            const active = until === null || until.getTime() > Date.now();
            return active ? (
              <div key={h} className="error small">
                {until ? `暂停 ${h} 至 ${until.toLocaleTimeString("zh-CN", { hour12: false })}` : `已停用 ${h}`}：{why.slice(0, 160)}
              </div>
            ) : null;
          })}
        </div>
        <button className="btn" onClick={() => getText(`/api/campaigns/${id}/report`).then(setReport).catch(() => setReport("还没有日报"))}>
          查看日报
        </button>
      </div>

      <div className="two-col" id="campaign-progress">
        <Card title="预算与停止条件">
          <Progress label="trial" used={b.trials.used} budget={b.trials.budget} />
          <Progress label="turn" used={b.turns.used} budget={b.turns.budget} />
          <Progress label="运行时长" used={b.hours.used} budget={b.hours.budget} unit="h" />
          <Progress label="连续无新 L2 通过的 turn" used={b.no_improvement_turns.current} budget={b.no_improvement_turns.limit} />
          <Progress label="连续失败 turn" used={b.failed_turns_streak.current} budget={b.failed_turns_streak.limit} />
          <div className="muted small">
            token：输入 {fmtNum(b.usage.input)}，输出 {fmtNum(b.usage.output)}；Claude 报告费用 ${fmtNum(b.usage.cost_usd, 2)}
          </div>
        </Card>
        <Card title="筛选漏斗（不同候选数）">
          <Chart option={funnelOption} height={230} />
        </Card>
      </div>

      <div className="two-col">
        <Card title="搜索强度：最佳 ICIR 与多重检验门槛">
          {data.intensity.length ? <Chart option={intensityOption} /> : <Empty />}
          <div className="muted small">
            虚线是在同样 trial 数、同样 ICIR 方差下纯噪声也能达到的最大 ICIR 的期望值（L3 的 DSR 门槛基准）。实线高出虚线越多，结果越可能不是搜索出来的偶然。
          </div>
        </Card>
        <Card title="失败原因">
          {Object.entries(data.funnel.failure_reasons).map(([lvl, reasons]) => (
            <div key={lvl}>
              <h4>{lvl.toUpperCase()}</h4>
              <FailureList reasons={reasons as Record<string, number>} level={lvl} />
            </div>
          ))}
        </Card>
      </div>

      <div id="campaign-trials"><Card title={`研究轮次（${data.turns.length}）`}>
        {data.turns.length === 0 ? <Empty /> : (
          <table className="table">
            <thead>
              <tr><th>#</th><th>agent</th><th>状态</th><th>trial</th><th>新 L2</th><th>token（输入/输出）</th><th>费用</th><th>耗时</th><th>开始</th><th>摘要</th></tr>
            </thead>
            <tbody>
              {[...data.turns].reverse().map((t: Json) => (
                <tr key={t.turn_id} className="clickable" onClick={() => setTurnId(t.turn_id)}>
                  <td>{t.turn_index}</td>
                  <td>{t.harness}/{t.model}</td>
                  <td><Badge value={t.status} /></td>
                  <td>{t.trials_before} → {t.trials_after ?? "…"}</td>
                  <td>{t.robust_passed_new ?? "—"}</td>
                  <td>{fmtNum(t.usage.input_tokens)} / {fmtNum(t.usage.output_tokens)}</td>
                  <td>{t.usage.cost_usd ? `$${fmtNum(t.usage.cost_usd, 2)}` : "—"}</td>
                  <td>{duration(t.started_at, t.ended_at)}</td>
                  <td>{fmtTime(t.started_at)}</td>
                  <td className="summary-cell">{(t.error || summaryLine(t.summary)).slice(0, 140)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card></div>

      <Card title="最近评估">
        <RecentTable rows={data.recent} />
      </Card>

      <div className="two-col">
        <Card title="研究记忆（仅开发阶段证据）">
          <h4>L2 通过的候选</h4>
          {derived.successes.length ? (
            <ul className="list">{derived.successes.map((s: Json, i: number) => (
              <li key={i}><code>{s.expression}</code> ICIR {fmtNum(s.icir)}，库相关 {fmtNum(s.library_max_abs_corr, 2)}</li>
            ))}</ul>
          ) : <Empty text="还没有" />}
          <h4>因与因子库相关过高被拒</h4>
          {derived.too_correlated.length ? (
            <ul className="list">{derived.too_correlated.slice(-8).map((s: Json, i: number) => (
              <li key={i}><code>{s.expression}</code> 与 {s.with} 相关 {fmtNum(s.corr, 2)}</li>
            ))}</ul>
          ) : <Empty text="没有" />}
          <h4>agent 总结的经验</h4>
          {data.memory.insights.length ? (
            <ul className="list">{data.memory.insights.slice(-4).map((s: Json, i: number) => (
              <li key={i}><span className="muted small">{s.turn}</span> {s.text}</li>
            ))}</ul>
          ) : <Empty text="还没有" />}
          {data.memory.insights.length > 4 && <details><summary>查看其余 {data.memory.insights.length - 4} 条经验</summary>
            <ul className="list">{data.memory.insights.slice(0, -4).map((s: Json, i: number) => <li key={i}><span className="muted small">{s.turn}</span> {s.text}</li>)}</ul>
          </details>}
        </Card>
        <Card title="搜索空间覆盖（trial / L1 通过 / L2 通过）">
          <CellTable cells={derived.cells} focus={c.spec.cells} dead={derived.dead_cells} />
        </Card>
      </div>

      <div className="two-col" id="campaign-holdout">
        <Card title="指令与研究请求">
          <h4>指令（用 CLI `alphasieve directive add` 添加）</h4>
          {data.directives.length ? (
            <ul className="list">{data.directives.map((d: Json) => (
              <li key={d.directive_id}><Badge value={d.status} /> [{d.kind}] {d.content}</li>
            ))}</ul>
          ) : <Empty text="没有" />}
          <h4>agent 请求（用 CLI `alphasieve request respond` 回复）</h4>
          {data.requests.length ? (
            <ul className="list">{data.requests.map((r: Json) => (
              <li key={r.request_id}>
                <Badge value={r.status} /> <b>{r.request_id}</b> [{r.kind}] {r.content}
                {r.response && <div className="muted small">回复：{r.response}</div>}
              </li>
            ))}</ul>
          ) : <Empty text="没有" />}
        </Card>
        <Card title="候选名单与留出集">
          <HoldoutSection data={data} />
        </Card>
      </div>

      {turnId && <TurnDrawer campaignId={id} turnId={turnId} onClose={() => setTurnId(null)} />}
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

function summaryLine(s: string | null): string {
  if (!s) return "";
  const m = s.match(/SUMMARY:\s*([\s\S]*?)(\n\s*INSIGHTS:|$)/);
  return (m ? m[1] : s).replace(/\s+/g, " ").trim();
}

export function RecentTable({ rows }: { rows: Json[] }) {
  if (!rows.length) return <Empty />;
  return (
    <table className="table">
      <thead><tr><th>因子</th><th>表达式</th><th>结果</th><th>IC</th><th>ICIR</th><th>库相关</th><th>未通过的检查</th></tr></thead>
      <tbody>
        {[...rows].reverse().map((r, i) => (
          <tr key={i}>
            <td><a href={link(`/factor/${r.factor.split("@")[0]}`)}>{r.name ?? r.factor}</a></td>
            <td><code>{r.expression}</code></td>
            <td><Badge value={r.outcome} /></td>
            <td>{fmtNum(r.ic_mean, 4)}</td>
            <td>{fmtNum(r.icir)}</td>
            <td>{fmtNum(r.library_max_abs_corr, 2)}</td>
            <td className="small" title={r.failed.join(", ")}>{r.failed.map((code: string) => failureLabel(code)).join("、")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CellTable({ cells, focus, dead }: { cells: Record<string, Json>; focus: Json[]; dead: string[] }) {
  const focusKeys = new Set(focus.map((f) => `${f.domain}/${f.form}/${f.scale}`));
  const keys = Array.from(new Set([...focusKeys, ...Object.keys(cells)])).sort();
  if (!keys.length) return <Empty />;
  return (
    <table className="table">
      <thead><tr><th>格子（领域/形式/尺度）</th><th>trial</th><th>L1</th><th>L2</th><th></th></tr></thead>
      <tbody>
        {keys.map((k) => {
          const v = cells[k] ?? { trials: 0, l1_passed: 0, robust_passed: 0 };
          return (
            <tr key={k}>
              <td>{k}{focusKeys.has(k) && <span className="tag">重点</span>}</td>
              <td>{v.trials}</td><td>{v.l1_passed}</td><td>{v.robust_passed}</td>
              <td>{dead.includes(k) && <span className="badge grey">死格子</span>}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function HoldoutSection({ data }: { data: Json }) {
  if (!data.shortlists.length) return <Empty text="campaign 结题后才会锁定 shortlist" />;
  return (
    <div>
      {data.shortlists.map((s: Json) => (
        <div key={s.shortlist_id}>
          <div className="muted small">
            {s.shortlist_id} 锁定于 {fmtTime(s.locked_at)} · campaign trial 数 {s.l3.discount?.n_trials} · 期望最大 ICIR{" "}
            {fmtNum(s.l3.discount?.sr0)}
          </div>
          <table className="table">
            <thead><tr><th>因子</th><th>ICIR</th><th>DSR</th><th>p</th><th>BH</th><th>入选</th></tr></thead>
            <tbody>
              {(s.l3.candidates ?? []).map((m: Json) => (
                <tr key={m.candidate_hash}>
                  <td><a href={link(`/factor/${m.factor_id}`)}>{m.factor_id}@{m.version}</a></td>
                  <td>{fmtNum(m.icir)}</td><td>{fmtNum(m.dsr)}</td><td>{fmtNum(m.p_value, 4)}</td>
                  <td>{m.bh?.passed ? "通过" : "未通过"}</td><td>{m.passed ? "是" : "否"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      {data.holdout_requests.map((h: Json) => (
        <div key={h.request_id} className="holdout-box">
          <b>{h.request_id}</b> <Badge value={h.status} /> 创建于 {fmtTime(h.created_at)}
          {h.status === "pending" && (
            <div className="muted small">
              批准命令（仅人工执行）：<code>alphasieve holdout approve {h.request_id} --reason "..."</code>
            </div>
          )}
          {h.result && (
            <table className="table">
              <thead><tr><th>因子</th><th>结果</th><th>dev ICIR</th><th>holdout IC</th><th>holdout ICIR</th><th>多空</th></tr></thead>
              <tbody>
                {h.result.results.map((r: Json) => (
                  <tr key={r.trial_id}>
                    <td>{r.name}</td><td><Badge value={r.outcome} /></td><td>{fmtNum(r.dev_icir)}</td>
                    <td>{fmtNum(r.holdout.ic_mean, 4)}</td><td>{fmtNum(r.holdout.icir)}</td><td>{fmtPct(r.holdout.long_short)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
    </div>
  );
}

function TurnDrawer({ campaignId, turnId, onClose }: { campaignId: string; turnId: string; onClose: () => void }) {
  const { data, error } = useApi<Json>(`/api/campaigns/${campaignId}/turns/${turnId}`);
  return (
    <div className="drawer" onClick={onClose}>
      <div className="drawer-body" onClick={(e) => e.stopPropagation()}>
        <button className="btn close" onClick={onClose}>关闭</button>
        {!data ? <Loading error={error} /> : (
          <>
            <h3>turn {data.turn_index} · {data.harness}/{data.model} <Badge value={data.status} /></h3>
            <div className="muted small">{fmtTime(data.started_at)} → {fmtTime(data.ended_at)} · trial {data.trials_before} → {data.trials_after}</div>
            {data.error && <div className="error">{data.error}</div>}
            <h4>agent 最终总结</h4>
            <pre className="pre">{data.summary || "—"}</pre>
            <h4>过程（{data.events.length} 条）</h4>
            {data.events.map((e: Json, i: number) => (
              <div key={i} className={`event ${e.kind}`}>
                {e.kind === "command" ? (
                  <details>
                    <summary><code>{e.text.slice(0, 220)}</code> {e.exit_code !== undefined && e.exit_code !== null && <span className={`exit ${e.exit_code ? "bad" : ""}`}>exit {e.exit_code}</span>}</summary>
                    {e.output && <pre className="pre small">{e.output}</pre>}
                  </details>
                ) : (
                  <div className="message">{e.text}</div>
                )}
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
