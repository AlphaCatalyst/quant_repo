# 08 · 进展展示与交互

## Part A · 进展

### 1. 什么算进展

在 AlphaSieve 里，进展不是“回测收益变高了”，而是：

| 层级 | 进展的含义 | 主要指标 |
|---|---|---|
| Campaign | 候选在筛子中往下走了多少，预算花得值不值 | 各层通过数、每 100 trial 产出的 `robust_passed` 数、停止条件进度 |
| 批次 | 锁定的 shortlist 在 holdout 上存活了多少 | holdout 通过率、每次 holdout 读取产出的通过数 |
| 因子库 | 库是否在正交地变大 | 成员数、平均两两相关、族覆盖 |
| 前瞻 | 通过验证的因子在新数据上是否仍然有效 | cohort 累计超额、`fresh_supported` 数 |
| 知识 | 我们排除了哪些错误方向 | 证伪假设数、禁区条目数、失败原因分布 |

“被证伪”计入进展：它说明 verifier 在工作，并且缩小了后续搜索空间。

### 2. 核心可视化与计算口径

**筛选漏斗**
- 每一层的数量 = 到达过该层（含通过与失败）的候选版本数。
- 每层可展开：失败原因分布（例如 L1 中“与库高相关”占 46%、“ICIR 不足”占 38%）。
- 全局漏斗按时间窗口汇总；campaign 漏斗只算本 campaign。

**搜索强度曲线**
- 横轴：本 campaign 累计 completed dev trial 数 N。
- 线 1：截至第 N 次 trial 的最佳 dev 指标（ICIR 或多头超额 Sharpe）。
- 线 2：在当前 N 与 trial 指标方差下，DSR 显著所需的门槛。
- 解读：线 2 在线 1 之上，说明当前最佳结果不能排除是运气；这是提醒人“不要被最佳值打动”的主要手段（思路来自 honest evaluation 论文的 evaporation curve）。

**预算消耗**
- 每类预算的已用 / 总量，以及按近 6 小时速率外推的耗尽时间。
- holdout 读取预算按 campaign 与全局分别展示。

**搜索空间覆盖矩阵**
- 格子 = 数据域 × 变换形态 × 时间尺度（见 [11-factor-search-space.md](11-factor-search-space.md) §4）。
- 每格：尝试数、L2 通过数、入库数、是否禁区；颜色表示“未探索 / 探索中 / 有产出 / 饱和”。
- 用于回答“还有哪些方向没试过”“哪些方向已经挖透”。

**因子库健康度**
- 成员数随时间变化；平均两两相关随时间变化；族分布；相关性热力图。

**前瞻 cohort**
- 横轴：自 cohort 锁定以来的交易日；纵轴：相对基准的累计超额。
- 分池对比：机器（因子 / 模型）、人工、LLM 主观信号并列。
- 标注最短观察期（默认 60 日）与当前统计显著性。

### 3. 停滞诊断

前端在 campaign 进展页给出自动诊断，帮助人判断要不要干预：

| 症状 | 可能原因 | 建议动作 |
|---|---|---|
| L1 失败主要是“与库高相关” | agent 在已被覆盖的族里打转 | 查看禁区；下 `forbid` 指令，或 `prioritize` 新族 |
| L1 失败主要是“ICIR 不足”，且候选高度相似 | 方向本身无效 | 下 `hint` 换方向，或结束 campaign |
| 大量重复哈希 | agent 在刷提交 | 检查 program.md；暂停并调整 |
| L2 失败集中在“中性化后消失” | 信号主要是行业或市值暴露 | `hint`：要求候选内置中性化 |
| turn 费用高但 trial 少 | agent 在读数据、写笔记，评估少 | 调低 turn 时长或提示先评估 |
| 连续 turn 失败 | 环境或权限问题 | 查看 transcript；campaign 已自动暂停 |

诊断规则写在后端（`campaigns/diagnostics.py`），结果随 campaign read model 返回。

### 4. 定期摘要

- **日报**（每日 09:00 生成）：昨日各 campaign 的漏斗变化、预算消耗、新增待办、证伪的主要方向、fresh cohort 当日变化、系统告警。
- **批次报告**（campaign 结束时生成）：本批次尝试的假设、通过与淘汰情况、搜索强度曲线、shortlist 与 holdout 结果（仅 human 可见）。
- 摘要以 artifact 保存，前端可查看，也可推送到企业微信（见 §8）。
- 摘要中的数字规则与前端一致：带证据等级、带 trial 数、不单独报最佳收益。

## Part B · 交互

### 5. 原则

1. **决定结构化，不靠聊天**：审批、评审、回复都是带类型和必填理由的表单，结果推进状态机并留痕。自由文本只用于指令内容和评论。
2. **agent 异步工作，人不阻塞内环**：内环不需要人参与；只有 holdout 打开、评审、进入 paper 这些节点需要人。
3. **每个 UI 动作都有 CLI 等价物**：人可以只用终端完成全部操作，也可以让另一个 agent 以 human 身份代办（需凭据，并在审计中标注）。
4. **不泄漏**：人给 agent 的任何信息都不能包含 holdout / fresh 结果。

### 6. 人 → agent：指令（Directive）

| 类型 | 语义 | 示例 |
|---|---|---|
| `prioritize` | 优先探索某方向 | “优先尝试与流动性无关的反转构造” |
| `forbid` | 禁止某方向，写入 campaign 禁区 | “暂停换手率族” |
| `hint` | 提供思路或约束 | “候选需内置行业中性化” |
| `answer` | 回复 agent 的请求 | “不提供分钟数据，第一阶段只用日频” |

生命周期：`pending` → 在下一个 turn 开始时写入 `directives.md` → turn 结束后标记 `consumed`，记录消费它的 turn。`forbid` 类指令同时写入 campaign 记忆，持续生效直到被撤销。

防泄漏：指令提交时，后端检查内容是否引用了 holdout / fresh 对象（例如直接粘贴 Review Packet 中的指标、提及某候选“holdout 失败”），命中则拒绝并提示原因。这是辅助手段，最终依赖使用规范。

### 7. agent → 人：请求（AgentRequest）

| 类型 | 何时使用 | 人的处理 |
|---|---|---|
| `data` | 需要新字段或新数据源 | 批准（排期接入）/ 拒绝，附理由 |
| `question` | 研究问题不清、约束冲突 | 回复（生成 `answer` 指令） |
| `scope` | 想超出 campaign 设定（例如换股票池） | 批准会生成新 campaign 草稿；拒绝附理由 |

agent 提交请求后继续按原计划工作，不等待回复。

### 8. 审批流程

**HoldoutRequest**

```text
campaign 停止 → orchestrator 跑 L3 → 锁定 shortlist、冻结记忆 → 创建 HoldoutRequest
  → 审批中心出现待办（通知 Approver）
  → Approver 查看：shortlist 成员、dev 证据、trial 数、DSR、剩余 holdout 预算
  → 批准：system 执行 holdout 评估 → 结果写入各候选 Review Packet → 通过者进入 reviewable
  → 拒绝：shortlist 作废，候选保持 ledger_gated，理由记入时间线
```

**Review Packet 评审**

```text
reviewable → Approver 在评审页选择决定并填写理由
  approved_for_shadow → 进入影子特征与滚动重训流水线 → fresh_observing
  needs_repair        → 生成修复任务（新版本需重新走 L0–L3，且不能复用同一段 holdout）
  rejected            → 终态；原因进入全局证伪记录（不进入 agent 记忆）
```

**进入 paper**

```text
fresh_supported → 审批中心出现待办 → Approver 审阅 cohort 表现与 regime 状态
  → approved_for_paper：加入 paper 组合（StrategySpec 更新需同时审批）
  → 继续观察 / retired
```

**时效与提醒**：待办超过 24 小时未处理，日报高亮；超过 72 小时再次通知。HoldoutRequest 等待期间 campaign 处于 `awaiting_holdout_approval`，不影响其他 campaign 运行。

### 9. 控制操作的语义

| 操作 | 语义 |
|---|---|
| 暂停 campaign | 当前 turn 正常结束后不再调度新 turn；已提交的评估继续完成 |
| 停止 campaign | 同暂停，并立即进入 `concluding`（跑 L3、锁 shortlist） |
| 恢复 campaign | 仅限 `paused` 状态；已 `concluding` 的不能恢复内环 |
| 取消 turn | 终止 agent 子进程；已写入的 trial 保留 |
| 修改预算 | 只能增加，记录理由；减少预算等价于停止条件提前 |

### 10. 可见性矩阵

| 信息 | agent | human | 说明 |
|---|---|---|---|
| dev 指标与 gate 结果 | ✓ | ✓ | |
| L3 结果（是否进入 shortlist） | ✓（仅结论） | ✓ | 基于 dev 数据 |
| holdout 指标与结论 | ✗ | ✓ | agent 只知道“批次已结束” |
| fresh 指标与结论 | ✗ | ✓ | |
| 评审决定与理由 | ✗ | ✓ | |
| 人工评论 | 仅标记“可共享”的 | ✓ | |
| 其他 campaign 的候选 | 仅已入库因子的定义与 dev 指标 | ✓ | 用于相关性与禁区 |

### 11. 通知

- 渠道：企业微信机器人 webhook（可配置多个）；邮件可选。
- 默认推送：日报、新待办、系统告警（数据更新失败、ledger 校验失败、campaign 自动暂停）。
- 消息只包含摘要与前端链接，不包含 holdout / fresh 具体指标。
