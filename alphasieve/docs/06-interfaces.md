# 06 · 接口

AlphaSieve 有两个入口：JSON CLI（agent 与人共用）和 HTTP API（前端使用）。两者调用同一套 service 层，权限与校验只实现一次。

## 1. JSON CLI

### 1.1 通用约定

- 所有命令支持 `--json`；agent 必须使用 `--json`。
- 角色由环境变量 `ALPHASIEVE_ROLE` 决定（`agent` / `human` / `system`），`human` 角色需要本地凭据（`ALPHASIEVE_USER` + token）。
- 大结果（因子值、图表、完整报告）写入 artifact，响应中只返回 `artifact_id` 与摘要。
- 长任务返回 `job_id`，用 `alphasieve job status <job_id>` 查询。

### 1.2 响应信封

```json
{
  "schema": "alphasieve.response/v1",
  "command": "factor eval",
  "status": "ok",
  "data": {},
  "artifacts": [{"artifact_id": "a1b2...", "kind": "factor_report"}],
  "warnings": [],
  "error": null
}
```

失败时 `status` 为 `error`，`error` 为 `{"code": "GATE_FAILED", "message": "...", "details": {}}`。

### 1.3 退出码

| 退出码 | error.code | 含义 |
|---|---|---|
| 0 | — | 成功 |
| 2 | `VALIDATION_ERROR` | 输入不合法（YAML、DSL 语法、参数） |
| 3 | `GATE_FAILED` | 评估完成但未通过 gate（结果已入账） |
| 4 | `PERMISSION_DENIED` | 当前角色无权执行 |
| 5 | `BUDGET_EXHAUSTED` | trial / holdout / 费用预算不足 |
| 6 | `NOT_FOUND` | 对象不存在 |
| 7 | `CONFLICT` | 状态不允许该操作（例如对已锁定的 shortlist 增删） |
| 10 | `INTERNAL` | 内部错误 |

### 1.4 命令清单

角色列：A = agent，H = human，S = system。

| 命令 | 角色 | 说明 |
|---|---|---|
| `version` | A H S | 版本信息 |
| `data status` | A H S | 数据最新日期、区间边界、质量检查摘要 |
| `data describe <field>` | A H S | 字段含义、覆盖率、分布（仅 dev） |
| `data sample --fields ... --date ...` | A H | 抽样查看 dev 区间数据（行数上限） |
| `factor validate <spec.yaml>` | A H | 只跑 L0，不计 trial |
| `factor eval <spec.yaml>` | A H | 跑 L0–L2，写 ledger，返回指标与 gate 结果 |
| `factor show <factor_id>[@version]` | A H | 因子定义、状态、dev 指标（agent 看不到 holdout/fresh 字段） |
| `factor list [--state ...] [--campaign ...]` | A H | 列表 |
| `library list` / `library corr <factor_id>` | A H | 因子库成员与相关性 |
| `ledger stats [--campaign ...]` | A H | trial 数、失败原因分布、当前 DSR 门槛 |
| `memory show [--scope ...]` | A H | 经验记忆 |
| `report submit <file.md>` | A | 提交阶段报告 |
| `request create --kind data\|question\|scope` | A | 向人提出请求 |
| `campaign create <campaign.yaml>` | H | 创建 campaign |
| `campaign start\|pause\|resume\|stop <id>` | H | 控制 campaign |
| `campaign status <id>` | A H | 进度、预算、漏斗 |
| `directive add <campaign_id> --kind ... --text ...` | H | 给 agent 下指令 |
| `shortlist lock <campaign_id>` | H S | 锁定 shortlist 并冻结记忆 |
| `holdout request <shortlist_id>` | S H | 提交 holdout 申请 |
| `holdout approve\|reject <request_id> --reason ...` | H | 审批 |
| `review show <packet_id>` | H | 查看 Review Packet |
| `review decide <packet_id> --decision ... --reason ...` | H | 评审决定 |
| `strategy backtest <strategy.yaml>` | H | 策略回测（M6） |
| `fresh status [--cohort ...]` | H | 前瞻观察状态 |
| `job status <job_id>` | A H S | 任务状态 |
| `agent run --campaign <id> --once` | H S | 手动触发一个 turn（调试用） |

## 2. HTTP API

- 前缀 `/api/v1`，JSON 响应；OpenAPI 文档自动生成，前端类型由其生成。
- 认证：第一阶段单机部署，用户名密码 + session cookie；所有写操作记录用户。
- 读接口返回 read model（为页面聚合好的视图），避免前端拼接多次请求。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/overview` | 总览：活跃 campaign、待审批数、全局漏斗、预算、fresh 概况 |
| GET | `/campaigns` / `/campaigns/{id}` | 列表与详情（含漏斗、预算、停止条件进度） |
| POST | `/campaigns` | 创建 |
| POST | `/campaigns/{id}/actions` | `start` / `pause` / `resume` / `stop` / `conclude` |
| GET | `/campaigns/{id}/turns` / `/turns/{id}` | turn 列表、transcript 回放 |
| POST | `/campaigns/{id}/directives` | 下指令 |
| GET | `/campaigns/{id}/search-intensity` | 搜索强度曲线数据 |
| GET | `/factors` / `/factors/{id}` | 因子库与因子详情（含全部证据等级，human 可见） |
| POST | `/factors/{id}/comments` | 评论（可标记是否共享给 agent） |
| GET | `/ledger` | trial 分页查询与筛选 |
| GET | `/inbox` | 待处理事项：HoldoutRequest、Review Packet、AgentRequest、异常告警 |
| POST | `/holdout-requests/{id}/decision` | 批准 / 拒绝 |
| GET | `/review-packets/{id}` | Review Packet |
| POST | `/review-packets/{id}/decision` | 评审决定 |
| POST | `/agent-requests/{id}/response` | 回复 agent 请求 |
| GET | `/strategies` / `/strategies/{id}` | 策略与回测结果 |
| GET | `/fresh/cohorts` / `/fresh/cohorts/{id}` | 前瞻观察 |
| GET | `/memory` | 记忆条目 |
| GET | `/data/status` | 数据状态与质量报告 |
| GET | `/settings` / PUT `/settings/{key}` | 默认预算、调度参数（gate policy 只读展示） |
| GET | `/events` | SSE 事件流 |

## 3. 事件流

所有状态变化都写入 `events` 表，并通过 SSE（`/api/v1/events?campaign=...`）推送。

| 事件类型 | 载荷要点 |
|---|---|
| `campaign.status_changed` | campaign_id、from、to |
| `turn.started` / `turn.finished` | turn_id、agent、model、费用 |
| `turn.chunk` | turn_id、类型（tool_call / tool_result / text 摘要） |
| `trial.completed` | trial_id、factor_id、gate 结果、关键 dev 指标 |
| `factor.state_changed` | factor_id、version、from、to、依据 |
| `shortlist.locked` | shortlist_id、成员 |
| `inbox.created` / `inbox.resolved` | 类型、对象 id |
| `holdout.completed` | shortlist_id、通过数（不含具体指标，具体指标只在 Review Packet） |
| `fresh.updated` | cohort_id、日期 |
| `budget.warning` | campaign_id、预算类型、已用比例 |
| `system.alert` | 数据更新失败、ledger 校验失败、连续 turn 失败 |

## 4. Artifact 布局

```text
artifacts/<artifact_id>/
  manifest.json      类型、生成命令、代码版本、数据快照签名、配置、gate policy 版本、种子、父 artifact
  metrics.json       结构化指标（带 evidence_tier）
  report.md          人可读报告（因子卡片、实验报告、Review Packet）
  charts/*.json      图表数据（前端用 ECharts 渲染，不存图片）
  logs/stdout.txt
artifacts/turns/<turn_id>.jsonl   agent transcript
```

- `artifact_id` = manifest 规范化后的哈希；相同输入重复运行得到相同 id，用于复现校验。
- 图表以数据形式存储，前端统一渲染，保证样式一致、可交互。
