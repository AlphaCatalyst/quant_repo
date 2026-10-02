import { StrictMode, useEffect, useState } from "react";
import { refreshPaused, setRefreshPaused, useApi, useRefreshPaused, type Json } from "./api";
import { createRoot } from "react-dom/client";
import "./styles.css";
import Overview from "./pages/Overview";
import Campaign from "./pages/Campaign";
import { Factor, Factors } from "./pages/Factors";
import { Data, Ledger } from "./pages/Ledger";
import { Mandates, Strategy } from "./pages/Mandates";
import Compare from "./pages/Compare";
import Inbox from "./pages/Inbox";
import { Glossary } from "./components";

function useHashPath(): string {
  const [path, setPath] = useState(() => window.location.hash.slice(1) || "/");
  useEffect(() => {
    const onChange = () => {
      setPath(window.location.hash.slice(1) || "/");
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return path;
}

const NAV: [string, string][] = [
  ["/", "总览"],
  ["/inbox", "待你决定"],
  ["/mandates", "策略"],
  ["/factors", "因子"],
  ["/ledger", "试验记录"],
  ["/data", "数据"],
];

function useTableOverflow() {
  useEffect(() => {
    const root = document.getElementById("root");
    if (!root) return;
    const observed = new Set<HTMLElement>();
    const update = (container: HTMLElement) => {
      const table = container.querySelector("table");
      container.dataset.overflow = String(!!table && table.scrollWidth > container.clientWidth + 1);
    };
    const resize = new ResizeObserver(() => {
      for (const container of observed) update(container);
    });
    const scan = () => {
      for (const container of observed) {
        if (!root.contains(container)) {
          resize.unobserve(container);
          const table = container.querySelector("table");
          if (table) resize.unobserve(table);
          observed.delete(container);
        }
      }
      for (const container of root.querySelectorAll<HTMLElement>(".table-scroll")) {
        if (!observed.has(container)) {
          observed.add(container);
          resize.observe(container);
          const table = container.querySelector("table");
          if (table) resize.observe(table);
        }
        update(container);
      }
    };
    const mutation = new MutationObserver(scan);
    mutation.observe(root, { childList: true, characterData: true, subtree: true });
    scan();
    return () => { mutation.disconnect(); resize.disconnect(); };
  }, []);
}

function App() {
  useTableOverflow();
  const path = useHashPath();
  const { data: overview } = useApi<Json>("/api/overview", 30000);
  const { data: mandateSummary } = useApi<Json>("/api/mandates", 30000);
  const { data: status, updatedAt, error: statusError, reload } = useApi<Json>("/api/status", 30000);
  const paused = useRefreshPaused();
  const [now, setNow] = useState(Date.now());
  const [theme, setTheme] = useState(() => localStorage.getItem("alphasieve-theme") || "light");
  const [showGuide, setShowGuide] = useState(() => localStorage.getItem("alphasieve-guide-seen") !== "1");
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem("alphasieve-theme", theme); }, [theme]);
  useEffect(() => { const id = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(id); }, []);
  const parts = path.split("?")[0].split("/").filter(Boolean);
  let page;
  if (parts[0] === "campaign" && parts[1]) page = <Campaign id={parts[1]} key={parts[1]} />;
  else if (parts[0] === "factor" && parts[1]) page = <Factor id={parts[1]} key={parts[1]} />;
  else if (parts[0] === "strategy" && parts[1]) page = <Strategy id={parts[1]} key={parts[1]} />;
  else if (parts[0] === "compare") page = <Compare query={path.split("?")[1] ?? ""} />;
  else if (parts[0] === "mandates") page = <Mandates />;
  else if (parts[0] === "factors") page = <Factors />;
  else if (parts[0] === "ledger") page = <Ledger />;
  else if (parts[0] === "data") page = <Data />;
  else if (parts[0] === "inbox") page = <Inbox />;
  else page = <Overview />;
  const active = "/" + (parts[0] === "campaign" ? "" : parts[0] === "factor" ? "factors"
    : parts[0] === "strategy" || parts[0] === "compare" ? "mandates" : parts[0] ?? "");
  const counts: Record<string, number | undefined> = {
    "/mandates": mandateSummary?.mandates?.length, "/factors": overview?.library_size,
    "/ledger": overview?.ledger?.completed_trials,
  };
  const notices: [string, string][] = [];
  if (status?.inbox?.open_requests) notices.push([`${status.inbox.open_requests} 个待回复请求`, `/campaign/${status.inbox.request_campaigns?.[0]}`]);
  if (status?.inbox?.pending_factor_holdout) notices.push([`${status.inbox.pending_factor_holdout} 个因子留出集待批准`, `/campaign/${status.inbox.factor_holdout_campaigns?.[0]}`]);
  if (status?.inbox?.pending_strategy_holdout) notices.push([`${status.inbox.pending_strategy_holdout} 个策略留出集待批准`, "/mandates"]);
  if (status?.inbox?.open_reviews) notices.push([`${status.inbox.open_reviews} 个待评审事项`, `/campaign/${status.inbox.review_campaigns?.[0]}`]);
  const backupAge = status?.latest_backup?.created_at ? now - Date.parse(status.latest_backup.created_at) : Infinity;
  if (status && backupAge > 86400000) notices.push([status.latest_backup ? "状态库备份超过 24 小时" : "暂无状态库备份", "/data"]);
  if (status?.trade_days_lag > 3)
    notices.push([`交易数据落后 ${status.trade_days_lag} 个交易日`, "/data"]);
  return (
    <>
      <nav className="nav">
        <a className="brand" href="#/">AlphaSieve</a>
        {NAV.map(([p, label]) => (
          <a key={p} href={`#${p}`} className={active === p ? "active" : ""}>{label}{counts[p] != null && <span className="nav-count" title={p === "/mandates" ? "任务书数量" : p === "/factors" ? "因子库数量" : "已完成试验数"}>{counts[p]}</span>}</a>
        ))}
        <span className="nav-note top-chip">{paused ? "已暂停" : statusError ? "刷新失败" : `实时 · ${age(updatedAt, now)}`}</span>
        <span className="nav-meta" title="运行中的研究">运行中 {status?.running_campaigns ?? "—"}</span>
        <span className="nav-meta" title="最近交易日">交易日 {status?.latest_trade_date ?? "—"}</span>
        <span className="nav-meta" title="最近状态库备份">备份 {age(status?.latest_backup?.created_at, now)}</span>
        <button className="nav-button" onClick={() => { setRefreshPaused(!refreshPaused()); if (paused) reload(); }}>{paused ? "继续" : "暂停"}</button>
        <button className="nav-button theme-button" title={theme === "dark" ? "切换浅色" : "切换深色"} aria-label="切换主题" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>◐</button>
        <Glossary />
      </nav>
      <main>
        {showGuide && <section className="card intro-guide" aria-label="看板怎么读">
          <div className="intro-head"><h3>第一次看？这样读看板</h3><button className="btn small" onClick={() => { localStorage.setItem("alphasieve-guide-seen", "1"); setShowGuide(false); }}>知道了</button></div>
          <p>四个任务：A 中证 500 增强、B 行业 ETF 轮动、C 业绩超预期漂移、D 股指期货对冲。每个任务都有自己的验收条件和试验预算。</p>
          <p><b>dev</b> 用于开发和比较；<b>holdout</b> 是锁定配置后的留出验证。多次尝试中挑最好的一次会高估表现，“搜索折扣”用于校正这种偏差。holdout 读取次数有限，必须由人审批，避免反复试探留出集。</p>
        </section>}
        {notices.length > 0 && <div className="alert-banner"><strong>需要处理</strong>{notices.map(([label, href]) => <a key={label} href={`#${href}`}>{label} →</a>)}</div>}{page}
      </main>
    </>
  );
}

function age(value: string | number | null | undefined, now: number): string {
  if (!value) return "未知";
  const ms = typeof value === "number" ? value : Date.parse(value);
  if (!Number.isFinite(ms)) return "未知";
  const seconds = Math.max(0, Math.floor((now - ms) / 1000));
  return seconds < 60 ? `${seconds} 秒前` : seconds < 3600 ? `${Math.floor(seconds / 60)} 分前` : `${Math.floor(seconds / 3600)} 小时前`;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
