import { useEffect, useRef, type ReactNode } from "react";
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
};

const TONE: Record<string, string> = {
  running: "green", completed: "green", robust_passed: "green", holdout_passed: "green", approved: "green",
  paused: "amber", awaiting_holdout_approval: "amber", pending: "amber", open: "amber", concluding: "amber",
  timeout: "amber", robust_failed: "amber",
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
