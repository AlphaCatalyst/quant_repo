# 02 · 系统架构

## 1. 总览

```text
                ┌──────────────────────────────────────────────┐
  人 ──浏览器──▶ │ Web UI (React)                               │
                └───────────────┬──────────────────────────────┘
                                │ HTTP + SSE
                ┌───────────────▼──────────────────────────────┐
  人 ──终端───▶ │ API Server (FastAPI)     alphasieve CLI       │ ◀── agent（仅 CLI，role=agent）
                └───────────────┬──────────────────────────────┘
                                │ 同一套 service 层（Python 包 alphasieve）
     ┌──────────────┬───────────┼──────────────┬───────────────┬──────────────┐
     ▼              ▼           ▼              ▼               ▼              ▼
  contracts       data       factors      evaluation       ledger/gates    backtest
     │              │           │              │               │              │
     └──────────────┴───────────┴──────┬───────┴───────────────┴──────────────┘
                                       ▼
                 存储：SQLite（元数据/ledger） + Parquet（数据/因子值） + 文件系统（artifact）

  Orchestrator（常驻进程）：调度 agent turn、执行批次 gate、计预算、跑定时任务
  Agent Adapter：以子进程方式启动 Claude Code / Codex，只暴露 CLI
```

核心约束：

- **唯一写入路径**：所有状态变更都经过 `alphasieve` 包内的 service 层。CLI 和 API 只是两个入口，调用同一套 service 函数，因此权限与校验只实现一次。
- **agent 只能走 CLI**：agent 进程的环境变量是 `ALPHASIEVE_ROLE=agent`，service 层按角色拒绝越权命令。
- **计算只在后端**：评估、回测、gate 判定都在 service 层完成；前端和 agent 只读结果。

## 2. 组件

| 组件 | 目录 | 职责 |
|---|---|---|
| contracts | `src/alphasieve/contracts/` | pydantic 模型：ResearchQuestion、DataContract、FactorSpec、StrategySpec、TrialLedgerEntry、Campaign 等；schema 版本化 |
| data | `src/alphasieve/data/` | 数据拉取、panel 构建、PIT 规则、可交易性标记、数据区间划分与访问控制 |
| factors | `src/alphasieve/factors/` | 因子 DSL 解析、算子库、表达式树、规范化与哈希、复杂度度量 |
| evaluation | `src/alphasieve/evaluation/` | 因子指标、多窗口检验、中性化、参考模型组边际贡献、评估不变量 |
| ledger | `src/alphasieve/ledger/` | trial ledger（append-only + 哈希链）、holdout 读取预算、试验计数 |
| gates | `src/alphasieve/gates/` | L0–L4 gate 实现、gate policy 版本、因子状态机 |
| backtest | `src/alphasieve/backtest/` | A 股日频截面执行模拟、指数增强组合构建 |
| models | `src/alphasieve/models/`（M6 新增） | 固定配置模型、滚动重训、参考模型组 |
| campaigns | `src/alphasieve/campaigns/`（M3 新增） | Campaign、Turn、Directive、预算与停止条件 |
| agent | `src/alphasieve/agent/`（M3 新增） | agent 执行器抽象（本机 Claude Code / Codex，M5 起 Nexus Cloud）、prompt 组装、transcript 解析，见 [14-agent-execution.md](14-agent-execution.md) |
| search_space | `src/alphasieve/search_space/`（M2 新增） | SearchSpace 配置、派生变量库、覆盖坐标与落格，见 [11-factor-search-space.md](11-factor-search-space.md) |
| memory | `src/alphasieve/memory/`（M3 新增） | 经验记忆：成功模板、禁区、洞察；冻结与解冻 |
| review | `src/alphasieve/review/`（M4 新增） | Review Packet 生成、审批记录、PromotionRecord |
| fresh | `src/alphasieve/fresh/`（M7 新增） | fresh cohort、前瞻分池、regime 信任门 |
| api | `src/alphasieve/api/`（F1 新增） | FastAPI 应用、read model、SSE 事件流 |
| cli | `src/alphasieve/cli/` | JSON CLI |
| orchestrator | `src/alphasieve/orchestrator/`（M3 新增） | 常驻调度进程、任务表、定时任务 |
| web | `web/`（F1 新增） | 前端工程 |

## 3. 进程模型

| 进程 | 启动方式 | 说明 |
|---|---|---|
| API Server | `alphasieve serve` | FastAPI + uvicorn，服务前端与 SSE；只处理轻量请求，重计算投递到任务表 |
| Orchestrator | `alphasieve orchestrator` | 单实例常驻；轮询任务表，调度 agent turn、批次 gate、holdout 评估、定时任务 |
| Worker | 由 orchestrator 以子进程启动 | 执行评估、回测、重训等重计算；超时与内存上限可配 |
| Agent 会话 | 由 orchestrator 以子进程启动 | `claude -p` / `codex exec`，cwd 为 campaign workspace |
| CLI | 人或 agent 直接调用 | 短命令直接执行；长任务投递到任务表并返回 job id |

任务表放在 SQLite 中（`jobs` 表：类型、参数、状态、租约、重试次数）。第一阶段单机运行，不引入消息队列。

## 4. 存储

存储分为 git、本地热存储、Ceph、taijifs 四层，存放位置与理由见 [03-data.md](03-data.md) §8。下面是本地热存储根目录（`ALPHASIEVE_HOT_ROOT`，生产环境为 `/data/alphasieve/`）的结构；artifact 与 transcript 写入 Ceph（`ALPHASIEVE_STORE_ROOT`）。

```text
<HOT_ROOT>/
  state/alphasieve.db        SQLite：元数据、ledger、任务表、事件、审批（WAL 模式；每小时备份到 Ceph）
  data/
    raw/tushare/<api>/        原始拉取的本地工作副本（权威副本在 Ceph）
    panel/dev/                开发窗口 panel（agent 可经 CLI 访问）
    panel/holdout/            留出区间 panel（仅 system 角色可读）
    panel/fresh/              前瞻区间 panel（仅 system 角色可读）
  cache/factors/              因子值缓存（库成员与 robust_passed 候选）
  workspaces/<campaign_id>/   每个 campaign 一个 git 仓库，agent 的工作目录

<STORE_ROOT>/（Ceph）
  raw/                        原始数据权威副本
  artifacts/<artifact_id>/    内容寻址的运行产物：manifest.json、metrics.json、图表、日志
  artifacts/turns/            agent transcript
  backups/state/              SQLite 备份
  reports/                    日报与批次报告
```

- **SQLite 表**（主要）：`research_questions`、`data_contracts`、`factor_specs`、`trials`（ledger）、`campaigns`、`turns`、`directives`、`shortlists`、`holdout_requests`、`review_packets`、`decisions`、`promotion_records`、`strategy_specs`、`fresh_cohorts`、`memory_items`、`events`、`jobs`、`users`。
- **ledger 完整性**：`trials` 只允许 INSERT；每行包含前一行哈希，形成哈希链；启动时与定时任务校验链完整。
- **artifact**：目录名为 manifest 内容哈希；manifest 记录代码版本（git commit）、数据快照签名、配置、gate policy 版本、随机种子。
- **为什么不用 Postgres**：第一阶段单机、写入量小，SQLite 足够且零运维；service 层通过仓储接口访问，后续可迁移。

## 5. 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 核心语言 | Python ≥ 3.11 | 量化生态、与 Qlib/LightGBM 兼容 |
| 数据处理 | pandas + pyarrow，重查询用 DuckDB | Parquet 原生；DuckDB 适合在 panel 与 artifact 上做即席查询 |
| schema | pydantic v2 | 契约校验与 JSON Schema 导出（前端类型也由此生成） |
| 模型 | LightGBM / XGBoost / scikit-learn（ridge） | 固定配置基线与参考模型组 |
| 回测 | 自研向量化（B1、B2）+ 自研逐日模拟器（B3–B5） | A 股规则固定，自研便于写不变量测试；用 Qlib 回测交叉验证，见 [13-backtest.md](13-backtest.md) |
| 统计 gate | `deflated-sharpe` + 自研 | DSR、BH-FDR |
| API | FastAPI + uvicorn | 与核心同语言；自动 OpenAPI |
| 前端 | React + TypeScript + Vite；TanStack Query / Router；ECharts；Tailwind + shadcn/ui | 见 [07-frontend.md](07-frontend.md) |
| agent runtime | 本机 Claude Code CLI、Codex CLI；规模化阶段 Nexus Cloud | 不自研，见 [05-agent-harness.md](05-agent-harness.md)、[14-agent-execution.md](14-agent-execution.md) |
| 依赖管理 | uv（Python）、pnpm（前端） | |

## 6. 安全与隔离边界

| 边界 | 实现 |
|---|---|
| 角色 | `ALPHASIEVE_ROLE` ∈ {`agent`, `human`, `system`}；人类通过 API 登录后以 `human` 角色调用；orchestrator 与 worker 为 `system` |
| holdout / fresh 数据 | 独立目录，文件权限只允许 system 运行用户读取；data 层按角色拒绝访问；agent 会话以不同操作系统用户运行（M3 起） |
| 评估与回测代码 | agent 的 cwd 是 campaign workspace，不包含 `src/`；agent 的文件写权限限制在 workspace 内 |
| agent 工具 | Bash 只允许 `alphasieve ...` 与 workspace 内的基础文件命令；禁止网络访问（除 LLM 服务本身） |
| 审批 | 只有 `human` 角色能执行审批类命令；审批记录包含用户、时间、理由、证据快照哈希 |
| 审计 | 所有命令调用写入 `events` 表（角色、命令、参数摘要、结果状态） |

## 7. 与现有设计文档的对应

| 设计文档中的模块 | 实现位置 |
|---|---|
| Workspace | `workspaces/` + `campaigns/` |
| Data Contract Service | `data/` |
| Factor Registry | `factors/` + `factor_specs` 表 |
| Validation / Evaluation / Robustness Service | `gates/`（L0）、`evaluation/`（L1、L2） |
| Review Gate / Promotion Service | `review/` |
| Memory Service | `memory/` |
| Model / Portfolio Service | `models/` + `backtest/` |
