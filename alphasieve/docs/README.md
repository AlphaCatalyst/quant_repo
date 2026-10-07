# AlphaSieve 文档

本目录是 AlphaSieve 的实现文档：要做什么、怎么做、做到什么程度算完成。设计层面的论证（为什么这样做）在仓库的 [`design/`](../../design/README.md)，调研依据在 [`analysis/`](../../analysis/README.md)；本目录只写实现层面的决定，遇到设计问题引用而不重复。

## 文档地图

文档按层放在子目录里：`overview/`、`data/`、`research/`、`agent/`、`interfaces/`、`mandates/`、`personal/`、`acceptance/`。文件名不带编号；正文里的“mandate-specs §3”“docs/mandates/mandate-specs §3”指对应文档的章节。早期产物里的编号写法（如“docs/18 §3”）按文末的[旧编号对照](#旧编号对照)查找。未关闭的事项见 [TODO.md](TODO.md)。

### 总览与决策（`overview/`）

做什么、整体怎么搭、做到哪一步、已定与待定的事。

| 文档 | 回答的问题 |
|---|---|
| [product.md](overview/product.md) | 做给谁、解决什么问题、核心使用场景、范围与非目标、怎么算成功 |
| [architecture.md](overview/architecture.md) | 系统由哪些组件组成、进程与存储怎么划分、技术选型、安全边界 |
| [milestones.md](overview/milestones.md) | 里程碑、任务分解、验收标准、依赖顺序 |
| [decisions.md](overview/decisions.md) | 关键决策记录（ADR）与待定问题 |
| [TODO.md](TODO.md) | 未关闭事项汇总：待人工决定、立项后待实现、文档维护；每条注明出处 |

### 平台 · 数据（`data/`）

数据来源、panel、扩容与采购、公告与财报排雷。

| 文档 | 回答的问题 |
|---|---|
| [data-and-panels.md](data/data-and-panels.md) | 用哪些数据、panel 怎么构建、PIT 与可交易性规则、数据区间怎么封存 |
| [scaling.md](data/scaling.md) | 扩大算力与数据量：实测瓶颈、评估提速、常驻评估服务与平台 worker、全 A 与更多数据、规模化下的统计纪律 |
| [data-vendors.md](data/data-vendors.md) | 更长历史的分钟线、两融、资金流向去哪买、多少钱，推荐方案与接入步骤 |
| [financial-red-flags.md](data/financial-red-flags.md) | 财报排雷：9 条规则（应收、存货、利润现金背离、应计、商誉、其他应收、预付、存贷双高、毛利率异常）、公告日时点口径、不可用规则与数据限制 |
| [announcements.md](data/announcements.md) | 巨潮公告：接口与字段、事件分类与重要性、PDF 原文与页内定位、时间口径与许可说明 |
| [sw-industry-sensitivity.md](data/sw-industry-sensitivity.md) | 申万行业口径敏感性：已保存 A 组合在证监会与申万历史行业下的行业偏离、行业中性化因子诊断，以及默认口径建议 |

### 平台 · 研究核心与回测（`research/`）

因子评估、关卡、搜索空间、测试与回测规则。

| 文档 | 回答的问题 |
|---|---|
| [research-core.md](research/research-core.md) | 研究对象、因子 DSL、评估指标定义、gate 默认阈值、ledger、状态机、回测规则 |
| [factor-search-space.md](research/factor-search-space.md) | agent 挖因子的空间怎么给定：表达能力分级、派生变量库、覆盖坐标、SearchSpace 配置 |
| [testing.md](research/testing.md) | 系统测试 T0–T8：不变量测试、数据验证、金标对照、gate 校准、红队测试 |
| [backtest.md](research/backtest.md) | 回测 B1–B5：成交规则、成本模型、组合构建、滚动样本外、敏感性检查 |
| [task-layers.md](research/task-layers.md) | 量化任务怎么分层（信号、模型、组合、执行）、同时预测多少标的、组合问题在哪；AlphaSieve 的覆盖核对与待决定的调整 |

### 平台 · agent（`agent/`）

agent 循环与执行方式。

| 文档 | 回答的问题 |
|---|---|
| [agent-harness.md](agent/agent-harness.md) | agent 循环怎么跑：workspace、turn 生命周期、调度、预算、记忆、可见性 |
| [agent-execution.md](agent/agent-execution.md) | 用哪种 agent 执行：本机 Claude Code / Codex、Cursor、Nexus Cloud 的分工与规则 |

### 平台 · 接口、前端与运维（`interfaces/`）

CLI 与 HTTP、看板、交互与审批、监控、控制面。

| 文档 | 回答的问题 |
|---|---|
| [cli-and-api.md](interfaces/cli-and-api.md) | JSON CLI 命令、响应信封与错误码、HTTP API、事件流、artifact 布局 |
| [frontend.md](interfaces/frontend.md) | 前端做什么、页面与信息架构、关键组件、技术栈 |
| [progress-and-interaction.md](interfaces/progress-and-interaction.md) | 如何展示进展、人与 agent 如何交互、审批流、通知 |
| [monitoring.md](interfaces/monitoring.md) | 监控告警 v1：输入、规则、级别、公开与私有可见性、日更调度及限制 |
| [control-plane.md](interfaces/control-plane.md) | 控制面（已实现）：Ray/orbenchtest/codex-lb 断联的分类与续跑、通用作业层、发布目录与系统健康、新需求接入单与 skill |

### 机构型量化任务（mandate）（`mandates/`）

A–D 四个任务书、训练、组合构建、风险模型、前瞻验证。

| 文档 | 回答的问题 |
|---|---|
| [mandate-specs.md](mandates/mandate-specs.md) | 实际量化任务（mandate）：中证 500 增强、业绩超预期漂移、行业 ETF 轮动、股指期货对冲中性的任务书，以及共用的框架改动（已实现，见 acceptance-training.md） |
| [training-tasks.md](mandates/training-tasks.md) | 四个 mandate 的模型训练任务：标签、样本、特征、滚动验证、模型、配置和平台执行 |
| [training-round2.md](mandates/training-round2.md) | 第二轮训练任务：C 事件分数接入 A、因子库扩充、ETF 行业映射轮动、真实期货与基差对冲 |
| [a-portfolio.md](mandates/a-portfolio.md) | A 的组合构建：冻结 v4 分数、成本与容量诊断、四个预注册配置、N≤13 预算和停止规则 |
| [a-cost-aware.md](mandates/a-cost-aware.md) | A 的成本感知续研：因果收益尺度与 20 亿冲击定价、两个固定配置、N≤15 预算和停止规则 |
| [mandate-campaigns.md](mandates/mandate-campaigns.md) | 从属于 mandate 的因子 campaign 设计：冻结 A 基线、对齐 A 的 ridge 边际贡献、5 个一批进入 A 的规则 |
| [risk-model.md](mandates/risk-model.md) | 组合层风险模型 v1 设计：六个风格暴露、固定参数因子协方差与事前 TE 校验、实际持仓闭环；暂不引入 QP |
| [forward-paper.md](mandates/forward-paper.md) | 前瞻验证与 paper 追踪设计：只追加的 fresh 日分区、锁定模型的前向重拟合、观察账簿、统计判定与人工审批 |

### 个人账户（`personal/`）

个人体量的约束与方向、广义平台、覆盖缺口、决策与选股任务。

| 文档 | 回答的问题 |
|---|---|
| [personal-account.md](personal/personal-account.md) | 100 万–500 万个人账户：体量带来的约束与优势、可做的策略方向、上限与合理预期、对 AlphaSieve 的调整建议 |
| [broad-quant-platform.md](personal/broad-quant-platform.md) | 广义量化分析平台构想（50 万–1000 万个人持仓）：论点研究与可结算预测、信息与事件处理、持仓风控、特殊情况、决策复盘等可结合 agent 的能力与优先级 |
| [platform-implementation.md](personal/platform-implementation.md) | 广义平台的实现评估：现有代码的可复用度、两处前置重构（哈希链公共函数、agent profile）、快速可做项与中长期项；R-1、R-2、Q1–Q6 已实现 |
| [coverage-review.md](personal/coverage-review.md) | 覆盖评估：第四轮数据完成后已覆盖与缺失的能力（持仓时间序列与归因、公告排雷、监控提醒、资产范围、基本面工具、执行接入）、明确不做的方向与建议的下一步 |
| [personal-decision-tasks.md](personal/personal-decision-tasks.md) | 个人股票账户的决策与选股任务（草案）：下行预警、换仓门槛、仓位与再平衡带、事件响应、决策质量度量、周期定位、候选发现；合成账户与同频随机基线；预期与对挖因子、全 A 数据的取舍 |

### 验收记录（`acceptance/`）

各阶段的实测结果与阻塞项。

| 文档 | 回答的问题 |
|---|---|
| [acceptance-m0-m2.md](acceptance/acceptance-m0-m2.md) | M0–M2 验收记录：真实数据同步结果、测试、端到端评估、gate 校准 |
| [acceptance-m3-m4-f1.md](acceptance/acceptance-m3-m4-f1.md) | M3 / M4 / F1 验收记录：agent 循环、holdout 链路、前端、真实冒烟、试点结果与操作手册 |
| [acceptance-scaling.md](acceptance/acceptance-scaling.md) | 扩容 S-1 至 S-7 验收记录：评估性能、常驻服务、程序化搜索、策略回测、全 A 与事件、日内数据 |
| [acceptance-training.md](acceptance/acceptance-training.md) | 训练任务与四个 mandate 的 dev 验收记录：结果、作废的 trial、平台实测、阻塞项 |

## 阅读顺序

- 第一次了解项目：product → architecture → milestones。
- 开始写后端：architecture → data-and-panels → research-core → factor-search-space → backtest → cli-and-api → testing。
- 开始写 agent 循环：research-core → factor-search-space → agent-harness → agent-execution → cli-and-api。
- 开始写模型、组合与执行：task-layers → backtest → research-core §8。
- 开始写前端：product → frontend → progress-and-interaction → cli-and-api。
- 个人账户方向：personal-account → broad-quant-platform → coverage-review → personal-decision-tasks；已实现部分见 platform-implementation §9。

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

## 旧编号对照

文件名在 2026-10-07 去掉了编号前缀。训练任务 YAML 的描述（改动会改变 `config_hash`）、ledger 与 gate 等受保护模块的注释、已有提交信息和产物里仍是“docs/NN §x”写法，按下表查找。

| 旧编号 | 现路径 |
|---|---|
| 01 | [overview/product.md](overview/product.md) |
| 02 | [overview/architecture.md](overview/architecture.md) |
| 03 | [data/data-and-panels.md](data/data-and-panels.md) |
| 04 | [research/research-core.md](research/research-core.md) |
| 05 | [agent/agent-harness.md](agent/agent-harness.md) |
| 06 | [interfaces/cli-and-api.md](interfaces/cli-and-api.md) |
| 07 | [interfaces/frontend.md](interfaces/frontend.md) |
| 08 | [interfaces/progress-and-interaction.md](interfaces/progress-and-interaction.md) |
| 09 | [overview/milestones.md](overview/milestones.md) |
| 10 | [overview/decisions.md](overview/decisions.md) |
| 11 | [research/factor-search-space.md](research/factor-search-space.md) |
| 12 | [research/testing.md](research/testing.md) |
| 13 | [research/backtest.md](research/backtest.md) |
| 14 | [agent/agent-execution.md](agent/agent-execution.md) |
| 15 | [research/task-layers.md](research/task-layers.md) |
| 16 | [data/scaling.md](data/scaling.md) |
| 17 | [data/data-vendors.md](data/data-vendors.md) |
| 18 | [mandates/mandate-specs.md](mandates/mandate-specs.md) |
| 19 | [mandates/training-tasks.md](mandates/training-tasks.md) |
| 20 | [mandates/training-round2.md](mandates/training-round2.md) |
| 21 | [mandates/a-portfolio.md](mandates/a-portfolio.md) |
| 22 | [mandates/a-cost-aware.md](mandates/a-cost-aware.md) |
| 23 | [mandates/forward-paper.md](mandates/forward-paper.md) |
| 24 | [mandates/mandate-campaigns.md](mandates/mandate-campaigns.md) |
| 25 | [mandates/risk-model.md](mandates/risk-model.md) |
| 26 | [personal/personal-account.md](personal/personal-account.md) |
| 27 | [personal/broad-quant-platform.md](personal/broad-quant-platform.md) |
| 28 | [personal/platform-implementation.md](personal/platform-implementation.md) |
| 29 | [personal/coverage-review.md](personal/coverage-review.md) |
| 30 | [data/financial-red-flags.md](data/financial-red-flags.md) |
| 31 | [data/announcements.md](data/announcements.md) |
| 32 | [data/sw-industry-sensitivity.md](data/sw-industry-sensitivity.md) |
| 33 | [interfaces/monitoring.md](interfaces/monitoring.md) |
| 34 | [interfaces/control-plane.md](interfaces/control-plane.md) |
| 35 | [personal/personal-decision-tasks.md](personal/personal-decision-tasks.md) |
