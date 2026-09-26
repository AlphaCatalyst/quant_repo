# 10 · 决策记录

记录已做出的关键决策及理由，以及尚待确定的问题。新增决策追加在末尾，不修改已有条目；推翻某条决策时新增一条并注明“取代 D-n”。

## 已定决策

**D-1 项目名 AlphaSieve**
agent 大量生成候选，分层 verifier 逐级筛选，只有经得住留出与前瞻验证的留下；名字表达“主要产出是筛选，而不是生成”。

**D-2 自研薄核心，不整体 fork 开源平台**
调研的项目中没有一个完整实现研究契约层（trial ledger、holdout 预算、证据分级状态机、统一 StrategySpec），且候选底座各有问题：QuantMind-qm2 体量大且耦合重，RD-Agent 评估窗口存在重叠，kph 无许可证，QuantDesk 的领域模型面向单策略加密交易。契约与 gate 自研，计算与模型用库，算子等模块从 MIT 项目移植。依据见 [../../analysis/](../../analysis/README.md) 与对话中的 build-vs-reuse 分析。

**D-3 Python 核心 + FastAPI + React 前端**
核心与 API 同语言，service 层只实现一次；前端独立工程，由 OpenAPI 生成类型。

**D-4 SQLite + Parquet + 文件系统 artifact**
单机阶段零运维；ledger 用 append-only + 哈希链保证完整性；service 层经仓储接口访问，后续可迁移 Postgres。

**D-5 service 层是唯一写入路径**
CLI 与 API 都调用同一套 service 函数；权限、校验、审计只实现一次。

**D-6 agent runtime 使用 Claude Code / Codex，不自研**
适配器只负责 prompt、子进程、工具限制与 transcript；agent 通过 JSON CLI 操作，不接触数据文件与评估代码。

**D-7 第一阶段只做 A 股日频截面选股（指数增强）**
verifier 统计功效最高、最贴近 A 股实盘约束；其他策略按 [design/strategy-scope.md](../../design/strategy-scope.md) 分档后续引入。

**D-8 审批结构化，不靠对话**
与 QuantDesk 的“对话式审批”相反：审批、评审、回复都是带类型与必填理由的表单，推进状态机并留痕。

**D-9 holdout 与 fresh 物理隔离**
独立目录与文件权限、agent 以独立操作系统用户运行、data 层按角色访问；holdout 结果永不写入 agent 记忆。

**D-10 前端是审查台**
首屏展示漏斗、预算与待办，而不是收益曲线；所有指标带证据等级，所有“最佳结果”带 trial 数与 DSR 门槛。

**D-11 暂不引入 QuantDesk 平台外壳**
先完成 Python 核心与自研前端；若后续需要更完整的 agent 会话管理与工作区 UI，再评估把核心作为引擎接入 QuantDesk 外壳。

**D-12 状态机补充 `robust_passed` 与 `ledger_failed`**
L3 需要整批 trial 计数，不能在单次评估中执行；通过 L2 的候选先进入 `robust_passed` 等待批次 gate。已同步到 [design/system-contracts.md](../../design/system-contracts.md)。

**D-13 存储分四层：git / 本地热存储 / Ceph / taijifs**
沿用 scicomp-foundry 的做法。SQLite 状态库与工作 panel 放本地盘（`/data/alphasieve/`），原始数据权威副本、artifact、transcript、备份放 Ceph（`/mnt/private_felixjjiang/alphasieve/`），发布归档放 taijifs。SQLite 不放 ceph-fuse。详见 [03-data.md](03-data.md) §8。

**D-14 研究循环执行后端：先本机 Claude Code / Codex，后 Nexus Cloud**
Cursor（含 subagent）用于开发 AlphaSieve 本身，不作为研究循环后端，因为难以施加操作系统用户隔离与 CLI 白名单，也无法由 orchestrator 无人值守调度。Nexus Cloud 用于广度搜索，云端结果只作筛查，本机 canonical 重验为权威，任务内 trial 全部入账、按 batch 计数。详见 [14-agent-execution.md](14-agent-execution.md)。

**D-15 因子搜索空间用表达能力分级 + 覆盖坐标定义**
E1 公式、E2 模板、E3 受限程序、E4 学习型、E5 文本事件；覆盖坐标为数据域 × 变换形态 × 时间尺度。窗口参数离散化；扩大搜索空间由人决定。详见 [11-factor-search-space.md](11-factor-search-space.md)。

**D-16 回测分 B1–B5 五级，引擎自研并用 Qlib 交叉验证**
详见 [13-backtest.md](13-backtest.md)。

**D-17 系统测试分 T0–T8 九级，其中不变量测试与 gate 校准是必需项**
verifier 本身的正确性要靠测试证明；gate 阈值用零假设模拟与植入信号校准。详见 [12-testing.md](12-testing.md)。

## 待定问题

| 编号 | 问题 | 影响 | 计划决定时间 |
|---|---|---|---|
| Q-1 | 数据源是否只用 Tushare，还是接入自有行情库作为主源；Tushare 账号积分是否覆盖 `stk_limit`、`index_weight`、财务报表等接口 | M1 Provider 设计、数据授权 | M1 开始前 |
| Q-2 | holdout 区间长度（默认 2023-01-01 至项目启动日）是否足够 | L4 统计功效 | M1 结束时 |
| Q-3 | gate 阈值的校准基准：Alpha158 子集还是自有因子库 | L1/L2 通过率 | M2 结束时 |
| Q-4 | agent 默认使用哪个模型、单 campaign 费用上限 | 预算默认值 | M3 开始前 |
| Q-5 | Researcher 与 Approver 是否强制分离 | 审批流程与权限 | F2 开始前 |
| Q-6 | 是否需要多用户部署（团队共享一台服务器）以及认证方式 | API 认证、部署 | F2 开始前 |
| Q-7 | 通知渠道：企业微信机器人还是企业微信应用消息 | 通知实现 | F2 |
| Q-8 | paper 组合的资金规模与基准选择 | M7 策略设定 | M7 开始前 |
| Q-9 | dev panel 能否随任务镜像进入 Nexus Cloud 沙盒（数据授权与合规） | M5 能否启动 | M5 开始前 |
| Q-10 | westock-data 能否批量拉取历史日线、是否提供历史指数成分（PIT） | 交叉校验源与事件数据源的可行性 | M1 期间实测 |
| Q-11 | E3 受限程序因子的沙箱实现：进程级限制还是容器 | M4 的 E3 任务 | M4 开始前 |
