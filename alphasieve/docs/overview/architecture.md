# 系统架构

状态：已实现，按实际部署持续更新。2026-10-07 核对。

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

下表是 2026-10-01 的实际目录（`src/alphasieve/` 下）。最初规划的 `models/`、`review/`、`memory/`、`api/`、`orchestrator/` 没有单独建目录，职责并入了表中对应模块；`backtest/` 只剩空的占位包。

| 组件 | 目录 | 职责 |
|---|---|---|
| contracts | `contracts/` | pydantic 模型：FactorSpec、TrialLedgerEntry、Campaign 等；JSON Schema 导出 |
| data | `data/` | 数据同步（BaoStock、westock、新浪期货）、panel 构建、PIT 规则、可交易性、区间划分与按角色访问、ETF 与期货数据 |
| factors | `factors/` | 因子 DSL、算子库、派生变量、规范化与哈希、因子注册与因子库 |
| evaluation | `evaluation/` | 因子指标、中性化、ridge 边际贡献、模板展开、numba 内核、常驻评估服务（本机 worker 与平台桥接） |
| ledger | `ledger/` | trial ledger（append-only + 哈希链），按层（factor / strategy）与 mandate 计数 |
| gates | `gates/` | L0–L3 gate、gate policy 版本、因子状态机 |
| search_space / search | `search_space/`、`search/` | SearchSpace 与落格；程序化搜索（遗传编程、枚举） |
| campaigns | `campaigns/` | Campaign、Turn、Directive、预算与停止条件；经验记忆；结题、shortlist、因子层 holdout、Review Packet |
| agents | `agents/` | 执行器（本机 Claude Code / Codex）、无人值守 orchestrator 循环、workspace 模板、turn 后完整性检查 |
| strategy | `strategy/` | 执行模拟（T+1、涨跌停、参与率、平方根冲击）、启发式与 LP 组合构建、早期 `strategy backtest` |
| training | `training/` | TrainingTask 配置、样本与残差标签、滚动训练、四个 mandate 的组合与验收、冻结分数来源、策略层 holdout |
| state | `state/` | SQLite 迁移与连接；数据库定时备份 |
| web | `web/` | 只读 FastAPI 与构建好的前端静态文件 |
| cli | `cli/` | JSON CLI（唯一写入入口） |
| 前端工程 | 仓库根目录 `frontend/` | React + Vite + ECharts；构建产物输出到 `web/dist` |
| fresh（规划） | — | 前瞻验证与 paper 追踪，设计见 [forward-paper.md](../mandates/forward-paper.md) |

## 3. 进程模型

常驻进程都由 systemd 管理（`deploy/systemd/`，`deploy/install.sh` 安装）。

| 进程 | 启动方式 | 说明 |
|---|---|---|
| Web | `alphasieve-web.service` → `alphasieve serve` | 只读 FastAPI + 前端静态文件，HTTP Basic 认证；不提供任何写操作 |
| Orchestrator | `alphasieve-orchestrator@<campaign>.service` → `alphasieve orchestrator run` | 每个 campaign 一个实例，文件锁保证单实例；一次一个 agent turn，直到停止条件，然后结题（L3 与 holdout 申请） |
| 评估 worker | `alphasieve-evalworker.service` → `alphasieve evalsvc worker` | 常驻评估服务（D-25），面板常驻内存，从目录队列取请求 |
| 平台桥接 | `alphasieve-evalbridge.service` → `alphasieve evalsvc bridge` | 把评估请求转发给 taijifs 上的平台 Ray worker（D-23） |
| Agent 会话 | 由 orchestrator 以子进程启动 | `claude -p` / `codex exec`，cwd 为 campaign workspace |
| 定时任务 | `alphasieve-daily-update.timer`、`alphasieve-state-backup.timer` | 每日数据更新；每小时数据库备份 |
| CLI | 人或 agent 直接调用 | 唯一写入入口；训练等长任务在前台或 Ray 集群上运行 |

没有 SQLite 任务表，也不引入消息队列：agent turn 由 orchestrator 串行调度，评估请求走评估服务的目录队列。

## 4. 存储

存储分为 git、本地热存储、Ceph、taijifs 四层，存放位置与理由见 [data-and-panels.md](../data/data-and-panels.md) §8。下面是本地热存储根目录（`ALPHASIEVE_HOT_ROOT`，生产环境为 `/data/alphasieve/`）的结构；artifact 与 transcript 写入 Ceph（`ALPHASIEVE_STORE_ROOT`）。

```text
<HOT_ROOT>/
  state/alphasieve.db        SQLite：元数据、ledger、事件、审批（WAL 模式；每小时备份到 Ceph 的 <STORE_ROOT>/backups/state/）
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

- **SQLite 表**（2026-10-01 实际）：`trials`（ledger，factor 与 strategy 两层）、`factor_specs`、`library`、`data_snapshots`、`campaigns`、`turns`、`directives`、`agent_requests`、`memory_items`、`shortlists`、`holdout_requests`（因子层）、`strategy_holdout_requests`（策略层）、`review_packets`、`decisions`、`events`、`schema_version`。原规划的 `research_questions`、`data_contracts`、`promotion_records`、`strategy_specs`、`fresh_cohorts`、`jobs`、`users` 没有建：策略配置以 TrainingTask YAML 加 artifact manifest 冻结，fresh 见 [forward-paper.md](../mandates/forward-paper.md)，web 用单个 Basic 认证凭据。
- **ledger 完整性**：`trials` 只允许 INSERT；每行包含前一行哈希，形成哈希链；`alphasieve ledger verify` 校验，每次备份也会在副本上校验一遍。
- **artifact**：目录名为 manifest 内容哈希；manifest 记录代码版本（git commit）、数据快照签名、配置、gate policy 版本、随机种子。
- **为什么不用 Postgres**：第一阶段单机、写入量小，SQLite 足够且零运维；service 层通过仓储接口访问，后续可迁移。

## 5. 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 核心语言 | Python ≥ 3.11 | 量化生态、与 Qlib/LightGBM 兼容 |
| 数据处理 | pandas + pyarrow，重查询用 DuckDB | Parquet 原生；DuckDB 适合在 panel 与 artifact 上做即席查询 |
| schema | pydantic v2 | 契约校验与 JSON Schema 导出（前端类型也由此生成） |
| 模型 | LightGBM / XGBoost / scikit-learn（ridge） | 固定配置基线与参考模型组 |
| 回测 | 自研向量化（B1、B2）+ 自研逐日模拟器（B3–B5） | A 股规则固定，自研便于写不变量测试；用 Qlib 回测交叉验证，见 [backtest.md](../research/backtest.md) |
| 统计 gate | `deflated-sharpe` + 自研 | DSR、BH-FDR |
| API | FastAPI + uvicorn | 与核心同语言；自动 OpenAPI |
| 前端 | React + TypeScript + Vite；TanStack Query / Router；ECharts；Tailwind + shadcn/ui | 见 [frontend.md](../interfaces/frontend.md) |
| agent runtime | 本机 Claude Code CLI、Codex CLI；规模化阶段 Nexus Cloud | 不自研，见 [agent-harness.md](../agent/agent-harness.md)、[agent-execution.md](../agent/agent-execution.md) |
| 依赖管理 | uv（Python）、pnpm（前端） | |

## 6. 安全与隔离边界

| 边界 | 实现 |
|---|---|
| 角色 | `ALPHASIEVE_ROLE` ∈ {`agent`, `human`, `system`}；人类在本机 CLI 以 `human` 角色执行审批（web 只读）；orchestrator 与 worker 为 `system` |
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
| Review Gate / Promotion Service | `campaigns/`（结题、Review Packet、因子层 holdout）、`training/holdout.py`（策略层 holdout）；审批是 human 角色的 CLI 命令 |
| Memory Service | `campaigns/memory.py` |
| Model / Portfolio Service | `training/`（模型、mandate 组合与验收）+ `strategy/`（执行与 LP） |
| Forward / Paper Service | 未实现，设计见 [forward-paper.md](../mandates/forward-paper.md) |
