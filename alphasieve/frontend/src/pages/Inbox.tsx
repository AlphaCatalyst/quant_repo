import { useApi, type Json } from "../api";
import { Card, Loading, link } from "../components";

export function InboxSummary() {
  const { data, error } = useApi<Json>("/api/inbox", 30000);
  if (!data) return <Card title="待你决定"><Loading error={error} /></Card>;
  const open = data.decisions.filter((d: Json) => d.status === "open");
  return <Card title={`待你决定（${data.items.length + open.length}）`} extra={<a href={link("/inbox")}>查看全部</a>}>
    <div className="inbox-summary">
      {data.items.slice(0, 4).map((item: Json) => <a key={item.id} href={link("/inbox")}>{item.title} · {item.target}</a>)}
      {open.slice(0, 4).map((item: Json) => <a key={item.id} href={link(`/inbox?item=${item.id}`)}>{item.title}</a>)}
      {!data.items.length && !open.length && <span className="muted">暂无待决事项</span>}
    </div>
  </Card>;
}

export default function Inbox() {
  const { data, error } = useApi<Json>("/api/inbox", 30000);
  const { data: status } = useApi<Json>("/api/status", 30000);
  if (!data) return <Loading error={error} />;
  return <div className="page">
    <div className="page-head"><div><h2>待你决定</h2><p className="muted">这里汇总待办与设计问题。答复请通过与 agent 对话或 CLI 完成。</p></div></div>
    <Card title={`自动项（${data.items.length}）`}>
      <div className="inbox-list">{data.items.map((item: Json) => <article key={item.id} className="inbox-item">
        <strong>{item.title} · {item.target}</strong><div className="small muted">{item.id} · {item.detail?.content ?? (typeof item.detail === "string" ? item.detail : "")}</div>
        {item.signing?.map((s: Json, index: number) => <details key={index}><summary>生成待签内容 · {s.decision ?? index + 1}</summary>
          <p className="small">{s.note}</p><code>{s.challenge_command}</code>
          {s.content && <><p className="small">已生成、尚未使用的待签内容（到期 {s.expires_at}）：</p><pre>{s.content}</pre><code>{s.sign_command}</code></>}
          <p className="small">笔记本签名：<code>ssh-keygen -Y sign -f &lt;你的私钥&gt; -n alphasieve-approval &lt;待签文件&gt;</code></p></details>)}
      </article>)}{!data.items.length && <p className="muted">暂无自动待办</p>}</div>
    </Card>
    <Card title="近期人工决定"><div className="inbox-list">{data.history?.map((h: Json) => <div key={h.id} className="small">{h.decided_at} · {h.id} · {h.status} · {h.signature_status}</div>)}{!data.history?.length && <span className="muted">暂无记录</span>}</div></Card>
    <Card title="设计类待决问题"><div className="inbox-list">{data.decisions.map((d: Json) => <article id={d.id} key={d.id} className="inbox-item">
      <strong>{d.title}</strong><span className="small muted"> · {d.status === "open" ? "待决定" : `已决定 ${d.decided_at ?? ""}`}</span>
      <p>{d.question}</p><p><b>推荐：</b>{d.recommendation}</p>
      <ul>{d.options.map((o: Json) => <li key={o.name}><b>{o.name}：</b>{o.consequence}</li>)}</ul>
      <a href={link(`/docs/${d.evidence.split("/").pop()}`)} className="small">依据：{d.evidence}</a>
      {d.decision && <p>决定：{d.decision}</p>}
    </article>)}</div></Card>
    <Card title="审批签名公钥"><p className="small">{status?.approval_fingerprints?.length ? status.approval_fingerprints.join("、") : "尚未登记公钥"}</p></Card>
  </div>;
}
