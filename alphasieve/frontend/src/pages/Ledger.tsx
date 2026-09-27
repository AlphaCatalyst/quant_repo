import { useState } from "react";
import { useApi, type Json } from "../api";
import { Badge, Card, Empty, fmtNum, fmtTime, link, Loading } from "../components";
import { DataCard } from "./Overview";

export function Ledger() {
  const [campaign, setCampaign] = useState("");
  const [page, setPage] = useState(0);
  const size = 100;
  const q = `/api/ledger?limit=${size}&offset=${page * size}${campaign ? `&campaign=${campaign}` : ""}`;
  const { data, error } = useApi<Json>(q, 30000);
  const campaigns = useApi<Json>("/api/overview").data?.campaigns ?? [];
  if (!data) return <Loading error={error} />;
  return (
    <div className="page">
      <Card title="Trial ledger（追加写入、哈希链）" extra={
        <div className="filters">
          <select value={campaign} onChange={(e) => { setCampaign(e.target.value); setPage(0); }}>
            <option value="">全部</option>
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
            <thead><tr><th>seq</th><th>时间</th><th>trial</th><th>类型</th><th>tier</th><th>campaign</th><th>因子</th><th>结果</th><th>IC</th><th>ICIR</th><th>写入者</th><th>hash</th></tr></thead>
            <tbody>
              {data.trials.map((t: Json) => (
                <tr key={t.seq}>
                  <td>{t.seq}</td><td className="small">{fmtTime(t.created_at)}</td><td className="small">{t.trial_id}</td>
                  <td>{t.record_kind}</td><td><span className="tag">{t.evidence_tier}</span></td><td className="small">{t.campaign_id ?? "—"}</td>
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
  return (
    <div className="page">
      <DataCard data={data} />
      <Card title="panel 元数据">
        <pre className="pre">{JSON.stringify(data.panels, null, 2)}</pre>
      </Card>
    </div>
  );
}
