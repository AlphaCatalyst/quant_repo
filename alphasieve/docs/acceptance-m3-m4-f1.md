# M3 / M4 / F1 验收记录

日期：2026-09-27。范围与约定见 [10-decisions.md](10-decisions.md) D-21，实现中的决定见 D-22。

## 1. 结论

| 项 | 状态 |
|---|---|
| 每日增量数据更新（systemd timer，周一至周六 18:40） | 已安装；手动触发一次成功（新增 11,466 行日线，镜像到 Ceph） |
| M3 campaign / turn / 指令 / 请求 / 记忆，Codex 与 Claude Code 执行器，orchestrator | 已实现；两种执行器在真实数据上各跑通一个 turn |
| M4 L3、shortlist 锁定与记忆冻结、HoldoutRequest、holdout 评估（L4）、Review Packet、评审决定 | 已实现；端到端测试在合成数据上通过；真实数据上等待试点结题 |
| F1 只读前端 `http://9.134.61.161:8720`（需要登录） | 已上线（systemd 服务 `alphasieve-web`） |
| 试点 campaign `pilot-fundamental-001` | 无人值守运行中，结果见第 6 节 |
| 测试 | 82 个通过，21 个需要真实数据或网络的测试按标记跳过；ruff 无告警 |

## 2. 新增能力

- 命令：
  - `campaign create/list/status/start/pause/resume/stop/conclude/turns/intensity`
  - `directive add/list`
  - `request create/list/respond`
  - `memory show`
  - `holdout list/approve/reject`
  - `review list/show/decide`
  - `orchestrator run/report`
  - `serve`
  - `data daily-update`
- 评估入口新增的约束：agent 必须在运行中的 campaign 内评估；不得使用 campaign 领域之外的字段（按 L0 失败记录）；有 trial 总预算和单 turn 配额；重复提交会被拒绝。
- 每个 campaign 有一个 git 工作区 `/data/alphasieve/workspaces/<campaign>/`：
  - `program.md`、`brief.md`、`memory.md`、`directives.md` 每个 turn 前重新生成，设为只读；
  - agent 写 `candidates/`、`notes/`；
  - 每个 turn 结束自动 commit。
- transcript 存到 Ceph `transcripts/<campaign>/<turn>.jsonl`，日报在 `reports/<campaign>/daily-<日期>.md` 和 `status.md`。
- systemd 单元：
  - `alphasieve-daily-update.timer`
  - `alphasieve-orchestrator@<campaign>.service`（手动启动，不自动重启）
  - `alphasieve-web.service`

## 3. 测试

- `tests/test_m3_m4_campaign.py`（12 个）：
  - L3 统计量；
  - campaign 校验与状态流转；
  - agent 评估约束：无 campaign、领域越界、预算、暂停、单 turn 配额、重复候选；
  - 指令防泄漏；
  - orchestrator 跑到预算后自动结题；
  - 红队：角色伪装、直接访问数据库或 holdout 文件，两者都被判为 `integrity_violation` 并暂停 campaign；
  - 连续失败自动暂停；
  - holdout 全链路：
    - agent 批准被拒；
    - system 角色批准被拒；
    - 人工批准后 holdout trial 由 system 写入；
    - 同一请求不能批准第二次；
    - 读取预算用尽；
    - agent 看到的状态被遮蔽；
    - 评审决定只能做一次；
    - ledger 哈希链完整；
  - Codex 沙箱可写目录覆盖 agent 侧所有写路径。
- `tests/test_f1_web.py`：未登录和密码错误都返回 401；各读模型数据与 ledger 一致；非法 artifact id 被拒绝。
- 前端：用无头浏览器登录后逐页截图，控制台无错误。

## 4. 真实环境冒烟（smoke campaign）

`smoke-exec-002`（8 个 trial，3 个 turn，领域为量价与换手）：

| turn | agent | 结果 | trial | 用量 | 耗时 |
|---|---|---|---|---|---|
| 1 | Codex / GPT-6 Sol（high） | 完成 | 6 | 输入约 112 万 token（104 万命中缓存），输出 5.8k | 14.5 分钟 |
| 2 | Claude Code / Opus 5（AIHub） | 完成 | 2 | 输入约 19.5 万 token，输出 1.3 万，报告费用 $0.78 | 6.7 分钟 |

两个 agent 都按规程先读 program 和 brief，先 validate 再 eval，额度用完后收到 `BUDGET_EXHAUSTED` 就停下，最后写笔记，并输出 SUMMARY / INSIGHTS。trial 预算用完后 orchestrator 自动结题：没有候选通过 L2，因此不生成 HoldoutRequest，campaign 直接结束。单次评估耗时：只到 L1 约 40–70 秒，到 L2 约 2.5–3.3 分钟。

冒烟暴露并已修复的问题：

1. 读取子进程输出时使用了非阻塞文本流，第一个 turn 因此失败（`smoke-exec-001` 保留这条失败记录，已停止）。
2. Codex 并行提交了两次相同候选，浪费一个 trial。现在同一 campaign 内的重复提交会被拒绝。
3. `data describe` 不支持派生字段（两个 agent 都遇到）。已修复，同时禁止 agent 查询标签字段。
4. 评估并发锁最初放在 Codex 沙箱不可写的目录，试点第一个评估因此报 INTERNAL（未记 trial）。锁已移到 state 目录，并加了回归测试。

隔离实测：
- Claude Code 在 `--bare` + `dontAsk` 模式下，用 Bash 或 Read 读工作区外的文件都被拒绝；
- 白名单外的命令（如 `sed -i`）也被拒绝；
- 按安全测试的口吻写的提示会被模型直接拒答，因此改用普通任务的口吻来验证权限边界。

## 5. 残余风险

- Codex 沙箱不限制读取（D-21 已接受）。靠 turn 后的完整性检查发现违规，事后暂停 campaign，事前无法阻止。
- Researcher 与 Approver 是同一人（D-21）。
- GPT-6 Sol 偶尔返回“模型容量不足”，这样的 turn 记为失败，会占用 turn 预算（试点第 1 个 turn 就是这种情况）。
- L1 阈值仍是 v0（D-19），L3 的 T6 校准尚未做。

## 6. 试点 campaign 结果

中期状态（2026-09-27 14:13）：campaign 自动暂停在第 14 个 turn，共 55 / 200 个 trial。

- 漏斗：55 个候选全部通过 L0，10 个通过 L1，1 个通过 L2。
- 唯一通过 L2 的候选：`F-000082 roe_growth_accel_turnover`，表达式为 `group_rank(Δ60 yoy_ni) + 0.5·group_rank(Δ60 roe_avg) − ts_zscore(40 日平均换手, 60)`。
  - IC 0.032，ICIR 0.271，与因子库最大相关 0.47；
  - 扣成本后年化超额 0.06%，边际 IC 0.001，两项都只是勉强为正。
- 用量：Codex 共约 1,013 万输入 token（大部分命中缓存），5.5 万输出 token。
- 两个 agent 通道都失效了：
  - Claude（AIHub project 228）预算耗尽。第 2 个 turn 失败后按 D-22 第 7 条停用，备用 key 属于同一项目，同样不可用。
  - GPT-6 Sol 从第 12 个 turn 起所有请求都被 OpenAI 以“prompt flagged as potentially violating our usage policy”拒绝。独立探测时连“Reply with the single word ok.”也被拒绝，说明是账号或通道层面的拦截，与 campaign 内容无关。
  - 连续 3 个失败 turn 后，campaign 按设计自动暂停。
- 尚未结题：没有做 L3，也没有生成 HoldoutRequest。

## 7. 操作手册

```bash
cd /data/codebase/quant_repo/alphasieve
export ALPHASIEVE_ROLE=human

# 看进展（也可以打开前端）
.venv/bin/alphasieve campaign status pilot-fundamental-001
.venv/bin/alphasieve campaign turns pilot-fundamental-001
cat /mnt/private_felixjjiang/alphasieve/reports/pilot-fundamental-001/status.md

# 给下一个 turn 下指令 / 回复 agent 请求
.venv/bin/alphasieve directive add pilot-fundamental-001 --kind prioritize --content "..."
.venv/bin/alphasieve request list
.venv/bin/alphasieve request respond R-xxxx --decision answered --response "..."

# 暂停、恢复、提前结题
.venv/bin/alphasieve campaign pause pilot-fundamental-001
.venv/bin/alphasieve campaign resume pilot-fundamental-001 && systemctl start alphasieve-orchestrator@pilot-fundamental-001
.venv/bin/alphasieve campaign conclude pilot-fundamental-001 --reason "..."

# holdout 与评审（只能由人执行）
.venv/bin/alphasieve holdout list
.venv/bin/alphasieve holdout approve H-xxxx --reason "..."    # 或 holdout reject
.venv/bin/alphasieve review list
.venv/bin/alphasieve review show P-xxxx
.venv/bin/alphasieve review decide P-xxxx --decision approved_for_shadow|needs_repair|rejected --reason "..."
```

前端登录凭据：`cat /data/alphasieve/web.credentials`（用户名和密码）。orchestrator 日志在 `/data/alphasieve/logs/orchestrator-<campaign>.jsonl/.err`。
