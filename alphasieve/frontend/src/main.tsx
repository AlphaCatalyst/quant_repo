import { StrictMode, useEffect, useState } from "react";
import { refreshPaused, setRefreshPaused, useApi, useRefreshPaused, type Json } from "./api";
import { createRoot } from "react-dom/client";
import "./styles.css";
import Overview from "./pages/Overview";
import Campaign from "./pages/Campaign";
import { Factor, Factors } from "./pages/Factors";
import { Data, Ledger } from "./pages/Ledger";
import { Mandates, Strategy } from "./pages/Mandates";
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
  ["/mandates", "策略"],
  ["/factors", "因子"],
  ["/ledger", "试验记录"],
  ["/data", "数据"],
];

function App() {
  const path = useHashPath();
  const { data: overview } = useApi<Json>("/api/overview", 30000);
  const { data: status, updatedAt, error: statusError, reload } = useApi<Json>("/api/status", 30000);
  const paused = useRefreshPaused();
  const [now, setNow] = useState(Date.now());
  const [theme, setTheme] = useState(() => localStorage.getItem("alphasieve-theme") || "light");
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem("alphasieve-theme", theme); }, [theme]);
  useEffect(() => { const id = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(id); }, []);
  const parts = path.split("?")[0].split("/").filter(Boolean);
  let page;
  if (parts[0] === "campaign" && parts[1]) page = <Campaign id={parts[1]} key={parts[1]} />;
  else if (parts[0] === "factor" && parts[1]) page = <Factor id={parts[1]} key={parts[1]} />;
  else if (parts[0] === "strategy" && parts[1]) page = <Strategy id={parts[1]} key={parts[1]} />;
  else if (parts[0] === "mandates") page = <Mandates />;
  else if (parts[0] === "factors") page = <Factors />;
  else if (parts[0] === "ledger") page = <Ledger />;
  else if (parts[0] === "data") page = <Data />;
  else page = <Overview />;
  const active = "/" + (parts[0] === "campaign" ? "" : parts[0] === "factor" ? "factors"
    : parts[0] === "strategy" ? "mandates" : parts[0] ?? "");
  const counts: Record<string, number | undefined> = {
    "/": overview?.campaigns?.length, "/mandates": 4, "/factors": overview?.library_size,
    "/ledger": overview?.ledger?.completed_trials,
  };
  const notices: [string, string][] = [];
  if (status?.inbox?.open_requests) notices.push([`${status.inbox.open_requests} 个待回复请求`, `/campaign/${status.inbox.request_campaigns?.[0]}`]);
  if (status?.inbox?.pending_factor_holdout) notices.push([`${status.inbox.pending_factor_holdout} 个因子留出集待批准`, `/campaign/${status.inbox.factor_holdout_campaigns?.[0]}`]);
  if (status?.inbox?.pending_strategy_holdout) notices.push([`${status.inbox.pending_strategy_holdout} 个策略留出集待批准`, "/mandates"]);
  if (status?.inbox?.open_reviews) notices.push([`${status.inbox.open_reviews} 个待评审事项`, `/campaign/${status.inbox.review_campaigns?.[0]}`]);
  const backupAge = status?.latest_backup?.created_at ? now - Date.parse(status.latest_backup.created_at) : Infinity;
  if (status && backupAge > 86400000) notices.push([status.latest_backup ? "状态库备份超过 24 小时" : "暂无状态库备份", "/data"]);
  const tradeAge = status?.latest_trade_date ? now - Date.parse(status.latest_trade_date) : NaN;
  if (status && Number.isFinite(tradeAge) && tradeAge > 5 * 86400000) notices.push(["交易数据超过 5 天", "/data"]);
  return (
    <>
      <nav className="nav">
        <a className="brand" href="#/">AlphaSieve</a>
        {NAV.map(([p, label]) => (
          <a key={p} href={`#${p}`} className={active === p ? "active" : ""}>{label}{counts[p] != null && <span className="nav-count">{counts[p]}</span>}</a>
        ))}
        <span className="nav-note top-chip">{paused ? "已暂停" : statusError ? "刷新失败" : `实时 · ${age(updatedAt, now)}`}</span>
        <button className="nav-button" onClick={() => { setRefreshPaused(!refreshPaused()); if (paused) reload(); }}>{paused ? "继续" : "暂停"}</button>
        <button className="nav-button" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? "浅色" : "深色"}</button>
      </nav>
      <div className="status-line"><span>运行中 {status?.running_campaigns ?? "—"}</span><span>最近更新交易日 {status?.latest_trade_date ?? "未知"}</span><span>状态库备份 {age(status?.latest_backup?.created_at, now)}</span><span>只读视图</span><Glossary /></div>
      <main>{notices.length > 0 && <div className="alert-banner"><strong>需要处理</strong>{notices.map(([label, href]) => <a key={label} href={`#${href}`}>{label} →</a>)}</div>}{page}</main>
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
