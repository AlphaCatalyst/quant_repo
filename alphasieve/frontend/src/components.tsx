import { useEffect, useRef, useState, type ReactNode } from "react";
import * as echarts from "echarts";

export const STATUS_LABEL: Record<string, string> = {
  draft: "草稿",
  running: "运行中",
  paused: "已暂停",
  concluding: "结题中",
  awaiting_holdout_approval: "等待 holdout 批准",
  holdout_evaluated: "holdout 已评估",
  concluded: "已结束",
  completed: "完成",
  failed: "失败",
  timeout: "超时",
  integrity_violation: "违规",
  pending: "待处理",
  open: "待处理",
  approved: "已批准",
  rejected: "已拒绝",
  answered: "已答复",
  consumed: "已送达",
  validation_failed: "校验未通过",
  evaluation_failed: "评估未通过",
  robust_failed: "稳健性未通过",
  robust_passed: "稳健性通过",
  holdout_passed: "留出集通过",
  holdout_failed: "留出集未通过",
  holdout_contaminated: "留出集污染",
  error: "错误",
  library: "已入库",
  proposed: "待评估",
  validating: "校验中",
  validated: "已校验",
  evaluating: "评估中",
  robust_checking: "稳健性检查中",
  shortlisted: "已入围",
};

export const OUTCOME_LABEL: Record<string, string> = {
  validation_failed: "L0 未通过",
  evaluation_failed: "L1 未通过",
  robust_failed: "L2 未通过",
  robust_passed: "L2 通过",
  holdout_passed: "holdout 通过",
  holdout_failed: "holdout 未通过",
  holdout_contaminated: "holdout 污染",
  error: "错误",
  dev_passed: "dev 验收通过",
  dev_failed: "dev 验收未通过",
  run_failed: "运行失败",
};

const TONE: Record<string, string> = {
  running: "green", completed: "green", robust_passed: "green", holdout_passed: "green", approved: "green",
  paused: "amber", awaiting_holdout_approval: "amber", pending: "amber", open: "amber", concluding: "amber",
  timeout: "amber", robust_failed: "amber", dev_failed: "amber", dev_passed: "green", run_failed: "red",
  failed: "red", integrity_violation: "red", error: "red", holdout_failed: "red", rejected: "red",
  validation_failed: "grey", evaluation_failed: "grey", concluded: "blue", holdout_evaluated: "blue",
};

export function Badge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="badge grey">—</span>;
  const label = STATUS_LABEL[value] ?? OUTCOME_LABEL[value] ?? value;
  return <span className={`badge ${TONE[value] ?? "grey"}`}>{label}</span>;
}

export function Card({ title, children, extra }: { title?: ReactNode; children: ReactNode; extra?: ReactNode }) {
  return (
    <section className="card">
      {title && (
        <header>
          <h3>{title}</h3>
          {extra}
        </header>
      )}
      {children}
    </section>
  );
}

export function Progress({ label, used, budget, unit = "" }: { label: string; used: number; budget: number; unit?: string }) {
  const pct = budget ? Math.min(100, (used / budget) * 100) : 0;
  return (
    <div className="progress">
      <div className="progress-label">
        <span>{label}</span>
        <span>
          {fmtNum(used)}
          {unit} / {fmtNum(budget)}
          {unit}
        </span>
      </div>
      <div className="progress-track">
        <div className={`progress-fill ${pct >= 90 ? "hot" : ""}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function fmtNum(v: unknown, digits = 3): string {
  if (typeof v !== "number" || !isFinite(v)) return "—";
  if (Number.isInteger(v)) return v.toLocaleString();
  return v.toFixed(digits);
}

export function fmtPct(v: unknown, digits = 1): string {
  return typeof v === "number" && isFinite(v) ? `${(v * 100).toFixed(digits)}%` : "—";
}

export function fmtTime(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(v);
  return isNaN(d.getTime()) ? v : d.toLocaleString("zh-CN", { hour12: false });
}

export function duration(a?: string | null, b?: string | null): string {
  if (!a || !b) return "—";
  const s = (new Date(b).getTime() - new Date(a).getTime()) / 1000;
  if (!isFinite(s)) return "—";
  return s >= 60 ? `${Math.floor(s / 60)}分${Math.round(s % 60)}秒` : `${Math.round(s)}秒`;
}

export function Chart({ option, height = 280 }: { option: echarts.EChartsOption; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    chart.current = echarts.init(ref.current);
    const onResize = () => chart.current?.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.current?.dispose();
    };
  }, []);
  useEffect(() => {
    chart.current?.setOption(option, true);
  }, [option]);
  return <div ref={ref} style={{ height, width: "100%" }} />;
}

export function Loading({ error }: { error?: string | null }) {
  return <div className={error ? "error" : "muted"}>{error ? `加载失败：${error}` : "加载中…"}</div>;
}

export function Empty({ text = "暂无数据" }: { text?: string }) {
  return <div className="muted empty">{text}</div>;
}

export function link(path: string) {
  return `#${path}`;
}

export function exportCSV(filename: string, headers: string[], rows: unknown[][]) {
  const cell = (value: unknown) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  const csv = "\ufeff" + [headers, ...rows].map((row) => row.map(cell).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export type TableColumn<T> = { key: string; label: string; value: (row: T) => unknown; render?: (row: T) => ReactNode; sortable?: boolean };

export function DataTable<T>({ rows, columns, filename, searchPlaceholder = "搜索", filters = [] }: {
  rows: T[]; columns: TableColumn<T>[]; filename: string; searchPlaceholder?: string;
  filters?: { label: string; value: (row: T) => string; options: { value: string; label: string }[] }[];
}) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [sort, setSort] = useState("");
  const filtered = rows.filter((row) =>
    (!query || columns.some((col) => String(col.value(row) ?? "").toLowerCase().includes(query.toLowerCase()))) &&
    filters.every((f) => !selected[f.label] || f.value(row) === selected[f.label]));
  const key = sort.replace(/^-/, "");
  const column = columns.find((c) => c.key === key);
  const ordered = column ? [...filtered].sort((a, b) => {
    const x = column.value(a), y = column.value(b);
    const n = x == null ? 1 : y == null ? -1 : typeof x === "number" && typeof y === "number" ? x - y
      : String(x).localeCompare(String(y), "zh-CN", { numeric: true });
    return sort.startsWith("-") ? -n : n;
  }) : filtered;
  return <>
    <div className="table-tools filters">
      <input aria-label={searchPlaceholder} placeholder={searchPlaceholder} value={query} onChange={(e) => setQuery(e.target.value)} />
      {filters.map((f) => <select key={f.label} aria-label={f.label} value={selected[f.label] ?? ""}
        onChange={(e) => setSelected({ ...selected, [f.label]: e.target.value })}>
        <option value="">{f.label} · 全部（{rows.length}）</option>
        {f.options.map((o) => <option key={o.value} value={o.value}>{o.label}（{rows.filter((r) => f.value(r) === o.value).length}）</option>)}
      </select>)}
      <span className="muted small">显示 {ordered.length} / {rows.length}</span>
      <button className="btn small" onClick={() => exportCSV(filename, columns.map((c) => c.label), ordered.map((r) => columns.map((c) => c.value(r))))}>导出 CSV</button>
    </div>
    <div className="table-scroll" role="region" aria-label="数据表格，向右滚动查看更多列">
      <table className="table"><thead><tr>{columns.map((c) => <th key={c.key}>
        {c.sortable === false ? c.label : <button className="table-sort" onClick={() => setSort(sort === c.key ? `-${c.key}` : c.key)}>{c.label}{key === c.key ? sort.startsWith("-") ? " ↓" : " ↑" : ""}</button>}
      </th>)}</tr></thead><tbody>{ordered.map((row, i) => <tr key={i}>{columns.map((c) => <td key={c.key}>{c.render ? c.render(row) : String(c.value(row) ?? "—")}</td>)}</tr>)}</tbody></table>
    </div>
  </>;
}

const TERMS: [string, string][] = [
  ["IC / RankIC", "每天计算因子值与未来标签的截面 Spearman 相关；展示的 IC 是日度 RankIC 均值。"],
  ["ICIR", "日度 RankIC 均值除以其标准差。"],
  ["L1", "开发窗口检查覆盖率、方向调整后的 RankIC、ICIR 和与因子库的相关性。"],
  ["L2", "样本内稳健性检查，包括子窗口同号与中性化后 RankIC。"],
  ["L3", "整批搜索折扣关卡；按 campaign ledger 的试验数与方差计算 DSR。"],
  ["N / 折扣后", "N 是 mandate 已完成的累计策略 trial 数；策略层折扣后 IR 或夏普为观测值减去 N 次零假设试验的期望最大值，并非显著性概率。"],
  ["TE", "跟踪误差：组合相对基准的主动收益波动。具体目标按 mandate 验收定义。"],
  ["IR", "信息比率：年化净超额除以跟踪误差。"],
  ["MDD", "最大回撤；策略页的超额最大回撤相对基准计算。"],
];

export function Glossary() {
  const [open, setOpen] = useState(false);
  return <>
    <button className="glossary-trigger" onClick={() => setOpen(true)}>指标口径</button>
    {open && <div className="drawer" onClick={() => setOpen(false)}><div className="drawer-body glossary" onClick={(e) => e.stopPropagation()}>
      <button className="btn close" onClick={() => setOpen(false)}>关闭</button>
      <h2>指标口径</h2><p className="muted">摘要依据 docs/04-research-core.md、docs/18-mandates.md 与 docs/21-a-portfolio.md；各策略的具体阈值以其验收检查为准。</p>
      <dl>{TERMS.map(([term, definition]) => <div key={term}><dt>{term}</dt><dd>{definition}</dd></div>)}</dl>
    </div></div>}
  </>;
}
