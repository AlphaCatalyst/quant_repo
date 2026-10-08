import { useApi, type Json } from "../api";
import { Card, DataTable, Empty, fmtNum, link, Loading } from "../components";

const LEVEL: Record<string, [string, string]> = {
  red: ["红灯", "red"], amber: ["黄灯", "amber"], none: ["未触发", "green"], unavailable: ["数据不足", "grey"],
};

export function LevelBadge({ level }: { level: string }) {
  const [label, tone] = LEVEL[level] ?? [level, "grey"];
  return <span className={`badge ${tone}`}>{label}</span>;
}

function fmtValue(v: number | null | undefined) {
  if (v == null) return "—";
  return Math.abs(v) >= 10 ? fmtNum(v, 1) : fmtNum(v, 3);
}

export default function RedFlags() {
  const { data, error } = useApi<Json>("/api/redflags", 300000);
  if (!data) return <Loading error={error} />;
  if (!data.asof) return <Card title="财报排雷"><Empty text="还没有扫描结果：alphasieve redflag scan --universe csi800" /></Card>;
  const rules: Json[] = data.rules;
  const ruleName = Object.fromEntries(rules.map((r) => [r.id, r.name]));
  const held = (data.rows as Json[]).filter((r) => r.held);
  const levels = data.levels ?? {};
  const maxRule = Math.max(1, ...Object.values(data.rule_counts ?? {}).map((c: Json) => c.red + c.amber));
  return (
    <div className="page">
      <div className="page-head"><div>
        <h2>财报排雷</h2>
        <p className="muted question">用最近一期已公告的财报，按 9 条规则查应收、存货、现金流、商誉等异常，和同行业比较给出红/黄灯。
          这是排雷线索，不是买卖信号；红灯说明“需要去读原文”。扫描日 {data.asof}，范围 {data.universe ?? "—"}（{fmtNum(data.count)} 只）。
          <a href={link("/docs/financial-red-flags.md")}>规则口径 →</a></p>
      </div></div>

      <div className="metric-strip">
        {(["red", "amber", "none", "unavailable"] as const).map((k) => <div key={k} className={`stat ${k === "red" || k === "amber" ? "amber" : ""}`}>
          <div className="stat-value">{fmtNum(levels[k] ?? 0)}</div><div className="stat-label">{LEVEL[k][0]}</div></div>)}
      </div>

      <div className="two-col">
        <Card title={`我的持仓（${held.length}）`}>
          {held.length === 0 ? <Empty text="持仓不在扫描范围内" /> : <table className="table compact"><thead><tr><th>股票</th><th>行业</th><th>结论</th><th>触发规则</th></tr></thead><tbody>
            {held.map((r) => <tr key={r.code}><td>{r.name ?? r.code}<div className="muted small">{r.code}</div></td><td>{r.industry ?? "—"}</td>
              <td><LevelBadge level={r.level} /></td>
              <td className="small">{Object.keys(r.flags).length ? Object.entries(r.flags).map(([k, f]: [string, Json]) => `${ruleName[k] ?? k}（${LEVEL[f.level]?.[0]}）`).join("、") : "—"}</td></tr>)}
          </tbody></table>}
          <p className="small muted">{data.held_total > held.length ? `另有 ${data.held_total - held.length} 只持仓不在扫描范围（${data.universe}）内；` : ""}需要时可以对单只股票运行 alphasieve redflag check。</p>
        </Card>
        <Card title="各规则触发次数">
          <div className="bars">{rules.map((r) => {
            const c = data.rule_counts?.[r.id] ?? { red: 0, amber: 0 };
            return <div key={r.id} className="bar-row" title={`${r.formula}\n${r.rationale}`}>
              <span className="bar-label">{r.name}{!r.available && "（暂不可用）"}</span>
              <span className="bar-track stacked"><span className="bar-fill red" style={{ width: `${(c.red / maxRule) * 100}%` }} /><span className="bar-fill amber" style={{ width: `${(c.amber / maxRule) * 100}%` }} /></span>
              <span className="bar-value">{c.red}/{c.amber}</span></div>;
          })}</div>
          <p className="small muted">右侧数字为 红灯/黄灯 次数；鼠标悬停看公式与含义。</p>
        </Card>
      </div>

      <Card title="红灯与黄灯清单">
        <DataTable rows={(data.rows as Json[]).filter((r) => r.level === "red" || r.level === "amber")} filename={`redflags-${data.asof}.csv`} searchPlaceholder="搜索股票或行业"
          filters={[
            { label: "结论", value: (r: Json) => r.level, options: [{ value: "red", label: "红灯" }, { value: "amber", label: "黄灯" }] },
            { label: "行业", value: (r: Json) => r.industry ?? "", options: Array.from(new Set((data.rows as Json[]).map((r) => r.industry ?? ""))).sort().map((v) => ({ value: v, label: v || "未知" })) },
            { label: "规则", value: (r: Json) => Object.keys(r.flags)[0] ?? "", options: rules.map((r) => ({ value: r.id, label: r.name })) },
          ]}
          columns={[
            { key: "name", label: "股票", value: (r: Json) => `${r.name ?? ""} ${r.code}`, render: (r: Json) => <>{r.name ?? r.code}{r.held && <span className="tag">持仓</span>}<div className="muted small">{r.code}</div></> },
            { key: "industry", label: "行业", value: (r: Json) => r.industry },
            { key: "level", label: "结论", value: (r: Json) => r.level === "red" ? 0 : 1, render: (r: Json) => <LevelBadge level={r.level} /> },
            { key: "n", label: "触发数", value: (r: Json) => Object.keys(r.flags).length },
            { key: "flags", label: "触发规则（数值）", value: (r: Json) => Object.keys(r.flags).map((k) => ruleName[k]).join("、"), sortable: false,
              render: (r: Json) => <span className="small">{Object.entries(r.flags).map(([k, f]: [string, Json]) => <span key={k} className={`check-chip ${f.level === "red" ? "bad" : ""}`}>{ruleName[k] ?? k} {fmtValue(f.value)}</span>)}</span> },
            { key: "period", label: "报告期", value: (r: Json) => r.period },
            { key: "ann", label: "公告日", value: (r: Json) => r.announcement_date },
          ]} />
      </Card>
    </div>
  );
}
