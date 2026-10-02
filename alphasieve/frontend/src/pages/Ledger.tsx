import { useState } from "react";
import { useApi, type Json } from "../api";
import { Badge, Card, Empty, fmtNum, fmtTime, link, Loading } from "../components";

export function Ledger() {
  const [campaign, setCampaign] = useState("");
  const [layer, setLayer] = useState("");
  const [mandate, setMandate] = useState("");
  const [outcome, setOutcome] = useState("");
  const [page, setPage] = useState(0);
  const size = 100;
  const q = `/api/ledger?${new URLSearchParams({ limit: String(size), offset: String(page * size),
    ...(campaign ? { campaign } : {}), ...(layer ? { layer } : {}),
    ...(mandate ? { mandate } : {}), ...(outcome ? { outcome } : {}) }).toString()}`;
  const { data, error } = useApi<Json>(q, 30000);
  const campaigns = useApi<Json>("/api/overview").data?.campaigns ?? [];
  if (!data) return <Loading error={error} />;
  return (
    <div className="page">
      <Card title="试验记录（追加写入、哈希链）" extra={
        <div className="filters">
          <select aria-label="层级" value={layer} onChange={(e) => { setLayer(e.target.value); setPage(0); }}>
            <option value="">全部层级</option><option value="factor">因子</option><option value="strategy">策略</option>
          </select>
          <select aria-label="任务" value={mandate} onChange={(e) => { setMandate(e.target.value); setPage(0); }}>
            <option value="">全部任务</option>{["A", "B", "C", "D"].map((m) => <option key={m} value={m}>任务 {m}</option>)}
          </select>
          <select aria-label="结果" value={outcome} onChange={(e) => { setOutcome(e.target.value); setPage(0); }}>
            <option value="">全部结果</option>
            <option value="dev_passed">开发通过</option><option value="dev_failed">开发未通过</option>
            <option value="holdout_passed">留出集通过</option><option value="holdout_failed">留出集未通过</option>
            <option value="robust_passed">因子稳健性通过</option><option value="robust_failed">因子稳健性未通过</option>
            <option value="evaluation_failed">评估失败</option><option value="run_failed">运行失败</option>
          </select>
          <select value={campaign} onChange={(e) => { setCampaign(e.target.value); setPage(0); }}>
            <option value="">全部研究</option>
            {campaigns.map((c: Json) => <option key={c.campaign_id} value={c.campaign_id}>{c.campaign_id}</option>)}
          </select>
          <button className="btn small" disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button>
          <button className="btn small" disabled={data.trials.length < size} onClick={() => setPage(page + 1)}>下一页</button>
        </div>
      }>
        <div className={data.verify.ok ? "ok" : "error"}>
          哈希链校验：{data.verify.ok ? `通过（${data.verify.rows} 行）` : `失败：${JSON.stringify(data.verify.errors).slice(0, 300)}`}
        </div>
        {data.trials.length === 0 ? <Empty /> : (
          <table className="table">
            <thead><tr><th>序号</th><th>时间</th><th>试验</th><th>层级</th><th>任务</th><th>记录类型</th><th>区间</th><th>研究</th><th>因子</th><th>结果</th><th>IC</th><th>ICIR</th><th>写入者</th><th>哈希</th></tr></thead>
            <tbody>
              {data.trials.map((t: Json) => (
                <tr key={t.seq}>
                  <td>{t.seq}</td><td className="small">{fmtTime(t.created_at)}</td><td className="small">{t.trial_id}</td>
                  <td>{t.layer === "strategy" ? "策略" : "因子"}</td><td>{t.scope ?? "—"}</td>
                  <td>{({ completed: "完成", failed: "失败", void: "作废" } as Record<string, string>)[t.record_kind] ?? t.record_kind}</td>
                  <td><span className="tag">{t.evidence_tier === "holdout" ? "留出集" : t.evidence_tier === "dev" ? "开发" : t.evidence_tier}</span></td><td className="small">{t.campaign_id ?? "—"}</td>
                  <td>{t.factor_id ? <a href={link(`/factor/${t.factor_id}`)}>{t.factor_id}@{t.version}</a> : "—"}</td>
                  <td><Badge value={t.outcome} /></td><td>{fmtNum(t.metrics.ic_mean, 4)}</td><td>{fmtNum(t.metrics.icir)}</td>
                  <td>{t.created_by}</td><td className="small mono">{t.hash.slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

export function Data() {
  const { data, error } = useApi<Json>("/api/data", 60000);
  if (!data) return <Loading error={error} />;
  const update = data.last_daily_update;
  return (
    <div className="page">
      <Card title="数据区间与面板">
        <table className="table">
          <thead><tr><th>区间</th><th>窗口</th><th>行数</th><th>标的数</th><th>构建时间</th><th>状态</th></tr></thead>
          <tbody>{["dev", "holdout", "fresh"].map((tier) => {
            const split = data.splits[tier];
            const panel = data.panels[tier];
            return <tr key={tier}><td>{tier}</td><td>{split?.start ?? "—"} ~ {split?.end ?? "—"}</td>
              <td>{panel?.rows != null ? fmtNum(panel.rows) : "—"}</td><td>{panel?.codes ?? "—"}</td>
              <td>{panel?.built_at ? fmtTime(panel.built_at) : "—"}</td>
              <td>{panel ? "有元数据" : "暂无元数据"}</td></tr>;
          })}</tbody>
        </table>
      </Card>
      <Card title="最近日更"><p>{update ? `${update.status} · 数据到 ${update.end ?? "—"} · 新增 ${fmtNum(update.new_rows)} 行 · 记录于 ${fmtTime(new Date(update.logged_at * 1000).toISOString())}` : "暂无日更记录"}</p></Card>
    </div>
  );
}
