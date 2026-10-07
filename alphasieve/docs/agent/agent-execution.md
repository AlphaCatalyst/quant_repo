# 14 · Agent 执行后端

本文回答：用哪种 agent 执行任务。分两类任务讨论：

- **开发 AlphaSieve 本身**（写代码、写测试、review）：工程任务，结果进 git，由人 review。
- **研究循环**（挖因子）：结果进 ledger，受分层 verifier 与隔离规则约束。

两类任务对执行环境的要求完全不同，所以选型也不同。

## 1. 候选后端对比

| 后端 | 形态 | 并发 | 能否施加我们的隔离规则 | 数据是否出机 | 结论 |
|---|---|---|---|---|---|
| 本机 Claude Code CLI | orchestrator 以子进程启动 `claude -p` | 1–3，受机器与额度限制 | 能：独立操作系统用户、工具白名单、cwd 限定在 workspace | 否 | **研究循环默认后端**（M3 起） |
| 本机 Codex CLI | 子进程启动 `codex exec` | 同上 | 能：同上 | 否 | **研究循环默认后端**，与 Claude Code 并列，用于模型多样性 |
| Codex subagent | Codex 会话内再派生子任务 | 同上 | 与本机 Codex 相同；不单独作为一类后端 | 否 | 归入本机 Codex 后端 |
| Cursor subagent | IDE 会话内的子任务 | 受会话限制 | 难：工具与运行环境由 Cursor 管理，难以套用操作系统用户隔离和 CLI 白名单；无法由 orchestrator 无人值守地调度 | 取决于运行位置 | **用于开发 AlphaSieve 本身**，不作为研究循环后端 |
| Cursor SDK / cloud agent | 可编程调用；cloud agent 在远端 VM 运行 | 较高 | 难：同上；cloud agent 需要把数据带到远端 | cloud 模式会 | 暂不采用；如需远端并发，优先 Nexus |
| Nexus Cloud | 批量沙盒任务，每个任务一个 agent（Codex / Claude Code harness），任务内带 verifier | 高：参考 scicomp-foundry，账号额度 100 slots、2 个活跃 batch，单 batch 上限 50000 条 | 能：无网络沙盒、固定镜像、任务内 verifier；holdout 不进镜像 | dev 数据随镜像出机（需确认合规） | **规模化广度搜索后端**（本地循环验证后引入） |

## 2. 推荐分工

```text
开发 AlphaSieve            研究循环（挖因子）
─────────────              ──────────────────────────────────────────
Cursor（含 subagent）       阶段一：本机 Claude Code / Codex（M3–M4）
Claude Code / Codex         阶段二：Nexus Cloud 广度搜索（本地链路稳定后）
人 review、CI               两者共用同一套 CLI、ledger 与 verifier
```

- 开发阶段用 Cursor 及其 subagent 做代码编写、并行调研与 review，效率高；这类工作的产物是代码，由 CI 和人审把关，不需要研究隔离。
- 研究循环必须满足 [agent-harness.md](agent-harness.md) 的隔离规则（操作系统用户、工具白名单、holdout 不可见），因此只用能被 orchestrator 无人值守调度、且能施加这些限制的后端。

## 3. 执行器抽象

```python
class AgentExecutor(Protocol):
    name: str                      # local_claude / local_codex / nexus
    def submit(self, tasks: list[MinerTask]) -> ExecutionHandle: ...
    def poll(self, handle: ExecutionHandle) -> ExecutionStatus: ...
    def collect(self, handle: ExecutionHandle) -> list[MinerResult]: ...
```

- `MinerTask`：campaign、分配的搜索空间格子、trial 预算、时间预算、program / brief / memory 快照、模型与 harness。
- `MinerResult`：候选列表、任务内 trial 日志（带哈希链）、transcript、费用、退出状态。
- 本机执行器的 `submit` 就是启动子进程；Nexus 执行器的 `submit` 是打包任务并提交 batch。上层 orchestrator 不关心差异。

## 4. 本机执行（阶段一）

- 每个 turn 一个子进程，cwd 为 campaign workspace，环境变量 `ALPHASIEVE_ROLE=agent`，以 `alphasieve-agent` 操作系统用户运行。
- 评估在本机直接调用 service 层，trial 实时写入权威 ledger。
- 并发默认 2；每周额度与费用预算用尽时暂停调度。
- 模型：Claude Code 与 Codex 各配置一个默认模型（待定，见 [decisions.md](../overview/decisions.md) Q-4）；同一 campaign 可交替使用两种 harness 以增加探索多样性，trial 记录 harness 与模型。

### codex-lb 断联与续跑

Codex 回合开始前探测配置中的 codex-lb `/health`。连接失败时不启动回合；连续两次探测失败后暂停相关 campaign，并发送一次系统提醒。运行中的回合若因模型连接中断，标为 `interrupted`，不计入失败次数或回合预算。论点研究或复核 run 同样标为 `interrupted`，保留 workspace。

控制面定时探测恢复后，将因 `llm_unavailable` 暂停的 campaign 重新启动，并在同一 workspace 和输入上重跑被中断的论点 run，单个 run 最多自动续跑三次。Claude 使用独立端点，不受 codex-lb 探测结果阻断；可识别的 Claude 网络故障也按基础设施中断处理。

## 5. Nexus Cloud 执行（阶段二）

参考 scicomp-foundry 的 authoring factory（`/data/codebase/scicomputing/scicomp-foundry/docs/22-authoring-factory.md`）：云端并发执行，本机做权威重验与唯一写入。

### 5.1 流程

```text
planner（本机）：按覆盖矩阵选 K 个格子，生成 K 个 MinerTask
  → 打包：任务镜像（alphasieve 包 + dev panel 快照 + 评估器）+ 每个任务的 brief / program / memory
  → submit：一个 batch，每个任务一个 agent（Codex 或 Claude Code harness），无网络
  → 任务内：agent 使用沙盒内的 alphasieve CLI（role=agent）评估候选，每次评估写入任务内 trial 日志
  → 任务内 verifier：独立环境中按确定性流程重算全部候选，校验 trial 日志哈希链
  → collect（本机）：取回候选、trial 日志、transcript
  → ingest（本机）：任务内每一条 trial 写入权威 ledger（标注 executor=nexus、batch_id、task_id）
  → canonical 重验（本机）：对任务报告通过 L2 的候选在本机重新评估，本机结果才是权威证据
  → 后续 L3、L4 与本地流程完全相同
```

### 5.2 必须守住的规则

| 规则 | 原因与实现 |
|---|---|
| holdout / fresh 永不进镜像 | 镜像构建脚本只打包 dev 目录；构建后扫描镜像内容 |
| 云端结果只是筛查 | 与 scicomp-foundry 相同：权威证据只来自本机 canonical 重验；两者不一致时标记并人工复核 |
| 任务内 trial 全部入账 | 任务内每次评估都计入 DSR 试验数；任务的 trial 日志缺失或哈希链断裂，该任务的全部候选作废 |
| DSR 计数按 batch 统一 | 同一 batch 的 K 个任务结果会被一起挑选进 shortlist，因此试验数按整个 batch 累计，不能按任务或格子拆分计数 |
| 模型与 harness 可追溯 | 每条 trial 记录模型、harness 版本、镜像 digest |
| 数据合规 | dev panel 出机前需确认数据授权允许在内部云沙盒中使用；不满足则只能用本机后端 |

### 5.3 规模与代价

- 例：一个 batch 50 个任务 × 每任务 30 次 trial = 1500 次 trial。广度上去了，但 batch 级 DSR 门槛也随之升高。
- 因此 Nexus 适合“广度探索、快速证伪大量方向”，而不是在单一方向上深挖；深挖用本机后端的连续 turn 更合适。
- 镜像大小：中证 1000 股票池 dev panel 压缩后约数百 MB（见 [data-and-panels.md](../data/data-and-panels.md) §7），应烘焙进任务镜像，而不是每个任务单独携带。

### 5.4 引入条件

- M4 完成：本机链路（L0–L4、ledger、Review Packet）稳定运行，并通过红队测试。
- 数据合规确认。
- ingest 与 canonical 重验流程通过集成测试（用假任务结果模拟）。

## 6. 模型选择

- 参考 scicomp-foundry 的实测：不同模型在同一任务上的强弱并不一致，单模型会同时高估和低估难度；因此研究循环中保留至少两种模型与两种 harness。
- 默认模型、接入方式与试点费用约束见 [decisions.md](../overview/decisions.md) D-20：主力 Codex + GPT-6 Sol，第二通道 Claude Code + Claude Opus 5（经 AIHub）。
- 模型切换不改变任何评估逻辑；trial 记录模型信息，用于事后分析“哪种模型提出的候选更能通过 holdout”。

## 平台批量任务（D-23）

用于扩大试验规模：只用 dev 数据，不涉及 holdout 与 agent。

```bash
cd /data/codebase/quant_repo/alphasieve
deploy/ray/submit.sh calib gate calibrate --random 2000     # 手动 dev 命令；训练和报告使用 jobs submit
ls /taijifs_zw35/r2/felixjjiang/alphasieve/runs/             # 每个任务的 result.json
ray job list --address http://28.83.35.117:8081              # 任务状态；日志用 ray job logs <job_id>
```

- 代码随任务上传；平台上按 `pyproject.toml` 建环境，同一依赖版本会复用。
- 数据在 `/taijifs_zw35/r2/felixjjiang/alphasieve/hot/`，目前只有 dev panel；平台任务有自己的状态库，与本机主 ledger 分开。
- 放了 RunLab 密钥文件后，每个任务自动在 RunLab 记一个 run，见 D-23。
