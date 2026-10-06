import { useState } from "react";
import { useApi, type Json } from "../api";

type ResourceResponse = { snapshot: Json | null; history: Json[]; age_s: number | null; stale: boolean; ray_r2: Record<string, boolean> };
const show = (value: unknown) => value == null ? "—" : String(value);
const number = (value: unknown, digits = 1) => typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
const gib = (value: unknown) => typeof value === "number" ? `${number(value / 2 ** 30)} GiB` : "—";
const metric = (used: unknown, total: unknown, unit = "") => `${number(used)} / ${number(total)}${unit}`;
const entries = (value: Json) => Object.entries(value ?? {}) as [string, Json][];
const age = (value: string | null | undefined) => value ? `${Math.max(0, Math.round((Date.now() - Date.parse(value)) / 60000))} 分钟` : "—";
const reachable = (value: Json) => value?.reachable === true ? "可达" : value?.reachable === false ? "不可达" : "暂无数据";
const healthLabel: Record<string, string> = { ok: "正常", warn: "关注", fail: "故障" };
const jobLabel: Record<string, string> = { queued: "排队", submitted: "已提交", running: "运行中",
  retry_wait: "等待重试", paused: "暂停", succeeded: "成功", failed: "失败", cancelled: "已取消" };

function Spark({ values, binary = false }: { values: number[]; binary?: boolean }) {
  if (!values.length) return <span className="muted">暂无历史</span>;
  const lo = binary ? 0 : Math.min(...values), hi = binary ? 1 : Math.max(...values);
  const points = values.map((v, i) => `${i * 180 / Math.max(values.length - 1, 1)},${33 - (v - lo) * 29 / Math.max(hi - lo, 0.01)}`).join(" ");
  return <svg className="control-spark" viewBox="0 0 180 36" role="img" aria-label="近 24 小时趋势"><polyline points={points} fill="none" stroke="currentColor" strokeWidth="2" /></svg>;
}
function Series({ rows, get, binary = false }: { rows: Json[]; get: (row: Json) => unknown; binary?: boolean }) {
  return <Spark binary={binary} values={rows.map(get).filter((v): v is number => typeof v === "number" && Number.isFinite(v))} />;
}
function Load({ target }: { target: Json }) {
  const load = target?.loadavg?.[0], cores = target?.cpu_count;
  const ratio = typeof load === "number" && typeof cores === "number" && cores > 0 ? Math.min(load / cores, 1) : 0;
  return <><p>负载 / 核数：<strong>{metric(load, cores)}</strong></p><div className="control-meter"><span style={{ width: `${ratio * 100}%` }} /></div></>;
}
function Memory({ value }: { value: Json }) {
  if (!value) return <p>内存：—</p>;
  if (value.MemTotal != null) return <p>内存：{gib(value.MemTotal - (value.MemAvailable ?? 0))} / {gib(value.MemTotal)}</p>;
  return <p>可用内存：{gib(value.mem_available_bytes)}</p>;
}
function GroupBar({ groups }: { groups: Json }) {
  const order = ["alphasieve", "scicomp-foundry", "codex/claude", "docker", "other"];
  const names: Record<string, string> = { alphasieve: "alphasieve", "scicomp-foundry": "scicomp-foundry", "codex/claude": "agents", docker: "docker", other: "other" };
  const values = order.map(k => Number(groups?.[k]?.cpu_percent ?? 0));
  const sum = values.reduce((a, b) => a + b, 0);
  return <div><p>CPU 按进程组（% 单核）</p><div className="control-stack">{order.map((k, i) => <span key={k} className={`group-${i}`} style={{ width: `${sum ? values[i] / sum * 100 : 0}%` }} title={`${names[k]} ${number(values[i])}%`} />)}</div><div className="control-legend">{order.map((k, i) => <span key={k}><i className={`group-${i}`} />{names[k]} {number(values[i])}%</span>)}</div></div>;
}
export function ControlStrip() {
  const health = useApi<Json>("/api/control/health", 30000);
  const resources = useApi<ResourceResponse>("/api/control/resources", 30000);
  const jobs = useApi<Json>("/api/control/jobs?open=true", 30000);
  const llm = useApi<Json>("/api/control/llm", 30000);
  const local = resources.data?.snapshot?.local;
  const open = jobs.data?.jobs?.length;
  return <div className="control-strip">
    <a href="#/health">系统健康 <strong>{healthLabel[health.data?.overall] ?? "—"}</strong></a>
    <a href="#/resources">本机负载 <strong>{local ? metric(local.loadavg?.[0], local.cpu_count) : "—"}</strong></a>
    <a href="#/jobs">未结束作业 <strong>{show(open)}</strong></a>
    <a href="#/resources">codex-lb <strong>{llm.data?.available == null ? "—" : llm.data.available ? "可用" : "不可用"}</strong></a>
  </div>;
}
export function Resources() {
  const { data, error } = useApi<ResourceResponse>("/api/control/resources", 30000);
  const llm = useApi<Json>("/api/control/llm", 30000).data;
  const s = data?.snapshot, history = data?.history ?? [];
  const local = s?.local, ssh = s?.ssh_hosts?.orbenchtest;
  return <div className="page"><header className="page-head"><div><h2>计算资源</h2><p>最近快照：{s?.at ?? "暂无"} · 距今 {data?.age_s == null ? "—" : `${Math.round(data.age_s)} 秒`}</p></div></header>
    {error && <p className="control-warning">资源读取失败：{error}</p>}
    {data?.stale && <p className="control-warning">资源快照超过 5 分钟或尚未生成；以下状态可能过期。</p>}
    <div className="control-grid"><section className="card"><h3>本机</h3>{local ? <><Load target={local} /><Memory value={local.memory} /><p>磁盘：{entries(local.disks).map(([k, v]) => `${k} ${gib(v.used_bytes)} / ${gib(v.total_bytes)}`).join(" · ") || "—"}</p><GroupBar groups={local.groups} /></> : <p>暂无快照</p>}
      <small>24 小时负载</small><Series rows={history} get={r => r.local?.loadavg?.[0]} /></section>
    <section className="card"><h3>orbenchtest</h3><p>连通：{reachable(ssh)}</p><Load target={ssh} /><Memory value={ssh} /><p>挂载：{entries(ssh?.mounts).map(([k, v]) => `${k} ${v ? "正常" : "缺失"}`).join(" · ") || "—"}</p><p>westock-data：{ssh?.westock_data == null ? "—" : ssh.westock_data ? "可用" : "不可用"}</p><small>24 小时负载</small><Series rows={history} get={r => r.ssh_hosts?.orbenchtest?.loadavg?.[0]} /></section>
    {entries(s?.ray_clusters).map(([name, ray]) => <section className="card" key={name}><h3>Ray · {name}</h3><p>连通：{reachable(ray)} · 活节点 {show(ray?.nodes_alive)}</p><p>CPU：{metric(ray?.resources_used?.CPU, ray?.resources_total?.CPU)}</p><p>GPU：{metric(ray?.resources_used?.GPU, ray?.resources_total?.GPU)}</p><p>内存：{ray?.resources_used?.memory == null ? "—" : gib(ray.resources_used.memory)} / {ray?.resources_total?.memory == null ? "—" : gib(ray.resources_total.memory)}</p><p>本系统作业：{entries(ray?.jobs).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}</p><p>r2：{data?.ray_r2?.[name] == null ? "—" : data.ray_r2[name] ? "是" : "否"}</p><small>24 小时 CPU 使用</small><Series rows={history} get={r => r.ray_clusters?.[name]?.resources_used?.CPU} /></section>)}
    <section className="card"><h3>codex-lb</h3><p>连通：{reachable(s?.llm_endpoints?.["codex-lb"])}</p><p>延迟：{s?.llm_endpoints?.["codex-lb"]?.latency_ms == null ? "—" : `${number(s.llm_endpoints["codex-lb"].latency_ms)} ms`}</p><p>暂停始于：{show(llm?.paused_since)}</p><small>24 小时连通性</small><Series rows={history} binary get={r => r.llm_endpoints?.["codex-lb"]?.reachable == null ? null : Number(r.llm_endpoints["codex-lb"].reachable)} /></section></div>
    <section className="card"><h3>算力放置</h3><p>本机：调度、记账、状态库与看板；包含 holdout / fresh 的数据留在本机。</p><p>Ray：dev 训练、策略回测、因子评估及风险报告，输入限 2022-12-31 及以前。</p><p>orbenchtest：全量测试与 westock-data 请求中转，供应商响应只经内存；codex-lb：模型调用入口。</p></section>
  </div>;
}
export function Health() {
  const { data, error } = useApi<Json>("/api/control/health", 30000);
  return <div className="page"><header className="page-head"><div><h2>系统健康</h2><p>总体状态：<span className={`badge ${data?.overall === "ok" ? "green" : data?.overall === "fail" ? "red" : "amber"}`}>{healthLabel[data?.overall] ?? "—"}</span></p></div><a href="#/alerts?kind=system">查看系统告警 →</a></header>
    {error && <p>读取失败：{error}</p>}{!data?.checks?.length && <section className="card">暂无检查结果</section>}
    <div className="control-grid">{(data?.checks ?? []).map((c: Json, i: number) => <section className="card" key={c.id ?? i}><h3>{c.title ?? c.id} <span className={`badge ${c.status === "ok" ? "green" : c.status === "fail" ? "red" : "amber"}`}>{healthLabel[c.status] ?? "—"}</span></h3><p>{show(c.detail)}</p><small>始于：{show(c.since)}</small></section>)}</div>
  </div>;
}
const finished = new Set(["succeeded", "failed", "cancelled"]);
function Command({ command }: { command: string }) { return <code className="control-command" title="选中即可复制">{command}</code>; }
export function Jobs() {
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<Json | null>(null);
  const { data, error } = useApi<Json>(`/api/control/jobs${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`, 30000);
  const schedule = useApi<Json>("/api/control/schedule", 30000);
  const jobs: Json[] = data?.jobs ?? [];
  const shown = jobs.filter(j => !status || j.status === status);
  const table = (rows: Json[]) => rows.length ? <div className="table-scroll"><table className="table"><thead><tr>{["作业", "种类", "状态", "放置 / 目标", "尝试", "失败分类", "年龄", "错误摘要"].map(x => <th key={x}>{x}</th>)}</tr></thead><tbody>{rows.map(j => <tr className="clickable" key={j.job_id} onClick={() => setSelected(j)}><td><button className="control-link">{j.job_id}</button></td><td>{j.kind}</td><td>{jobLabel[j.status] ?? j.status}</td><td>{j.placement} / {show(j.target)}</td><td>{show(j.attempt)} / {show(j.max_attempts)}</td><td>{show(j.failure_class)}</td><td>{age(j.submitted_at)}</td><td className="control-error">{show(j.error)?.slice(0, 100)}</td></tr>)}</tbody></table></div> : <p className="muted">暂无作业</p>;
  return <div className="page"><header className="page-head"><div><h2>作业与调度</h2><p>所有操作通过 CLI 执行；点击作业查看尝试记录与命令。</p></div></header>
    {error && <p>作业读取失败：{error}</p>}
    <section className="card"><div className="control-filters"><label>状态 <select value={status} onChange={e => setStatus(e.target.value)}><option value="">全部</option>{Array.from(new Set(jobs.map(j => j.status))).map(v => <option key={v} value={v}>{jobLabel[v] ?? v}</option>)}</select></label><label>种类 <select value={kind} onChange={e => setKind(e.target.value)}><option value="">全部</option>{Array.from(new Set(jobs.map(j => j.kind))).map(v => <option key={v}>{v}</option>)}</select></label></div><h3>未结束作业</h3>{table(shown.filter(j => !finished.has(j.status)))}</section>
    <section className="card"><h3>最近结束作业</h3>{table(shown.filter(j => finished.has(j.status)).slice(0, 30))}</section>
    <section className="card"><h3>调度计划</h3>{schedule.error && <p>调度读取失败：{schedule.error}</p>}<div className="table-scroll"><table className="table"><thead><tr>{["名称", "规格", "启用", "上次运行 / 状态", "下次到期"].map(x => <th key={x}>{x}</th>)}</tr></thead><tbody>{(Array.isArray(schedule.data) ? schedule.data : schedule.data?.schedule ?? []).map((x: Json, i: number) => <tr key={x.name ?? i}><td>{x.name}</td><td>{show(x.spec)}</td><td>{x.enabled ? "是" : "否"}</td><td>{show(x.last_run)} / {show(x.last_status)}</td><td>{show(x.next_due)}</td></tr>)}</tbody></table></div></section>
    <section className="card"><h3>发布</h3><p>发布与回滚仅通过 CLI 脚本执行：</p><p><Command command="deploy/release.sh <commit>" /></p><p><Command command="deploy/release.sh --rollback" /></p></section>
    {selected && <div className="control-drawer-backdrop" onClick={() => setSelected(null)}><aside className="control-drawer" role="dialog" aria-label="作业详情" onClick={e => e.stopPropagation()}><button className="control-close" onClick={() => setSelected(null)}>关闭</button><h2>作业 {selected.job_id}</h2><p>{selected.kind} · {jobLabel[selected.status] ?? selected.status} · {selected.placement} / {show(selected.target)}</p><p>尝试 {show(selected.attempt)} / {show(selected.max_attempts)} · 失败分类 {show(selected.failure_class)}</p><p>提交：{show(selected.submitted_at)}<br />心跳：{show(selected.heartbeat_at)}<br />结束：{show(selected.finished_at)}</p><h3>尝试记录</h3>{Array.isArray(selected.attempts) && selected.attempts.length ? <ol>{selected.attempts.map((a: Json, i: number) => <li key={i}>{show(a.attempt)} · {show(a.status)} · {show(a.error)}</li>)}</ol> : <p>当前接口仅提供最近尝试次数；逐次记录暂无数据。</p>}<p className="control-error">{show(selected.error)}</p><h3>CLI 命令</h3><p><Command command={`alphasieve jobs resume ${selected.job_id}`} /></p><p><Command command={`alphasieve jobs cancel ${selected.job_id}`} /></p></aside></div>}
  </div>;
}
