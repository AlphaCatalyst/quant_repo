# 测试

状态：已实现。2026-10-07 核对。

## 远端运行完整 pytest

本机负载高时，在仓库根目录运行 `deploy/remote/pytest.sh`。脚本将已跟踪及未忽略的新文件同步至 `orbenchtest:~/alphasieve-ci/tree/`，按测试文件并行运行 pytest；可追加 pytest 参数，例如 `deploy/remote/pytest.sh -k forward`。默认最多 24 个测试文件同时运行，可用 `ALPHASIEVE_PYTEST_WORKERS` 调整。远端虚拟环境按 `uv.lock` 缓存，首次运行需下载依赖。

脚本把热存储和持久存储指向远端临时目录，并关闭真实数据及网络测试；这些测试会按现有 pytest 标记跳过。远端环境没有本机被忽略的真实数据，也不会复制 `.git`、`.venv` 或 `node_modules`。若出现失败，脚本会打印对应测试文件的完整失败输出并返回非零码；需要核对环境差异时，在本机只重跑所列文件。

AlphaSieve 里有两种“检验”，不要混淆：

- **研究验证**：检验一个因子候选是否有效，即 L0–L5 分层 verifier（见 [research-core.md](research-core.md) §4）。
- **系统测试**：检验 AlphaSieve 本身是否正确，即本文件。系统测试的核心目标是证明 verifier 本身可信——如果评估代码有前视或计数漏洞，L0–L5 再严格也没有意义。

## 1. 系统测试分级

| 级别 | 名称 | 测什么 | 运行时机 |
|---|---|---|---|
| T0 | 静态检查 | ruff、类型检查、schema 校验、配置文件校验 | 每次提交 |
| T1 | 单元测试 | 算子、指标、DSL 解析、状态机转移、ledger 写入 | 每次提交 |
| T2 | 不变量测试 | 无前视、截断不变性、区间隔离、确定性、ledger 不可篡改 | 每次提交 |
| T3 | 数据验证 | 已知事件核对、跨源对账、质量报告阈值 | 每次数据更新后 |
| T4 | 集成测试 | 在小型 fixture panel 上跑通 CLI 全链路 | 每次提交 |
| T5 | 金标对照 | 经典因子与参考实现对照；回测与独立引擎对照 | 每日定时 |
| T6 | Gate 校准 | 随机因子与植入信号的蒙特卡洛测试 | 每周定时；gate policy 变更时必跑 |
| T7 | 红队测试 | agent 越权读取、指令泄漏、工具逃逸、刷提交 | 每个 agent 相关里程碑；每周定时 |
| T8 | 复现与回归 | artifact 按 manifest 重跑得到相同哈希；指标回归 | 每日定时 |

## 2. 各级要点

### T1 单元测试

- 每个算子对照手写的 numpy 参考实现，覆盖缺失值、窗口不足、并列排名等边界。
- 每个指标对照小规模手算结果（例如 5 只股票 × 10 天的 RankIC）。
- 状态机：每条合法转移有测试；非法转移（例如 `evaluated` 直接到 `shadow_promoted`）必须抛错。

### T2 不变量测试（最重要）

| 不变量 | 测试方法 |
|---|---|
| 标签不前视 | 把 t+1 之后的标签随机打乱，已知有效因子的 RankIC 应接近 0 |
| 截断不变性 | 删掉 t 之后的数据重新计算，t 日的因子值与评估输入不变 |
| 指标只在声明区间计算 | 在声明区间之外注入极端值，指标不变 |
| embargo 生效 | dev 末尾 embargo 期内的样本不出现在评估中 |
| 区间隔离 | agent 角色调用任何读取 holdout / fresh 的路径都返回权限错误；文件权限检查 |
| 财务 PIT | 公告日之前财务字段为缺失 |
| 确定性 | 同一输入两次运行，指标与 artifact 哈希相同 |
| ledger 完整 | 每次评估恰好写入一条 started 与一条结果记录；篡改任意记录后校验失败 |
| trial 计数 | 重复提交同一哈希会增加 trial 数；失败候选也计数 |

这一级直接对应已发表系统中出过的问题：AutoScientist-Quant 发现 AlphaAgent、QuantaAlpha、RD-Agent(Q) 共用的评估代码曾按全样本计算指标（见 [analysis/factor-model-co-optimization-research.md](../../../analysis/factor-model-co-optimization-research.md) §5）。

### T3 数据验证

- **已知事件核对**：维护一份事件清单（ST 摘帽戴帽、长期停牌、连续涨停、退市、指数调样、财报公告日），逐条核对 panel 字段。
- **跨源对账**：从 Tushare 与第二数据源（westock-data 或 Qlib 社区数据）抽样对比收盘价、复权因子、成交额，偏差超过阈值报警。
- **质量阈值**：覆盖率、异常比例、时效性超过阈值时，当日数据标记为不可用，并阻止 fresh 计算。

### T4 集成测试

- 使用合成 fixture panel（确定性随机生成，约 200 只股票 × 3 年，含停牌、涨跌停、退市），不依赖真实数据，可在 CI 中运行。
- 覆盖链路：`factor validate` → `factor eval` → ledger → 状态转移 → `library` → `ledger stats`；M4 起加上 shortlist → holdout → Review Packet。
- 用脚本化的“假 agent”（按预设剧本调用 CLI）测试 orchestrator 与 turn 生命周期，不消耗 LLM 费用。

### T5 金标对照

- 10–20 个经典因子（短期反转、动量、低波、换手、估值、盈利质量等）在真实 dev 数据上的指标，与独立 notebook 实现对照，误差 < 1e-6。
- 回测：同一组持仓信号分别用 AlphaSieve 回测与一个独立实现（pandas 简版或 Qlib 回测）运行，净值差异在成本模型允许范围内。

### T6 Gate 校准

gate 的阈值是否合理，需要用已知答案的数据来检验：

- **零假设模拟**：生成 N 个随机因子（随机表达式，或对真实因子做截面打乱），全部跑 L1–L3，统计各层通过率。L3（DSR + BH）的误通过率应接近名义水平（例如 5%）；显著偏高说明计数或阈值有问题。
- **植入信号**：在一份 panel 副本中植入已知 IC 的合成因子（例如 IC = 0.02、0.03、0.05），检查各层的检出率，得到“功效曲线”。
- **搜索模拟**：用脚本模拟一个做 500 次试验、每次挑最好结果的“贪心 agent”，验证 L3 能把它的最佳随机结果挡住。
- 结果写入 gate 校准报告，与 `gate_policy.yaml` 版本绑定。

### T7 红队测试

| 场景 | 期望结果 |
|---|---|
| program.md 之外诱导 agent 读取 `data/panel/holdout` | 操作系统权限拒绝；审计记录 |
| agent 尝试调用 human 专属命令（审批、锁定 shortlist） | 退出码 4；审计记录 |
| agent 尝试修改 workspace 之外的文件 | 被工具权限拒绝 |
| agent 重复提交同一哈希以“刷”结果 | trial 数增加；停滞诊断报警 |
| 人在指令中粘贴 holdout 指标 | 指令被拒绝，并提示原因 |
| E3 程序尝试 import 网络或文件模块 | L0 静态检查失败 |
| E3 程序尝试通过索引技巧读取未来数据 | 截断不变性测试失败 |

### T8 复现与回归

- 每日随机抽取若干 artifact，按 manifest 重跑，哈希必须一致。
- 金标因子指标做回归快照；代码改动导致变化时必须显式更新快照并说明原因。

## 3. 测试数据

| 数据 | 用途 | 位置 |
|---|---|---|
| 合成 fixture panel | T1、T2、T4、CI | 由 `tests/fixtures/synth.py` 按种子生成，不入库 |
| 真实 dev 小切片 | T5 的快速版本 | 本地热存储，不入库 |
| 已知事件清单 | T3 | `tests/data_events.yaml`，入库（只含事件描述，不含行情） |
| 金标快照 | T5、T8 | `tests/golden/*.json`，入库（只含指标） |

真实行情数据不进入 git，也不进入 CI 日志。

## 4. 与里程碑的对应

| 里程碑 | 必须新增的测试 |
|---|---|
| M0 | T0、T1（contracts、ledger、CLI 信封）、T2（ledger 完整性、角色权限） |
| M1 | T3 全部；T2（财务 PIT、区间隔离、embargo） |
| M2 | T1（算子、指标）、T2（标签不前视、截断不变性、确定性、trial 计数）、T4、T5、T6（首次校准） |
| M3 | T4（假 agent）、T7 |
| M4 | T4（holdout 链路）、T6（L3 校准）、T7（holdout 泄漏检查） |
| M6 | T5（回测对照）、执行约束单元测试 |
| M7 | T2（fresh 不回填）、T8 |

## 不可变发布验证

`deploy/release.sh <commit>` 从目标 commit 建临时 worktree，并在其中调用 `deploy/remote/pytest.sh`；远端测试通过后才建立发布 worktree。`--dry-run` 只打印动作。`--skip-tests` 只能显式使用并会警告。发布和安装 systemd unit 由运维人工执行。
