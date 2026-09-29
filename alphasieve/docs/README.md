# AlphaSieve 文档

本目录是 AlphaSieve 的实现文档：要做什么、怎么做、做到什么程度算完成。设计层面的论证（为什么这样做）在仓库的 [`design/`](../../design/README.md)，调研依据在 [`analysis/`](../../analysis/README.md)；本目录只写实现层面的决定，遇到设计问题引用而不重复。

## 文档地图

| 文档 | 回答的问题 |
|---|---|
| [01-product.md](01-product.md) | 做给谁、解决什么问题、核心使用场景、范围与非目标、怎么算成功 |
| [02-architecture.md](02-architecture.md) | 系统由哪些组件组成、进程与存储怎么划分、技术选型、安全边界 |
| [03-data.md](03-data.md) | 用哪些数据、panel 怎么构建、PIT 与可交易性规则、数据区间怎么封存 |
| [04-research-core.md](04-research-core.md) | 研究对象、因子 DSL、评估指标定义、gate 默认阈值、ledger、状态机、回测规则 |
| [05-agent-harness.md](05-agent-harness.md) | agent 循环怎么跑：workspace、turn 生命周期、调度、预算、记忆、可见性 |
| [06-interfaces.md](06-interfaces.md) | JSON CLI 命令、响应信封与错误码、HTTP API、事件流、artifact 布局 |
| [07-frontend.md](07-frontend.md) | 前端做什么、页面与信息架构、关键组件、技术栈 |
| [08-progress-and-interaction.md](08-progress-and-interaction.md) | 如何展示进展、人与 agent 如何交互、审批流、通知 |
| [09-milestones.md](09-milestones.md) | 里程碑、任务分解、验收标准、依赖顺序 |
| [10-decisions.md](10-decisions.md) | 关键决策记录（ADR）与待定问题 |
| [11-factor-search-space.md](11-factor-search-space.md) | agent 挖因子的空间怎么给定：表达能力分级、派生变量库、覆盖坐标、SearchSpace 配置 |
| [12-testing.md](12-testing.md) | 系统测试 T0–T8：不变量测试、数据验证、金标对照、gate 校准、红队测试 |
| [13-backtest.md](13-backtest.md) | 回测 B1–B5：成交规则、成本模型、组合构建、滚动样本外、敏感性检查 |
| [14-agent-execution.md](14-agent-execution.md) | 用哪种 agent 执行：本机 Claude Code / Codex、Cursor、Nexus Cloud 的分工与规则 |
| [15-task-layers.md](15-task-layers.md) | 量化任务怎么分层（信号、模型、组合、执行）、同时预测多少标的、组合问题在哪；AlphaSieve 的覆盖核对与待决定的调整 |
| [16-scaling.md](16-scaling.md) | 扩大算力与数据量：实测瓶颈、评估提速、常驻评估服务与平台 worker、全 A 与更多数据、规模化下的统计纪律 |
| [17-data-vendors.md](17-data-vendors.md) | 更长历史的分钟线、两融、资金流向去哪买、多少钱，推荐方案与接入步骤 |
| [18-mandates.md](18-mandates.md) | 实际量化任务（mandate）：中证 500 增强、业绩超预期漂移、行业 ETF 轮动、股指期货对冲中性的任务书，以及共用的框架改动（方案待确认） |
| [acceptance-m0-m2.md](acceptance-m0-m2.md) | M0–M2 验收记录：真实数据同步结果、测试、端到端评估、gate 校准 |
| [acceptance-scaling.md](acceptance-scaling.md) | 扩容 S-1 至 S-7 验收记录：评估性能、常驻服务、程序化搜索、策略回测、全 A 与事件、日内数据 |
| [acceptance-m3-m4-f1.md](acceptance-m3-m4-f1.md) | M3 / M4 / F1 验收记录：agent 循环、holdout 链路、前端、真实冒烟、试点结果与操作手册 |

## 阅读顺序

- 第一次了解项目：01 → 02 → 09。
- 开始写后端：02 → 03 → 04 → 11 → 13 → 06 → 12。
- 开始写 agent 循环：04 → 11 → 05 → 14 → 06。
- 开始写模型、组合与执行：15 → 13 → 04 §8。
- 开始写前端：01 → 07 → 08 → 06。

## 术语

| 术语 | 含义 |
|---|---|
| Campaign | 一次有预算、有停止条件的研究活动，围绕一个 ResearchQuestion 展开，agent 在其中循环产出候选 |
| Candidate | agent 提交的一个因子候选（`FactorSpec` 的一个版本） |
| Trial | 一次评估，写入 trial ledger 的一条记录 |
| L0–L5 | 分层 verifier：结构约束、开发窗口、样本内稳健、搜索折扣、锁定留出、前瞻验证（见 [design/agent-loop-verification.md](../../design/agent-loop-verification.md)） |
| Evidence tier | 证据等级：`dev` / `holdout` / `fresh`，所有指标都必须带证据等级 |
| Shortlist | 通过 L3、准备打开 holdout 的一批候选，锁定后不可增删 |
| Review Packet | 人工评审材料，汇集一个候选的全部证据 |
| Fresh cohort | 同一批进入前瞻观察的候选，按 cohort 统计 |
