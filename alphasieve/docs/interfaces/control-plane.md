# 控制面：断点续跑、可见性与新需求接入

状态：C-1 至 C-6 已实现并上线，2026-10-06。与规划不同的地方见 §8。算力放置规则见 [scaling.md](../data/scaling.md) §6；决策见 D-41。

本机是控制面：调度、记账、状态库、看板。计算在 Ray 和 orbenchtest 上，模型调用经过 Mac 上的 codex-lb。这三者都会断：

| 依赖 | 怎么断 | 现在会怎样 |
|---|---|---|
| Ray 集群（多人共用，主要抢 GPU） | 节点被回收；head 被回收或 job API 不可达 | `train submit` 只提交不跟踪。作业失败后本地 trial 一直停在 started，无人发现；任务内不按重训点落盘，重跑从头开始 |
| orbenchtest | 重启后共享盘挂载和 iptables 规则丢失；SSH 中断 | westock 中转会回退本机（已实现）；远端测试直接失败 |
| codex-lb（Mac 经反向隧道到本机 `127.0.0.1:2456`） | Mac 断网、休眠、隧道断开 | 端口消失，所有 agent 回合失败，失败按普通失败计入 campaign；论点 agent 记为 failed |
| 本机自身 | 进程卡住、服务重启 | 日更卡死 4.5 小时无告警（2026-10-05）；超时 6 小时后也只是静默失败 |
| 开发与生产共用工作树 | lane 改到一半时服务导入代码 | Web 曾因此反复崩溃重启；日更运行中惰性导入的模块同样有风险 |

## 1. 原则

1. **状态在本机，计算可丢。** 每个远端作业在本机有一条记录（谁、什么输入、在哪、第几次）。远端只是执行者，丢了就按记录重提，不依赖 Ray 保存任何状态。
2. **先分类，再决定。** 失败分两类：基础设施失败（节点回收、作业丢失、SSH 255、codex-lb 不可达、超时无心跳）和任务失败（代码异常、数据错误、校验不通过）。前者自动重试、不计入预算，后者照常记账、不重试。分不清时按任务失败处理，并告警让人看。
3. **幂等的工作单元。** 远端作业拆成可独立完成的单元（重训点、股票批次、因子批次），每个单元的输出写到共享盘并原子改名。重提时跳过已完成的单元。
4. **有上限的自动恢复。** 每个作业最多重试 3 次，指数退避；连续基础设施失败时暂停这一类作业，并告警，而不是无限重试。
5. **失败必须可见。** 任何作业失败、卡住或数据过期，都在看板和提醒里出现。

## 2. 通用作业层

新增本机作业表和一组命令，取代各处零散的 submit/collect：

```text
jobs(job_id, kind, idempotency_key, placement, target, attempt, status, input_ref, output_ref,
     trial_id, submitted_at, heartbeat_at, finished_at, failure_class, error)
alphasieve jobs submit <kind> ...   # 写记录后提交
alphasieve jobs status [--open]
alphasieve jobs reconcile           # 定时：查询 Ray/SSH 实际状态，分类失败，按规则重提或告警
alphasieve jobs resume <job_id>     # 人工重提（同一 idempotency_key，不新开 trial）
alphasieve jobs cancel <job_id>
```

- `jobs reconcile` 由 systemd timer 每 5 分钟运行。它查询 `ray job status`；作业不存在或集群不可达且超过心跳时限时，判为基础设施失败。
- 训练接入后，节点回收不再新开 trial：同一 trial、同一 bundle 重提，`attempt` 加一，不计入策略试验预算。只有任务失败才调用 `fail_trial`。
- 集群切换：需要 r2 的作业只在 `28.83` 和 `21.234` 之间切换；自包含作业还可以去 `29.191`、`29.185`（见 scicomp-foundry docs/40）。
- 训练引擎改为每个重训点把结果写到 `runs/<trial>/units/<retrain_date>.json`，重提时跳过已完成的重训点。
- 现有的 `train submit/collect`、`risk submit/collect`、`sw_industry_sensitivity --ray` 迁移为 `jobs` 的不同 kind；旧命令保留为薄包装。

## 3. codex-lb 断联

- **探测：** agent 回合开始前探测 `127.0.0.1:2456/health`（约 50 ms）。不可达时不启动回合。
- **分类：** 回合中出现连接被拒、隧道断开、上游 5xx 连续失败时，标为 `failure_class=llm_unavailable`。campaign 不计这次失败，论点 agent run 记为 `interrupted` 而不是 `failed`。
- **暂停与恢复：** 连续 2 次不可达就暂停所有 agent 类作业（campaign、论点、Codex lane），写一条系统提醒“codex-lb 不可达”。`jobs reconcile` 每 5 分钟探测一次，恢复后自动续跑被中断的回合：campaign 从上一个完成的回合继续，论点 run 用同一 workspace 重跑。
- **Codex lane：** companion 支持 `task --resume`。lane 被中断后用同一会话续跑，不重新开题。
- **隧道本身：** Mac 端的反向隧道建议由 Mac 上的 launchd 或 autossh 自动重连；本机侧只负责探测和暂停，不尝试替 Mac 重连。

## 4. 本机控制面的稳定性

1. **发布目录。** 所有 systemd 服务改从 `/data/alphasieve/deploy/current` 运行，它是固定在某个 commit 上的 git worktree。`deploy/release.sh <commit>` 先在 orbenchtest 跑全量测试，通过后切换 worktree、重启 Web，日更和其他 timer 在下次触发时使用新版本。开发工作树的改动不再影响生产。
2. **超时与失败钩子。** 日更 unit 的超时从 6 小时降到 2 小时；所有 unit 加 `OnFailure=` 写系统提醒。
3. **系统健康。** 监控增加 system 类规则：日更失败或超过 1 个交易日未成功、某数据集最新日期落后、备份超过 2 小时未成功、evalbridge 心跳过期、codex-lb 不可达、Ray 集群不可达、有作业连续失败。看板新增“系统健康”页，列出这些项和最近一次日更的耗时与警告。
4. **orbenchtest 自检。** `jobs reconcile` 顺带检查 orbenchtest 是否可达、共享盘是否挂载；不可达时提醒，westock 继续回退本机。
5. **清理。** agent 和 lane 结束后回收 Codex 后台进程；`AGENTS.md` 的测试命令改为 `deploy/remote/pytest.sh`。

## 5. 新需求怎么接入

每个新需求先回答一张固定的接入单，再写代码。接入单放在 PR 描述或 `docs/` 的对应章节：

| 问题 | 可选答案 |
|---|---|
| 它是什么形态 | 一次性分析 / 定时任务 / 常驻服务 / agent 工作流 / 看板页面 |
| 读哪些数据，最晚到哪天 | 只用 dev（≤2022）/ 含 holdout / 含 fresh。决定能否离开本机 |
| 放在哪里跑 | 本机（只限轻量或含 holdout/fresh 数据）/ orbenchtest（要外网或 agent）/ Ray（dev 批量计算） |
| 工作单元与幂等键 | 按什么拆，重跑如何跳过已完成部分 |
| 失败分类 | 哪些错误算基础设施失败 |
| 输出与登记 | 写到哪里，是否进状态库、是否记 trial、是否需要人工审批 |
| 可见性 | 在看板哪里显示，哪些情况发提醒，是否涉及持仓隐私 |
| 验收 | 测试、真实小样本运行、与已有结果对比 |

实现上统一到三个扩展点，避免每个需求自己造调度：

- **作业种类：** 在作业层注册一个 kind，声明放置位置、数据层级上限、单元拆分和幂等键。提交、重试、续跑、告警都由作业层负责。
- **定时：** 新的定时任务不再新增 systemd unit，而是在一张调度配置里登记，由一个控制面 timer 统一触发并经过作业层。
- **监控规则：** 在 monitor 里注册规则，产出提醒；看板自动展示。

## 6. Skill

需要，分两个：

1. **`alphasieve-control-plane`（运维）。** 给 Cursor 和 Codex 用：查看系统健康、作业状态、失败分类、续跑与取消、发布、codex-lb 和 Ray 断联的处置步骤。它只调用 CLI，不直接改数据库或 systemd 文件。
2. **`alphasieve-new-capability`（接入）。** 收到新需求时，按 §5 的接入单走一遍：判断数据层级与放置、选扩展点、拆 lane 和文件归属、要求远端测试和真实小样本验收、更新 docs 和 README。我把需求交给 Codex lane 时也按它来写 prompt。

两个 skill 放在 `~/.cursor/skills/` 下，与 `remote-compute-offload` 并列；仓库内 `AGENTS.md` 指向它们。skill 等 §2–§5 实现后再写，避免描述不存在的命令。

## 7. 顺序

| 步骤 | 内容 | 依赖 |
|---|---|---|
| C-1 | 发布目录与发布脚本；unit 超时与 `OnFailure`；`AGENTS.md` 测试命令；清理残留进程 | 无 |
| C-2 | 系统健康规则与看板页 | C-1 |
| C-3 | 作业层与 `jobs reconcile`；训练按重训点落盘并接入，节点回收不新开 trial | C-1 |
| C-4 | codex-lb 探测、暂停与自动续跑 | C-3 |
| C-5 | 定时任务统一登记；`risk`、`sw` 等迁移到作业层 | C-3 |
| C-6 | 两个 skill | C-2–C-5 |

C-1 和 C-2 风险低，可以先做。C-3 会改训练与 trial 记账的契约，改之前需要 human 确认“基础设施失败不计入策略试验预算”这条规则。

## 8. 实现状态（2026-10-06）

上线方式：`deploy/release.sh` 在 orbenchtest 跑全量测试后发布到 `/data/alphasieve/deploy/current`，`deploy/install.sh` 安装 unit。所有 alphasieve 服务从 `current` 运行。

| 部分 | 实现 |
|---|---|
| 控制面入口 | 一个 `alphasieve-control.timer`，每分钟运行 `alphasieve control tick`。依次：触发到点的调度、`jobs reconcile`、codex-lb 探测与续跑、资源快照（每 2 分钟）、系统健康提醒（每 10 分钟）。输出追加到 `logs/control.jsonl` |
| 作业层 | `src/alphasieve/control/jobs.py` 与 `control/kinds/`。已注册 `train`、`risk_report`、`sw_sensitivity`、`local_command`。基础设施失败最多重试 3 次，退避 2、8、30 分钟，可切换到同类集群；任务失败不重试 |
| 训练续跑 | Ray 上的训练把每个重训点写到 `runs/<trial>/units/<date>.json`，内容带 bundle 哈希；重提时跳过已完成且哈希一致的重训点。节点回收时同一 trial 的 `attempt` 加一，不新开 trial |
| 调度 | `configs/control/schedule.yaml`：`daily-update`（周一至周六 18:40，超时 3 小时，westock 走 orbenchtest）、`state-backup`（每小时）、`forward-daily`（未启用，依赖日更）。本机作业以 transient unit `alphasieve-job-<id>` 运行，`Nice=10`、`CPUWeight=20`。新登记的条目从下一个时间点开始，不补跑登记前的时间点 |
| codex-lb | `control/llm.py`。连续 2 次不可达就暂停 agent 工作并写系统提醒；中断的回合记为 `interrupted`，不计入 campaign 预算（token 用量仍计入）；恢复后 campaign 继续，论点 run 最多自动续跑 3 次 |
| 健康与资源 | `alphasieve health show`、`alphasieve resources show [--probe]`。资源快照写到 `control/resources.json` 和 `resources-history.jsonl`，包括本机负载与 unit、四个 Ray 集群的节点与 CPU/GPU 用量、orbenchtest 的挂载、codex-lb 延迟 |
| 失败钩子 | 所有 unit 带 `OnFailure=alphasieve-failure@%n.service`，写一条 system 提醒 |
| 看板 | 新增“计算资源”“作业与调度”“系统健康”三页，总览页顶部有状态条；提醒页可按 system 类过滤。API 为 `/api/control/{resources,health,jobs,schedule,llm}`，只读 |
| Skill | `~/.cursor/skills/alphasieve-control-plane/` 与 `~/.cursor/skills/alphasieve-new-capability/` |

与规划不同的地方：

- 旧入口直接删除，没有保留薄包装：`train submit/collect`、`risk submit/collect`、`sw_industry_sensitivity --ray/--collect`、`deploy/ray/submit_batch.sh`，以及日更、备份、forward 各自的 systemd timer 和 evalworker unit。
- `jobs reconcile` 由 control tick 每分钟运行，不是每 5 分钟。
- 日更超时是 3 小时，运行超过 2 小时就在健康页告警。
- evalbridge 在没有远端 worker 时会撤掉自己的心跳，所以健康检查看 unit 是否运行，以及“有待评估任务但没有远端 worker”这种情况，不再单看心跳。
- 常驻服务（web、evalbridge）不设运行时长上限。设了上限的话，到期停止会被记成失败并每周误报一次。
- Codex lane 中断后的续跑仍由人或 agent 手动执行 `codex-companion task --resume`，步骤写在 control-plane skill 里。
