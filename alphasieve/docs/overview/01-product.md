# 01 · 产品定义

## 1. 一句话

AlphaSieve 让 code agent 在 A 股日频截面上持续挖掘和迭代因子，用分层 verifier 逐级筛选，人只在研究方向、验证预算和资金上做决定。

## 2. 要解决的问题

| 问题 | 现状 | AlphaSieve 的做法 |
|---|---|---|
| agent 能大量产出候选，但产出不等于有效 | 结果靠单次回测判断，容易被运气和数据窥探骗 | 分层 verifier + trial ledger 折扣搜索强度 |
| 历史数据被反复看过 | holdout 名存实亡 | holdout 物理隔离、读取计预算、污染即作废 |
| 研究过程不可追溯 | notebook 和聊天记录散落 | 每个候选、每次评估、每个决定都有记录和 artifact |
| 人不知道 agent 在做什么、做到哪一步 | 只能看最终结果 | 前端实时展示筛选漏斗、搜索强度、预算、agent 活动 |
| 人的决定混在对话里 | 审批无记录、无上下文 | 结构化审批中心，每个决定附证据与理由 |

## 3. 用户与角色

| 角色 | 典型身份 | 做什么 | 不做什么 |
|---|---|---|---|
| Researcher | 量化研究员 | 创建 campaign、定研究方向与数据契约、给 agent 下指令、写评审意见 | 批准自己 campaign 的 holdout（建议分离） |
| Approver | 研究负责人 / 风控 | 批准 holdout 打开、评审 Review Packet、批准进入 paper | 直接修改候选或指标 |
| Agent（Miner） | Claude Code / Codex 会话 | 提假设、写因子、诊断失败、写报告初稿 | 读 holdout/fresh、改评估代码、做任何审批 |
| Agent（Reviewer，可选） | 独立会话 | 对 Review Packet 做对抗式审查，意见仅供参考 | 写入 Miner 的记忆、改变状态 |
| System | orchestrator / scheduler | 调度 turn、执行 gate、计预算、跑定时任务 | 决定研究方向 |

早期团队规模小，Researcher 与 Approver 可以是同一人，但系统保留两种角色，审批动作以 Approver 身份记录。

## 4. 核心场景

**S1 创建并启动一个 campaign**
Researcher 在前端新建 campaign：研究问题“A 股量价反转类因子”、股票池中证 1000、预测周期 5 日、trial 预算 500、最长 3 天、连续 30 个 turn 无改进即停。启动后 orchestrator 开始调度 agent turn。

**S2 第二天早上看进展**
Researcher 打开 campaign 页：筛选漏斗显示 L0 提交 212、L1 通过 41、L2 通过 9、L3 通过 3；搜索强度曲线显示最佳 dev RankICIR 随 trial 数上升，DSR 门槛上升得更快；agent 活动流显示当前在尝试“成交量加权的短期反转”，并列出最近被证伪的方向。

**S3 引导 agent**
Researcher 发现 agent 反复在“换手率”族打转，下达指令“暂停换手率族，优先尝试与流动性无关的反转构造”。指令以结构化 directive 进入下一个 turn，并记入 campaign 时间线。

**S4 批准打开 holdout**
campaign 达到停止条件后，orchestrator 锁定 3 个候选的 shortlist，向审批中心提交 HoldoutRequest。Approver 查看 shortlist 的 dev 证据与 trial 数，批准打开；holdout 评估由后端执行，结果只进入 Review Packet。

**S5 评审**
Approver 打开 Review Packet：dev / holdout 指标分列、参考模型组上的边际贡献、与因子库相关性、行业与市值暴露、trial 数与 DSR。决定 1 个 `approved_for_shadow`、2 个 `rejected`，写明理由。

**S6 前瞻观察**
通过的因子进入 fresh cohort，每天自动更新。前端展示 cohort 相对基准的累计超额，以及机器/人/AI 各信号池的对比。满 60 个交易日后，系统给出 `fresh_supported` 或 `fresh_failed`，是否进入 paper 由 Approver 决定。

**S7 agent 请求**
agent 认为需要分钟级成交数据验证一个假设，提交 DataRequest。Researcher 在审批中心拒绝（超出第一阶段范围），理由回写给 agent。

## 5. 范围

第一阶段做：

- A 股日频截面因子研究，股票池沪深 300 / 中证 500 / 中证 1000。
- agent 自主循环的对象只有因子候选；下游模型固定配置、随因子库滚动重训。
- L0–L4 完整链路，L5 前瞻观察与指数增强组合的 paper 追踪。
- 前端：进展展示、审批中心、Review Packet、因子库、ledger 浏览。

第一阶段不做：

- 实盘下单与 broker 接入。
- 分钟级 / 日内数据与策略。
- 模型设计的自主循环、事件驱动策略、行业轮动（按 [design/strategy-scope.md](../../../design/strategy-scope.md) 后续引入）。
- 多租户与复杂权限。
- 自研 agent runtime（使用 Claude Code / Codex）。

## 6. 成功标准

系统层（第一阶段必须达成）：

| 指标 | 目标 |
|---|---|
| ledger 完整性 | 100% 的评估经唯一入口入账；有测试证明无法绕过 |
| holdout 隔离 | agent 进程无法读取 holdout/fresh 数据；有测试与审计日志证明 |
| 可复现 | 任一 artifact 可由其 manifest 重跑得到相同指标 |
| 评估正确性 | 评估不变量测试覆盖“指标只在声明区间计算”“标签不前视”等 |
| 可观察 | 人能在前端 1 分钟内回答“这个 campaign 进行到哪、花了多少预算、为什么没进展” |

研究层（按季度观察，不设硬目标）：

- 每单位 holdout 预算产出的 `holdout_passed` 与 `fresh_supported` 因子数。
- 因子库规模与平均两两相关性（正交增长）。
- 被证伪的假设数与原因分布。

明确不作为成功标准：回测收益率、单次 Sharpe、样本内最佳 IC。
