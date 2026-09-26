# 05 · Agent Harness

本文件定义 agent 循环怎么跑。原则来自 [design/agent-loop-verification.md](../../design/agent-loop-verification.md)：agent 只提案、确定性后端裁决；agent 只在 dev 窗口（L0–L2）自由循环；L3–L5 按预算执行，结果对 agent 不可见。

## 1. 不自研 agent runtime

- 使用现成的 code agent：Claude Code（`claude -p`）与 Codex（`codex exec`），通过适配器接入。
- 适配器只负责：组装 prompt、启动子进程、限制工具、收集 transcript、解析结果。它不持有任何研究真值。
- 适配器接口：

```python
class AgentAdapter(Protocol):
    name: str
    def run_turn(self, turn: TurnContext) -> TurnResult: ...
```

`TurnContext` 包含 workspace 路径、prompt、允许的工具、超时、模型；`TurnResult` 包含退出状态、transcript 路径、token 与费用、agent 自述的摘要。

执行后端的选型（本机 Claude Code / Codex、Cursor、Nexus Cloud）与上云时的 ledger 规则见 [14-agent-execution.md](14-agent-execution.md)；agent 可探索的因子空间见 [11-factor-search-space.md](11-factor-search-space.md)。

## 2. Campaign Workspace

每个 campaign 在 `workspaces/<campaign_id>/` 下有一个独立 git 仓库，是 agent 的 cwd：

```text
workspaces/<campaign_id>/
  program.md            本 campaign 的研究规程（由模板 + campaign 参数生成，agent 只读）
  brief.md              研究问题、股票池、周期、预算与剩余预算（每个 turn 前刷新，只读）
  memory.md             经验记忆摘要（每个 turn 前刷新，只读）
  directives.md         尚未消费的人工指令（每个 turn 前刷新，只读）
  candidates/           agent 编写的因子候选（YAML，FactorSpec 格式）
  notes/                agent 的研究笔记与假设记录
  reports/              agent 撰写的阶段报告
  .claude/ 或 .codex/   工具权限配置（由 orchestrator 写入）
```

- agent 在 `candidates/` 写 YAML，然后调用 `alphasieve factor eval candidates/xxx.yaml --json` 提交评估。
- orchestrator 在每个 turn 结束后自动提交 git commit，trial 记录中带上 commit hash。
- workspace 中没有任何数据文件和评估代码；数据只能通过 CLI 查询（`alphasieve data sample`、`data describe`，只返回 dev 区间）。

## 3. program.md 模板要点

program.md 相当于 agent 的研究 SOP，由 `templates/program.md.j2` 渲染，内容包括：

1. 目标：在给定股票池和周期上，找到能通过 L0–L2、且与因子库低相关的因子；被证伪也是有效产出。
2. 工作循环：读 brief、memory、directives → 提出假设（写入 notes）→ 写候选 YAML → `factor eval` → 读结果 → 修复或换方向 → 在报告中总结。
3. 约束：只能使用 DSL 白名单；参数先用默认值（default-first），失败时只允许单参数邻域调整；每个假设最多提交 N 个变体。
4. 禁止事项：不得尝试读取 holdout/fresh；不得修改 workspace 之外的文件；不得重复提交同一哈希以“刷”结果（重复提交也计入 trial）。
5. 每个 turn 结束时在 `reports/turn-<n>.md` 写三段：本轮尝试、结论（成立 / 证伪 / 待定）、下一步建议。

## 4. 工具权限

| 工具 | 允许范围 |
|---|---|
| 文件读写 | 仅 workspace 内；`program.md`、`brief.md`、`memory.md`、`directives.md` 只读 |
| Bash | 只允许 `alphasieve` 命令，以及 `ls`、`cat`、`git log`、`git diff` 等只读命令 |
| 网络 | 禁止（LLM 服务连接除外） |
| MCP | 第一阶段不启用；后续如启用，只暴露与 CLI 相同的只读与评估工具 |

agent 进程环境：`ALPHASIEVE_ROLE=agent`、`ALPHASIEVE_CAMPAIGN=<id>`，以独立的操作系统用户运行，该用户对 `data/panel/holdout`、`data/panel/fresh`、`state/`、`src/` 无读取权限（CLI 通过本地 service 访问数据库）。

## 5. Turn 生命周期

```text
orchestrator 选择下一个要推进的 campaign
  → 检查预算与停止条件（不满足则结束 campaign）
  → 刷新 brief.md / memory.md / directives.md
  → 创建 Turn 记录，写入 events
  → adapter.run_turn()（超时默认 30 分钟）
      agent 在 workspace 中写候选、调用 CLI 评估（每次评估都实时写 ledger 与事件）
  → 收集 transcript（JSONL）与费用
  → git commit workspace
  → 标记已消费的 directives
  → 更新 Turn 记录与 campaign 统计
  → 判断是否需要批次 gate
```

turn 的实时输出（agent 的工具调用、评估结果）通过事件流推送给前端，见 [06-interfaces.md](06-interfaces.md)。

## 6. 调度与三层节奏

| 循环 | 触发 | 内容 |
|---|---|---|
| 内环 | orchestrator 连续调度 turn | agent 在 dev 窗口迭代候选（L0–L2） |
| 中环 | campaign 达到停止条件，或 human 手动收束 | 对 `robust_passed` 候选跑 L3 → 锁定 shortlist → 冻结记忆 → 提交 HoldoutRequest |
| 外环 | 定时任务（每个交易日） | fresh cohort 更新；每月模型滚动重训 |

多个 campaign 并行时，orchestrator 按轮转调度，同一时间运行的 agent 会话数有上限（默认 2），避免 LLM 费用失控。

### 停止条件（任一满足即停止内环）

- trial 预算耗尽（默认 500）。
- turn 预算耗尽，或 LLM 费用预算耗尽。
- 最长运行时间到达（默认 72 小时）。
- 连续 K 个 turn（默认 20）没有新的 `robust_passed` 候选。
- human 手动停止。

停止后 campaign 进入 `concluding`，中环流程执行完进入 `awaiting_holdout_approval`，holdout 评估完成后进入 `concluded`。

## 7. 记忆

| 记忆类型 | 来源 | 写入时机 | 对 agent 可见 |
|---|---|---|---|
| 成功模板 | 通过 L2 的候选的结构模式 | 每个 turn 后由 system 提炼 | 是 |
| 禁区 | 与因子库高相关的因子族、反复 L1 失败的构造 | 每个 turn 后由 system 提炼 | 是 |
| 研究洞察 | agent 报告中的结论（经 system 结构化） | 每个 turn 后 | 是 |
| holdout 结果 | L4 | — | **否**，永不写入 agent 记忆 |
| fresh 结果 | L5 | — | **否** |
| 人工备注 | human 在因子或 campaign 上的评论 | 随时 | 仅当 human 显式标记为“可共享给 agent” |

- 记忆按作用域分层：全局（跨 campaign）与 campaign 内。
- shortlist 锁定时冻结该 campaign 的记忆快照（`memory_frozen_at`），之后的 holdout 过程不影响任何记忆。
- 被 holdout 淘汰的候选，在 agent 看来只是“该批次已结束”，不透露原因；这是为了防止 holdout 信息经记忆回流。

## 8. Reviewer agent（可选，M4 之后）

- 独立会话、独立 workspace，输入是 Review Packet（包含 holdout 结果），输出是一份审查意见，附在 Review Packet 上。
- 与 Miner agent 不共享记忆、不共享 workspace，意见只供 human 参考，不改变任何状态。

## 9. 费用与可观测性

- 每个 turn 记录 token 用量与费用，汇总到 campaign；前端展示预算消耗。
- transcript 以 JSONL 存放在 `artifacts/turns/<turn_id>.jsonl`，前端可回放。
- 异常处理：turn 超时或 agent 进程崩溃时，标记 turn 为 `failed`，不影响已写入的 trial；连续 3 个 turn 失败则暂停 campaign 并通知 human。
