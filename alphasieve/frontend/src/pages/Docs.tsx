import { useEffect, useState, type ReactNode } from "react";
import { getText, useApi, type Json } from "../api";
import { Card, DataTable, link, Loading } from "../components";

const FOLDER: Record<string, string> = {
  overview: "总览与决策", data: "数据", research: "研究核心与回测", agent: "Agent", interfaces: "接口与看板",
  mandates: "策略任务", personal: "个人账户", acceptance: "验收记录",
};

export default function Docs({ name, anchor }: { name?: string; anchor?: string }) {
  return name ? <DocView file={name} anchor={anchor} /> : <DocIndex />;
}

function DocIndex() {
  const { data, error } = useApi<Json>("/api/docs");
  if (!data) return <Loading error={error} />;
  const docs = data.docs as Json[];
  return <div className="page">
    <div className="page-head"><div>
      <h2>文档</h2>
      <p className="muted question">仓库 alphasieve/docs 下的实现文档，按层分目录；每篇开头写了当前状态和核对日期。
        另见 <a href={link("/docs/README.md")}>文档地图</a> 与 <a href={link("/docs/TODO.md")}>未关闭事项</a>。</p>
    </div></div>
    <Card>
      <DataTable rows={docs} filename="docs.csv" searchPlaceholder="搜索标题、状态或内容提要"
        filters={[{ label: "分层", value: (d: Json) => d.folder, options: Object.keys(FOLDER).map((k) => ({ value: k, label: FOLDER[k] })) }]}
        columns={[
          { key: "folder", label: "分层", value: (d: Json) => FOLDER[d.folder] ?? d.folder },
          { key: "title", label: "文档", value: (d: Json) => d.title, render: (d: Json) => <><a href={link(`/docs/${d.name}`)}>{d.title}</a><div className="muted small">{d.name}</div></> },
          { key: "summary", label: "回答的问题", value: (d: Json) => d.summary, render: (d: Json) => <span className="small summary-cell">{d.summary ?? "—"}</span> },
          { key: "status", label: "状态", value: (d: Json) => d.status, render: (d: Json) => <span className="small summary-cell">{d.status ?? "—"}</span> },
        ]} />
    </Card>
  </div>;
}

function DocView({ file, anchor }: { file: string; anchor?: string }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setText(null);
    getText(`/api/docs/${file}`).then(setText).catch((e: Error) => setError(e.message));
  }, [file]);
  useEffect(() => {
    if (text && anchor) document.getElementById(decodeURIComponent(anchor))?.scrollIntoView();
  }, [text, anchor]);
  if (text == null) return <Loading error={error} />;
  return <div className="page">
    <div className="small"><a href={link("/docs")}>← 全部文档</a> <span className="muted">· {file}</span></div>
    <article className="card markdown">{renderMarkdown(text)}</article>
  </div>;
}

function slug(text: string) {
  return text.trim().toLowerCase().replace(/[`*]/g, "").replace(/[^\p{L}\p{N}\s-]/gu, "").replace(/\s+/g, "-");
}

function docHref(href: string): string | null {
  if (/^https?:/.test(href)) return href;
  if (href.startsWith("#")) return null;
  const m = href.match(/(?:^|\/)([\w.-]+\.md)(#.*)?$/);
  if (!m) return null;
  if (href.startsWith("../../")) return null;
  return link(`/docs/${m[1]}${m[2] ?? ""}`);
}

function inline(text: string, key = 0): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[([^\]]+)\]\(([^)]+)\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = pattern.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = `${key}-${m.index}`;
    if (m[1]) out.push(<code key={k}>{m[1].slice(1, -1)}</code>);
    else if (m[2]) out.push(<b key={k}>{inline(m[2].slice(2, -2), m.index)}</b>);
    else {
      const href = docHref(m[5]);
      const label = inline(m[4], m.index);
      out.push(href ? <a key={k} href={href} {...(href.startsWith("http") ? { target: "_blank", rel: "noreferrer" } : {})}>{label}</a> : <span key={k}>{label}</span>);
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function cells(line: string) {
  return line.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

type ListItem = { indent: number; ordered: boolean; start: number; text: string };

function renderList(items: ListItem[], from: number, to: number, key: number): ReactNode {
  const base = items[from].indent;
  const List = items[from].ordered ? "ol" : "ul";
  const children: ReactNode[] = [];
  let j = from;
  while (j < to) {
    let end = j + 1;
    while (end < to && items[end].indent > base) end++;
    children.push(<li key={j}>{inline(items[j].text, key + j)}{end > j + 1 && renderList(items, j + 1, end, key + j * 100)}</li>);
    j = end;
  }
  return <List className="list" start={items[from].ordered ? items[from].start : undefined}>{children}</List>;
}

export function renderMarkdown(src: string): ReactNode[] {
  const lines = src.split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const body: string[] = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) body.push(lines[i]);
      out.push(<pre key={i} className="pre small">{body.join("\n")}</pre>);
      i++;
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      const Tag = (["h2", "h2", "h3", "h4"] as const)[h[1].length - 1];
      out.push(<Tag key={i} id={slug(h[2])}>{inline(h[2], i)}</Tag>);
      i++;
      continue;
    }
    if (/^\s*\|/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const head = cells(line);
      const rows: string[][] = [];
      for (i += 2; i < lines.length && /^\s*\|/.test(lines[i]); i++) rows.push(cells(lines[i]));
      out.push(<div key={i} className="table-scroll"><table className="table compact md-table"><thead><tr>{head.map((c, j) => <th key={j}>{inline(c, j)}</th>)}</tr></thead>
        <tbody>{rows.map((r, a) => <tr key={a}>{r.map((c, j) => <td key={j}>{inline(c, a * 100 + j)}</td>)}</tr>)}</tbody></table></div>);
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const items: ListItem[] = [];
      for (; i < lines.length && (/^\s*([-*]|\d+\.)\s+/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length)); i++) {
        const item = lines[i].match(/^(\s*)([-*]|\d+\.)\s+(.*)$/);
        if (item) items.push({ indent: item[1].length, ordered: /\d/.test(item[2]), start: parseInt(item[2]) || 1, text: item[3] });
        else items[items.length - 1].text += " " + lines[i].trim();
      }
      out.push(<div key={i}>{renderList(items, 0, items.length, i)}</div>);
      continue;
    }
    if (line.startsWith(">")) {
      const body: string[] = [];
      for (; i < lines.length && lines[i].startsWith(">"); i++) body.push(lines[i].replace(/^>\s?/, ""));
      out.push(<blockquote key={i}>{inline(body.join(" "), i)}</blockquote>);
      continue;
    }
    if (/^---+\s*$/.test(line)) { out.push(<hr key={i} />); i++; continue; }
    if (!line.trim()) { i++; continue; }
    const para: string[] = [];
    for (; i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*\||\s*([-*]|\d+\.)\s|>|---+\s*$)/.test(lines[i]); i++) para.push(lines[i].trim());
    if (!para.length) { para.push(line); i++; }
    out.push(<p key={i}>{inline(para.join(" "), i)}</p>);
  }
  return out;
}
