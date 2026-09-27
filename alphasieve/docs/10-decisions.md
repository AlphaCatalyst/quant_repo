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

**D-18 第一版使用免费数据源 BaoStock，股票池为中证 800**
没有 Tushare 账号，改用 BaoStock：提供不复权日线、复权因子、`tradestatus` 停牌、`isST`、沪深 300 / 中证 500 按日期查询的历史成分，以及带 `pubDate` 的季度财务数据，满足 PIT 要求。因此第一版股票池为中证 800（沪深 300 ∪ 中证 500）；中证 1000 缺少免费的历史成分来源，暂缓。涨跌停价按板块规则推算，流通市值由换手率反推，行业分类只有当前快照（非 PIT），这些局限写入 panel 的 `meta.json`。westock-data 作为对账源（比较日价格变动与成交量）。取代 D-7 中“股票池沪深 300 / 中证 500 / 中证 1000”的表述，以及 [03-data.md](03-data.md) 原先以 Tushare 为主源的设计。

**D-19 L1 阈值暂不调整，等 L3 上线后重新校准**
首次校准显示 v0 的 `min_icir = 0.25` 高于约 80% 的种子因子；但降到种子中位数 0.186 会让超过 10% 的盲目搜索候选满足 ICIR 条件，筛选压力转到尚未实现的 L3。因此 M3 期间继续使用 gate_policy v0；M4 完成 L3（DSR、BH-FDR）后，在 L3 下重新跑校准，再决定是否发布 v1。依据见 [acceptance-m0-m2.md](acceptance-m0-m2.md) §5。

**D-20 研究循环的模型分工与接入方式**
2026-09-27 实测：
- 主力 miner：Codex CLI 0.156.1 + GPT-6 Sol（本机已配置，reasoning effort 设为 high）。执行器为 Codex 使用独立的 `CODEX_HOME`，不加载用户全局的插件、hooks 与 MCP（全局配置下一个空请求就消耗约 1.2 万 token，且会扩大 agent 可用的工具面）。
- 第二 miner：Claude Code 2.1.119 + Claude Opus 5，经 AIHub 的 Anthropic 兼容接口 `/standard/v1/messages` 接入；本机默认的 Bedrock 通道返回 402（预算耗尽），不使用。AIHub 上的 Opus 5 不接受 Claude Code 默认的 reasoning effort 参数，需在单次运行时设置 `MAX_THINKING_TOKENS=0` 并关闭 thinking（与 scicomp-foundry 的做法一致）；全部通过运行时环境变量与 `--settings` 注入，不修改用户的 `~/.claude/settings.json`。
- Reviewer agent（M4 起）使用与 miner 不同家族的模型；规模化阶段（M5）再评估 DeepSeek V4 Pro 等低成本模型。
- 两种 miner 按 turn 交替或按搜索空间格子分工；每条 trial 记录模型与 harness，M4 之后按各模型候选的 holdout 通过率调整分配。
- 费用：M3 试点 campaign 先用运行上限约束（最多 60 个 turn、每个 turn 最长 30 分钟、trial 预算 200），按实测的每 trial 成本的约 1.5 倍设定正式上限。

**D-21 下一阶段（M3 + M4 + F1）的范围与运行约定（2026-09-27）**
- 目标：试点 campaign 无人值守跑完内环 → L3 批次筛选、锁定 shortlist、冻结记忆 → 生成 HoldoutRequest 等待人工批准；F1 前端可只读查看进展、因子库与 ledger。批准 holdout、评审与上 paper 始终由人执行，agent 与实现者都不代为操作。
- 试点方向：财务质量与成长，以及它们与量价的交互（种子库未覆盖，财务数据已补齐）。
- 隔离：本阶段不新建操作系统用户，只用进程级限制（CLI 白名单、角色检查、workspace 内不放数据与评估代码、Codex 独立 `CODEX_HOME`）。已知风险：agent 若绕过 CLI 用 shell 直接读文件，文件权限层面无法阻止；红队测试按进程级约束编写，操作系统用户隔离留待后续。
- 无人值守：允许，按 D-20 的上限（60 个 turn、每个 turn 30 分钟、trial 预算 200）自动停止。
- 审批人：Researcher 与 Approver 暂由同一人担任，审批动作仍以 Approver 身份记录（关闭 Q-5）。
- 前端访问：通过本机 IP 访问（`http://9.134.61.161:8720`），需要用户名密码登录，凭据只保存在本地配置中；页面会展示研究结果，后续还会展示 holdout 与评审信息（关闭 Q-6）。
- 通知：试点阶段不做，查看前端与日报文件（Q-7 推迟）。
- 代码：提交保留在本地，由用户决定何时推送。
- 每日增量数据更新：本阶段即安装 systemd timer（交易日收盘后拉取核心数据并重建 panel）。

## 待定问题

| 编号 | 问题 | 影响 | 计划决定时间 |
|---|---|---|---|
| Q-1 | ~~数据源选择~~ 已决定：见 D-18。需要中证 1000 或申万 PIT 行业时再评估 Tushare / 商业数据 | — | 已关闭 |
| Q-2 | holdout 区间长度（默认 2023-01-01 至项目启动日）是否足够 | L4 统计功效 | M1 结束时 |
| Q-3 | ~~是否发布 gate_policy v1~~ 已决定：见 D-19 | — | 已关闭 |
| Q-4 | ~~agent 默认模型与费用上限~~ 已决定：见 D-20（金额上限待试点实测后确定） | — | 已关闭 |
| Q-5 | ~~Researcher 与 Approver 是否分离~~ 已决定：暂由同一人担任，见 D-21 | — | 已关闭 |
| Q-6 | ~~部署与认证方式~~ 已决定：本机 IP + 登录，见 D-21 | — | 已关闭 |
| Q-7 | 通知渠道：试点阶段不做（D-21），F2 时再定 | 通知实现 | F2 |
| Q-8 | paper 组合的资金规模与基准选择 | M7 策略设定 | M7 开始前 |
| Q-9 | dev panel 能否随任务镜像进入 Nexus Cloud 沙盒（数据授权与合规） | M5 能否启动 | M5 开始前 |
| Q-10 | westock-data 实测：`kline` 支持按日期范围查询，默认前复权（现金分红减法调整、送转按比例缩放），指定不复权报服务错误；已用于日价格变动与成交量对账。是否提供历史指数成分仍未确认 | 事件数据源与中证 1000 成分 | 事件驱动启动前 |
| Q-11 | E3 受限程序因子的沙箱实现：进程级限制还是容器 | M4 的 E3 任务 | M4 开始前 |
