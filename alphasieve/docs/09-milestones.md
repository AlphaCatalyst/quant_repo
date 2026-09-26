# 09 · 里程碑

里程碑按依赖顺序排列。每个里程碑都要能独立验收：有可运行的命令、有测试、有文档更新。规模（S / M / L）是相对工作量，用于排期参考。

## 依赖关系

```text
M0 基础 ──▶ M1 数据 ──▶ M2 因子评估 ──┬──▶ M3 Agent 循环 ──▶ M4 稳健性与留出 ──┬──▶ M6 策略与模型 ──▶ M7 前瞻与 paper ──▶ M8 扩展
                                      │                                         └──▶ M5 Nexus 广度搜索
                                      └──▶ F1 前端只读 ──────────▶ F2 前端交互 ─────────────────────────▶ F3 前端完整
```

测试任务按 [12-testing.md](12-testing.md) §4 随各里程碑落地，不单列里程碑。

## M0 · 基础（S）

目标：把契约、存储和 CLI 的骨架立起来，后续模块都在这个骨架上加。

任务：
- `contracts/`：pydantic 定义 ResearchQuestion、DataContract、FactorSpec、TrialLedgerEntry、Campaign；导出 JSON Schema。
- `state/`：SQLite 初始化与迁移（alembic 或自写迁移脚本）；仓储接口。
- `ledger/`：append-only 写入、哈希链、校验命令。
- `artifacts`：内容寻址存储、manifest 规范化与哈希。
- `cli/`：响应信封、退出码、角色解析、审计事件写入。
- `config/`：`splits.yaml`、`gate_policy.yaml`、`models.yaml`、`costs.yaml` 的加载与版本号。
- 存储根目录配置：`ALPHASIEVE_HOT_ROOT`、`ALPHASIEVE_STORE_ROOT`、`ALPHASIEVE_ARCHIVE_ROOT`；Ceph 挂载保护与 SQLite 定时备份（见 [03-data.md](03-data.md) §8）。
- 合成 fixture panel 生成器（`tests/fixtures/synth.py`），供 T1、T2、T4 使用。

实现状态（2026-09-26）：已完成。Ceph 挂载保护以“挂载点必须是 mountpoint”检查实现；SQLite 定时备份随 M3 orchestrator 实现。

验收：
- `alphasieve ledger verify --json` 能检测被篡改的记录（测试中构造篡改）。
- 以 agent 角色调用 human 专属命令返回退出码 4。
- 同一 manifest 两次写入得到相同 `artifact_id`。

## M1 · 数据（M）

目标：可信的 A 股日频 panel，三个区间物理隔离。

任务：
- BaoStock Provider（D-18）：按 [03-data.md](03-data.md) §1 的接口拉取，写 raw Parquet 与 `data_snapshots`，多进程、断点续传。
- panel 构建：后复权价格、可交易性字段（推算涨跌停）、中证 800 成分 PIT、证监会行业（非 PIT）、基本面按公告日对齐。
- 标签：`label_{1,5,10,20}d`（T+1 开盘到 T+1+h 开盘），不可买入样本置缺失。
- 区间切分与 embargo；dev / holdout / fresh 分目录物化；data 层按角色访问。
- 数据质量检查与报告；`data status`、`data describe`、`data sample` 命令。
- 原始数据权威副本写入 Ceph，本地物化工作 panel；跨源对账（westock-data 或 Qlib 社区数据抽样）。
- 已知事件清单 `tests/data_events.yaml`。
- 每日增量更新任务（先以脚本形式，M3 接入 orchestrator）。

验收：
- 合成数据上覆盖停牌、涨停开盘、ST、拆股、新股、退市、成分 PIT、财务 PIT、embargo（`tests/test_m1_data.py`）。
- 真实数据上核对已知事件：2015-07 股灾大面积停牌、2020-02-03 大面积跌停开盘、国庆休市、中国平安连续在沪深 300、沪深 300 成分变化集中在 1/7 月快照（对应 6/12 月调样）、推算涨停价与实际涨停收盘一致、茅台除息日复权连续（`tests/test_real_data.py`）。
- 与 westock-data 抽样对账日价格变动与成交量。
- 以 agent 角色读取 holdout 区间被拒绝；holdout 目录权限为 0700。
- 基本面字段在公告日之后的第一个交易日才更新（不变量测试）。

实现状态（2026-09-26）：已完成。中证 1000、申万 PIT 行业、fresh 区间物化与每日增量调度推迟（后者随 M3 orchestrator）。

## M2 · 因子评估内核（M）

目标：一个候选从 YAML 到 L0–L2 结果、全部入账。

任务：
- 因子 DSL：解析器、类型检查、白名单、复杂度度量、规范化与 `candidate_hash`（移植 FactorMiner 算子与表达式树并裁剪）。
- 评估器：覆盖率、RankIC、ICIR、正比例、分组收益、多头超额、换手代理、库相关、中性化后 RankIC、成本后多头超额、四子窗口。
- 唯一评估入口 + ledger 写入；L0、L1、L2 gate（边际贡献先用 ridge 单模型）。
- 评估不变量测试：指标只在声明区间计算；标签不前视（打乱未来标签后 IC 应接近 0）；embargo 生效；同输入同输出。
- 阈值校准：用一组基础量价因子（Alpha158 子集）在 dev 区间的分布校准 L1/L2 阈值，写入 `gate_policy.yaml` v1；跑首次 T6 校准（零假设模拟与植入信号）。
- 搜索空间：`SearchSpace` 配置与版本、派生变量库 v1、覆盖坐标与候选落格、E2 模板（残差动量、特质波动、Amihud 等）、种子库（Alpha158、GTJA191）（见 [11-factor-search-space.md](11-factor-search-space.md)）。
- B1、B2 回测实现（见 [13-backtest.md](13-backtest.md)）。

实现状态（2026-09-26）：DSL（36 个算子、L0 白名单与复杂度检查、规范化哈希）、派生变量 dv1、SearchSpace 与落格、评估器（L1 指标、B2、中性化、四子窗口、ridge 边际贡献）、唯一评估入口与 ledger、L0–L2 gate、状态机、`factor` / `library` / `gate calibrate` 命令、18 个经典量价种子因子已完成。推迟：E2 模板、Alpha158 / GTJA191 种子库、`gate_policy.yaml` v1（校准报告只给建议值，修改需人工 review）。
- 命令：`factor validate`、`factor eval`、`factor show/list`、`library list/corr`、`ledger stats`。

验收：
- 10 个手写的经典因子（反转、动量、低波、换手、估值等）跑通，指标与独立 notebook 计算一致（误差 < 1e-6）。
- 故意构造的前视因子无法通过 L0（无法表达）；构造的“全样本标准化”被拒绝。
- 重复提交同一哈希会新增 trial 但不新增因子版本。

## F1 · 前端只读（M）

依赖：M2。

任务：
- `web/` 工程初始化（Vite、TanStack、Tailwind、shadcn/ui、ECharts、openapi-typescript）。
- API：`serve` 命令、认证、`/factors`、`/ledger`、`/data/status`、`/overview`（简版）。
- 页面：总览（简版）、因子库列表与详情（定义、证据、相关性、trial 历史）、Ledger、数据页。
- 通用组件：EvidenceBadge、StateBadge、MetricTable、ExpressionView。

验收：
- 在浏览器中查看 M2 生成的因子，指标与 CLI 输出一致；证据等级标签在所有指标旁可见。

## M3 · Agent 循环 v1（L）

目标：agent 在 dev 窗口自主跑一个完整 campaign 内环。

任务：
- Campaign / Turn / Directive / AgentRequest 对象与命令；停止条件与预算。
- orchestrator：任务表、调度循环、并发上限、异常处理与自动暂停。
- 执行器抽象 `AgentExecutor` 与本机后端：Claude Code 与 Codex；工具权限配置；独立操作系统用户运行；transcript 采集与费用统计（见 [14-agent-execution.md](14-agent-execution.md)）。
- campaign 创建时选择搜索空间格子；brief.md 渲染 SearchSpace 可读版本。
- workspace 模板：`program.md.j2`、brief / memory / directives 刷新；turn 后自动 commit。
- 记忆 v1：成功模板、禁区、洞察的提炼与展示；`memory show`。
- 事件表与 SSE 推送（为 F2 准备）。
- 停滞诊断规则 v1；日报生成。

验收：
- 在中证 1000、5 日标签上跑一个预算 200 trial 的 campaign，无人值守完成内环，所有 trial 可追溯到 turn 与 commit。
- 红队测试：在 program.md 之外诱导 agent 读取 holdout 文件，操作被拒绝并留下审计记录。
- 连续 3 个 turn 失败时 campaign 自动暂停并发出告警。

## M4 · 稳健性、搜索折扣与留出（M）

目标：打通 L3、L4 与人工评审。

任务：
- L2 完善：参考模型组（ridge、LightGBM、LambdaRank）边际贡献、walk-forward、default-first 参数规则与邻域救援计数。
- L3：DSR（试验数与方差取自 ledger）、同批 BH-FDR；批次 gate 作业。
- shortlist 锁定与记忆冻结；HoldoutRequest；holdout 评估作业；读取预算与污染判定。
- Review Packet 生成（Markdown + 结构化 JSON）；评审决定与 PromotionRecord；状态机完整实现。
- 命令：`shortlist lock`、`holdout request/approve/reject`、`review show/decide`。
- E3 受限程序因子：PITView、静态检查、截断不变性测试、沙箱资源上限。
- L3 的 T6 校准（贪心搜索模拟）。

验收：
- 端到端：campaign 结束 → shortlist → 审批 → holdout 评估 → Review Packet → 决定，全程状态与事件正确。
- 第二次读取同一 shortlist 的 holdout 被拒绝；超预算读取导致 `holdout_contaminated`。
- holdout 指标不出现在 agent 可访问的任何命令输出与 workspace 文件中（自动化检查）。

## M5 · Nexus 广度搜索（M）

依赖：M4；数据合规确认（[10-decisions.md](10-decisions.md) Q-9）。

目标：在 Nexus Cloud 上并行运行多个 miner 任务，按覆盖矩阵做广度探索，本机权威重验。

任务：
- Nexus 执行器：任务镜像构建（alphasieve 包 + dev panel 快照，构建后扫描确认不含 holdout / fresh）、batch 提交、轮询、collect。
- 任务内：沙盒 CLI、任务内 trial 日志（哈希链）、任务内 verifier。
- 本机 ingest：任务内全部 trial 写入权威 ledger；batch 级 DSR 计数；canonical 重验与不一致标记。
- planner：按覆盖矩阵为 batch 选格子。
- 参考 scicomp-foundry 的 authoring factory 与 cloud measure 工具链。

验收：
- 用假任务结果的集成测试：日志缺失或哈希链断裂的任务，其候选全部作废。
- 一个 20 任务的真实 batch 跑通，所有 trial 可追溯到任务、模型、镜像 digest；canonical 重验与云端结论不一致的候选被标记。

## F2 · 前端交互（L）

依赖：M3、M4。

任务：
- Campaign 列表与详情全部标签页（进展漏斗、实时 agent 活动、候选、搜索强度、预算、报告、时间线）。
- 创建 campaign 表单；campaign 控制操作。
- 指令面板（含防泄漏检查提示）。
- 审批中心；HoldoutRequest 决定页；Review Packet 评审页；Agent 请求回复。
- SSE 订阅与 query 失效刷新；停滞诊断展示；日报查看。
- 搜索空间覆盖矩阵视图（数据域 × 变换形态 × 时间尺度），campaign 创建时按格子选择范围。
- 企业微信通知。

验收：
- 场景 S1–S5、S7（见 [01-product.md](01-product.md) §4）全部可在前端完成，且与 CLI 结果一致。
- 关键流程的 Playwright 测试通过。

## M6 · 策略与模型（M）

目标：因子库转化为指数增强策略，模型随因子库滚动重训。

任务：
- StrategySpec 实现；指数增强组合构建（Top-K 与约束优化）。
- A 股执行模拟：T+1、涨跌停与停牌顺延、成本与冲击。
- 固定配置模型与月度滚动重训作业；影子特征物化。
- `strategy backtest` 命令；策略 artifact。

验收：
- 以 3–5 个手写因子构建的策略回测，与独立实现的结果一致；执行约束的单元测试覆盖涨停、停牌、T+1。

## M7 · 前瞻与 paper（M）

目标：L5 前瞻验证与 paper 组合追踪。

任务：
- FreshCohort：锁定、每日更新、最短观察期、cohort 级统计（HAC、BH）。
- 信号分池：机器 / 人工 / LLM 主观信号接入与前瞻追踪。
- regime 信任门与回撤控制叠加层。
- paper 组合：按 StrategySpec 每日生成目标持仓并模拟成交。
- 每日数据更新 → fresh 计算的完整调度。

验收：
- 连续运行 20 个交易日无人工干预；fresh 数据无回填（不变量测试）。
- `fresh_supported` 进入 paper 需要审批，且审批记录完整。

## F3 · 前端完整（M）

依赖：M6、M7。

任务：策略页、前瞻页（cohort 曲线、分池对比、regime 状态）、记忆页、设置页。

## M8 · 扩展（按需）

按 [design/agent-loop-verification.md](../../design/agent-loop-verification.md) §3.2 与 [design/strategy-scope.md](../../design/strategy-scope.md) §5 的顺序逐项引入：

- 模型设计的自主循环（目标、损失、集成），与因子交替优化（bandit 调度器、统一 ledger、双预算）。
- 事件驱动：公告与研报事件抽取、PIT 时间戳、事件信号化。
- 行业 / ETF 轮动配置层。
- Reviewer agent。
- 是否引入 QuantDesk 式平台外壳（见 [10-decisions.md](10-decisions.md)）。

M0–M2 的验收记录见 [acceptance-m0-m2.md](acceptance-m0-m2.md)。

## 通用完成标准（每个里程碑都适用）

- 新增命令都有 `--json` 输出与退出码测试。
- 新增状态变更都写事件，并在 ledger / 审计中可追溯。
- 涉及评估或数据的改动都有不变量测试。
- 更新本目录对应文档；接口变化同步 [06-interfaces.md](06-interfaces.md)。
- `uv run pytest` 全部通过；前端 `pnpm test` 通过（有前端改动时）。

## 近期第一步

1. M0：contracts 与 SQLite 仓储、ledger 哈希链、CLI 信封与角色。
2. M1：先拉取 2012 年至今的中证 1000 成分股日线、复权、涨跌停、停牌与指数成分，构建 dev panel。
3. M2：DSL 解析器与 5 个核心指标，跑通第一个经典反转因子的 `factor eval`。
