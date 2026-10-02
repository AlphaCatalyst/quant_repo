import { useState } from "react";
import { useApi, type Json } from "../api";
import { Card, Loading, link } from "../components";

export default function Progress() {
  const { data, error } = useApi<Json>("/api/progress", 30000);
  const [days, setDays] = useState("7");
  if (!data) return <Card title="研究进展"><Loading error={error} /></Card>;
  return <Card title="研究进展" extra={<span className="progress-switch"><button onClick={() => setDays("7")} aria-pressed={days === "7"}>近 7 天</button><button onClick={() => setDays("30")} aria-pressed={days === "30"}>近 30 天</button></span>}>
    <div className="progress-grid">
      <section><h4>试验进展</h4>{data.windows[days].length ? data.windows[days].map((g: Json) => <p key={`${g.layer}-${g.name}`} className="small"><b>{g.layer === "strategy" ? `策略 ${g.name}` : `因子 ${g.name}`}</b>：{g.trials} 次，{g.passed} 通过、{g.failed} 失败{Object.keys(g.failure_reasons).length ? `；主要失败：${Object.entries(g.failure_reasons).sort((a: any, b: any) => b[1] - a[1])[0][0]}` : ""}</p>) : <p className="muted">最近没有 dev trial</p>}</section>
      <section><h4>策略预算与最佳 dev</h4>{data.mandates.map((m: Json) => <p className="small" key={m.mandate}><b>{m.mandate}</b>：剩余 {m.remaining} / {m.budget}；{m.best_dev ? <a href={link(`/strategy/${m.best_dev.trial_id}`)}>最佳 {m.best_dev.trial_id} · IR/夏普 {m.best_dev.value.toFixed(2)}</a> : "暂无 dev 结果"}</p>)}</section>
      <section><h4>最新采纳决定</h4>{data.latest_decisions.map((d: Json) => <p className="small" key={d.id}>{d.title}</p>)}</section>
      <section><h4>下一步待决定</h4>{data.next_steps.slice(0, 5).map((d: Json) => <p className="small" key={d.id}><a href={link(`/inbox?item=${d.id}`)}>{d.title}</a></p>)}</section>
    </div>
  </Card>;
}
