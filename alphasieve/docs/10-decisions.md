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
- 每日增量数据更新：本阶段即安装 systemd timer（交易日收盘后拉取核心数据）。实现时的更正见 D-22 第 8 条：只更新原始数据，不重建 panel。

**D-22 M3 / M4 / F1 实现中的决定（2026-09-27）**
1. gate_policy 发布 v1：L1、L2 阈值与 v0 完全相同（D-19 的 L1 重新校准仍待做），只新增 L3、L4 两节。
   - L3：用日 RankIC 序列的 Deflated Sharpe。ICIR 充当 Sharpe；试验数 N 取本 campaign 的全部 dev trial；方差取这些 trial 的 ICIR 方差；偏度、峰度取候选自身的 IC 序列；标签重叠，有效样本量取有效日数除以预测周期。p ≤ 0.05，并在本 campaign 所有 L2 通过的候选之间做 BH（q = 0.10），shortlist 最多 10 个。
   - L4：holdout 上方向一致的平均 RankIC > 0，且 holdout ICIR ≥ 0.5 × dev ICIR。同一候选此前读过 holdout 的判为 `holdout_contaminated`。
2. 单个 turn 的 trial 配额为 max(3, min(10, ⌈2 × 剩余 trial / 剩余 turn⌉))，由 orchestrator 通过环境变量传给 CLI，CLI 在评估入口强制执行。
3. 同一 campaign 内 agent 重复提交同一候选（按表达式规范化后的哈希判断）会被拒绝，不记 trial。评估结果是确定的，重复评估只会抬高 L3 的试验数。实测中 Codex 并行提交过两次相同候选。
4. 整机同时最多 2 个评估（`ALPHASIEVE_EVAL_SLOTS`）：每个评估进程要把 dev panel 载入内存（约 3 GB 起）。
5. agent 能看到的范围：`shortlist_locked` 之后的因子状态一律显示为 `batch_concluded`；`campaign status` 不含 holdout 信息；记忆只由 dev 证据生成，campaign 结题时冻结；指令写入前检查是否提到 holdout、前瞻、评审相关的词和因子，命中即拒绝。
6. 进程级隔离的实现（D-21）：
   - Claude Code：`--bare`、`dontAsk`、工具白名单（`alphasieve` 命令、只读 git 命令、工作区内的 Read，Write 仅限 candidates / notes / reports）。实测读工作区外文件（Bash 与 Read 两种方式）都被拒绝。
   - Codex：`workspace-write` 沙箱，关闭网络，可写目录只有 state、cache、artifacts，不加载用户配置与 rules。它的沙箱不限制读取，这是 D-21 已接受的残余风险。
   - 补偿措施：每个 turn 结束后比较受保护状态（holdout 请求、决定、评审包、非 dev trial、shortlist、配置哈希），扫描 agent 执行过的命令与读取的路径（环境变量覆盖、holdout / fresh panel 路径、直接访问数据库、凭据、仅限人工的命令、策略文件），并检查带本 turn 标记却以非 agent 角色执行的命令。命中任一项，该 turn 记为 `integrity_violation`，campaign 自动暂停，该 turn 的经验不写入记忆。
7. 模型容量不足之类的暂时性错误按失败 turn 计。连续 3 个失败 turn 暂停 campaign；两种 agent 交替运行，一种暂时不可用不会让 campaign 停下。
8. 更正 D-21：每日更新只增量同步原始数据并镜像到 Ceph，不重建 panel。dev 窗口已固定，holdout 窗口止于 2026-09-25，fresh panel 要到 M7 才需要；每天重建只会改变 panel 签名，没有收益。
9. 前端只读。2026-09-27 按用户要求改为免密访问（web 服务设置 `ALPHASIEVE_WEB_AUTH=none`），取代 D-21 中“需要用户名密码登录”的约定：能访问本机 8720 端口的人都能看到研究结果与 ledger，但没有任何写操作。改回登录只需去掉这个环境变量。原先的做法：HTTP Basic 认证，凭据由系统生成，保存在 `/data/alphasieve/web.credentials`（权限 600）。静态 JS / CSS 不需认证（不含数据），所有 API 都需认证。技术栈比 07 文档简化：React + Vite + ECharts，没有用 TanStack、Tailwind、shadcn。

**D-23 平台 Ray 集群用于批量计算与模型训练（2026-09-27）**
- 提交方式：`deploy/ray/submit.sh <任务名> <alphasieve 参数...>`，默认 `RAY_ADDRESS=http://28.83.35.117:8081`。代码随任务上传（排除 `.venv`、`node_modules`、前端构建产物）；平台上用 uv 和腾讯 PyPI 镜像按 `pyproject.toml` 建环境，同一依赖版本会复用。任务以 system 角色运行。
- 集群实测：在线 4 个节点，每个节点 376 核、8 张 H20、约 2 TB 内存；能访问 BaoStock 与 AIHub，访问不到本机 `9.134.61.161`。本机的 `/data`、`/mnt/private_felixjjiang` 在平台上没有挂载；两边同名的 `/apdcephfs*` 路径实测不是同一个存储。
- 共享存储：`/taijifs_zw35/r2/felixjjiang/alphasieve/`，本机与平台读写互通。
  - `hot/`：平台任务的热目录，只放 dev panel，平台任务自己的状态库也在这里；
  - `store/`：平台任务的 artifact；
  - `models/`：模型；
  - `runs/<job_id>/`：每个任务的结果。
- 本机写入 taijifs 较慢（700MB 约 7 分钟），大文件尽量在平台一侧生成。
- 隔离：holdout 与 fresh 数据不离开本机。提交脚本发现远端有 holdout 或 fresh panel 就拒绝运行。平台任务的状态库与本机主 ledger 是分开的：平台上做的评估不计入 campaign 的 trial，也不能代替本机评估入口的记账。以后如果要把平台评估纳入 campaign，需要先定义合并规则。
- 适用范围：门槛校准与随机因子模拟、模型层滚动训练、M5 广度搜索。agent 循环仍然在本机运行。
- 实验追踪（RunLab，`http://runlab.woa.com`，org=taiji）：
  - 实测 RunLab 兼容 WandB 协议（wandb SDK 加 `/graphql`，太极账号登录，API Key 在页面菜单“API Key”中生成）；
  - 平台节点用 http 能访问，https 连不上；本机访问返回 403，因此只有平台任务往 RunLab 写数据。
  - 平台任务里预置的是别人任务的 SwanLab 配置（项目 `lzn-debug`，指向 `train-exp.taiji.woa.com`），没有任何 RunLab / WandB 配置，也不使用这份 SwanLab 配置。
  - 接入方式：把 `WANDB_API_KEY=...` 写进 `/taijifs_zw35/r2/felixjjiang/alphasieve/secrets/runlab.env`（目录权限 700）。提交脚本在节点上读取这个文件，开启 `ALPHASIEVE_TRACKING=runlab`；密钥不走 Ray 任务参数，因为共享 dashboard 上的任务参数所有人都能看到。
  - 每个平台任务结束后记一个 run：名字是任务号，config 为命令参数与代码版本，summary 为结果中的全部数值。holdout、评审、campaign 相关命令和 agent 角色一律不记。

**D-24 预测周期按信号类型设定；L2 的成本后超额只记录不拦截（2026-09-27，关闭 Q-12、Q-13）**
- 预测周期：`search_space.yaml` 的 `default_horizon` 按数据领域给出默认值：量价、成交量、换手流动性 5 日；市值估值、盈利、成长 20 日。
  - campaign 不写 `horizon` 时，取重点格子所在领域默认值的最大值，写入规格后固定不变；
  - 同一 campaign 只有一个周期，候选周期不一致判为 L0 的 `campaign_horizon` 失败。周期不一致的提交不算重复提交。
- L2：gate_policy v2 把 `cost_adjusted_excess` 标为只记录（`informational`）。照常计算和记录，但不影响是否通过，也不计入失败原因。能否扣成本后赚钱交给组合层与执行层判断（15 §4 P-2）。agent 规程同步修改。
- 已有的试点 `pilot-fundamental-001` 按 5 日、v1 口径运行，不追溯修改，由采用新口径的 campaign 取代（见 [16-scaling.md](16-scaling.md) S-5）。

**D-25 常驻评估服务（S-3，2026-09-27）**
- `evaluate_spec` 拆成三段：
  - 本机准备：校验、登记因子、写入“开始”记录；
  - 纯计算 `compute_job`：输入是可序列化的任务描述，包含库成员、配置与邻域计数，计算时不访问状态库；
  - 本机收尾：写 artifact、写入“完成”记录、推进因子状态。
- ledger 只在本机写入。
- 队列是目录协议（`pending/running/done/workers`）。本机队列在 state 目录下的 `evalq/`，Codex 沙箱里可写。
  - `alphasieve evalsvc worker` 常驻内存，载入一次 panel 后持续处理任务；
  - `evalsvc bridge` 把本机任务转发到 taijifs 上的 `evalq/`，由平台 worker（`deploy/ray/start_workers.sh`）处理。
  - `factor eval` 发现有活的 worker 或桥接进程就走队列，否则在进程内计算（`ALPHASIEVE_EVAL_QUEUE=auto|off|require`）。
- worker 只算 dev 层任务。心跳由后台线程每 10 秒写一次；桥接进程在内存里记住远端心跳，不会因为 taijifs 列目录偶尔读不到文件而误判 worker 已死。
- 库因子缓存改为原子写入，读到残缺文件会自动重算。
- 提交脚本每次都把包重新指向本次上传的代码。此前平台上的可编辑安装一直指向第一次上传的目录，导致修复没有生效。
- 实测：
  - 本机常驻 worker：只到 L1 的评估 11–17 秒，到 L2 的评估 19–34 秒（CLI 端到端）；
  - 平台 4 个 worker：计算时间 L1 2.6–10 秒、L2 22–32 秒，经 taijifs 队列的端到端时间 12–44 秒，4 个评估并行完成。
- 部署：`alphasieve-evalworker.service`（本机 2 个进程）已启用；`alphasieve-evalbridge.service` 只在平台 worker 运行时启动。
- 残余风险：agent 理论上可以伪造队列里的结果文件。完整性扫描已加入 `evalq/` 与 taijifs 路径（D-22 第 6 条的延伸）。

**D-26 股票池可配置；全 A 加 2005 年起的历史（S-4，2026-09-27）**
- `configs/universes.yaml` 定义股票池：
  - `csi800`：原有定义，路径不变；
  - `ashare_all`：沪深两市全部 A 股，包含已退市股票以避免幸存者偏差。股票池按规则确定：上市满 250 个交易日、非 ST、未停牌，且按 20 日平均成交额剔除每天最不活跃的 20%。历史从 2005 年起，dev 期从 2006-01-01 开始，holdout 与 fresh 窗口仍取自 `splits.yaml`；
  - `hs300_2020`：见 D-28。
- 同步、panel 构建、数据读取、评估、campaign 都按股票池区分。campaign 的 `universe` 必须在配置里，候选的股票池与 campaign 不一致判为 L0 的 `campaign_universe` 失败。worker 按股票池缓存 panel。
- 数据获取：
  - 平台节点访问不到外网（BaoStock 的 10030 与 80 端口都超时），所以原始数据在本机同步，平台只负责计算。
  - BaoStock 服务端有限速：单只股票从 2005 年起的日线要 12–17 秒，并发时每个会话都会变慢，全 A 日线约需 4–5 小时，财报约 96 万次请求，需要更久。
  - 因此分阶段进行：先完成日线（含 PE/PB/PS），据此构建第一版全 A panel；财报同步完成后再重建。holdout panel 只在本机构建（`data build-panel --tiers holdout`），平台上只构建 dev。
- 实现上顺带修复：并发连接同时执行数据库迁移的竞争（读取版本号改到写锁内）。

**D-27 并行 lane 与模板展开（S-5，2026-09-27）**
- campaign 的 `lanes`（1–16）表示每轮同时运行的 turn 数，重点格子轮流分给各 lane。每个 lane 有自己的工作区（`lane-<k>`），orchestrator 用线程并发执行，每个线程使用独立的数据库连接。
- ledger 的 trial 记录增加 `turn_id` 列，不参与哈希，旧记录照常能校验。
  - 单 turn 配额按 `turn_id` 计数，替代原来的“campaign 已用 trial 数 + 配额”上限；
  - campaign 总预算改为统计所有已开始的 trial（包括还在进行中的），并发时不会超出。
- `factor expand`：agent 写一个模板（表达式里带 `{参数}`，加上 `grid`），最多展开 12 个。第一个是默认版本，其余记为它的邻域变体。有评估队列时并发计算；每个变体都计入 trial，受 L2 的邻域次数限制。
- 实测（`lanes-smoke-001`，GPT-5.5，3 个 lane）：3 个 turn 同时启动，各用 4 个 trial，合计 12 个，产生 2 个新的 L2 通过。同时暴露并修复了一个问题：holdout 读取预算为 0 时结题失败，现在会锁定 shortlist 后直接结束。
- GPT-6 Sol 在 2026-09-27 20:25 前后再次被账号级拦截（任意提示都被拒），`fundamental-20d-001` 已暂停，等待决定模型。

**D-28 模型层与组合层、事件与日内数据、程序化搜索（S-6 / S-7，2026-09-27）**
- `strategy backtest`：
  - 模型层：以因子库为特征，按月滚动训练 ridge 或 LightGBM，训练与测试之间留出标签长度的间隔，输出样本外综合分数；
  - 组合层：中证 800 增强，行业偏离不超过 3%、个股上限 2%、单期换手上限 30%、主动市值暴露不超过 0.3 个标准差；
  - 执行：简化版 B3，次日开盘成交，涨停或停牌买不进、跌停或停牌卖不出，佣金、印花税、滑点分项计算。
  - 只用 dev 层数据。作为平台任务运行，打分与权重写入 taijifs 的 `store/models/<run_id>/`，汇总与净值序列写入 RunLab。
  - 首轮实测（中证 800，20 日周期，修复前的组合代码）：
    - ridge：IC 0.077（不如等权合成的 0.083），信息比率 0.49；
    - LightGBM：IC 0.088、ICIR 0.70，信息比率 0.93。
  - 同时暴露两个组合层问题：主动市值暴露约 −1.1 个标准差（严重偏小盘）；换手混合后行业偏离达到 4.8%。已补上市值约束，并在换手混合之后重新施加行业约束，需要重跑。
- 事件数据：BaoStock 的业绩预告与业绩快报，按发布日之后的第一个交易日对齐，最多沿用 60 个交易日。
  - 派生 6 个字段：`fc_chg_mid`、`fc_positive`、`fc_age`、`ex_eps_chg`、`ex_roe`、`ex_gr_yoy`，属于新的 `events` 领域，默认周期 20 日；
  - 中证 800 已同步：预告 38,589 条，快报 10,532 条。
- 日内特征：BaoStock 的分钟线约从 2020 年开始才有。同步时聚合成 4 个日频字段：`rv_intraday`、`tail30_vol_share`、`open30_ret`、`updown_vol_share`，属于新的 `intraday` 领域，默认周期 5 日；原始分钟线不保存。在限速下，5 分钟线（约 3,600 万行）需要约 20 小时，因此 `hs300_2020` 改用 15 分钟线（`intraday_minutes: 15`，每天 16 根），最后 30 分钟和开盘 30 分钟各取 2 根。
  - 由于覆盖期短，日内特征只放在单独的 `hs300_2020` 范围里（沪深 300 成分股，dev 期从 2020-03 开始），不混入 2012 年起的中证 800，否则覆盖率检查过不了。
- search_space 升级到 v2，新增 `events` 与 `intraday` 两个领域。
- 程序化搜索：`search run` 只能在 agent 类型为 `program` 的 campaign 里运行，与 LLM campaign 分开计算试验数。
  - 支持随机生成和进化（按 dev 上的 ICIR 选择、变异、交叉）两种方式；
  - 候选先做结构校验、按规范化哈希去重，不合法或重复的不占 trial；
  - 评估通过队列并发执行，可以交给平台 worker；预算用完后自动结题。

**D-29 agent 模型分档与 AIHub 通道（2026-09-27）**
- 按用户建议：高端模型用 GPT-6 Sol，低端模型用 DeepSeek V4 Pro。AIHub key（project 228）更新后保存在 `/data/alphasieve/secrets.env`（权限 600），Claude Opus 5 也恢复可用。
- 实测：
  - DeepSeek V4 Pro 在 AIHub 上的模型名是 `deepseek-v4-pro`，支持 `reasoning_effort=max`。没有 `deepseek-v4-pro-max` 这个模型名；
  - AIHub 的 Responses 接口解析不了 Codex 的请求（报 unhashable dict），而 Codex 已不支持 Chat Completions。因此 DeepSeek 改走 Claude Code 通道（AIHub 的 Anthropic 兼容接口 `/v1/messages`），读文件、执行 `alphasieve` 命令都正常。Claude Code 报告的费用按 Anthropic 价格估算，对 DeepSeek 不准；
  - GPT-6 Sol：Codex 自身账号仍被策略拦截（任意提示都被拒）。AIHub 上的 `gpt-6-sol` 返回上游认证错误（AIHub 侧的上游 key 无效），暂时两条通道都不可用。
- orchestrator 按“执行器/模型”记录不可用状态：
  - 策略拦截、容量不足、限流属于暂时性错误，该模型暂停 30 分钟后自动恢复；
  - 鉴权失败、预算耗尽为永久停用；
  - 所有模型都在暂停期时，orchestrator 等到最早的恢复时间再继续；全部永久停用才暂停 campaign。
- 新 campaign 按轮转分档：agents 列表写 1 份 GPT-6 Sol、2 份 DeepSeek V4 Pro，高端约占 1/3 的 turn；GPT-6 Sol 不可用时由 DeepSeek 顶上。

**D-30 westock 全报表与资金流向（2026-09-28）**
- 财报：westock `finance` 提供全 A 三大报表（利润表、资产负债表、现金流量表，2000 年起），含单季值与 TTM。全 A 5534 只一次同步约 3 分钟。
- PIT 核对（与 BaoStock 的首次披露对比）：
  - 公告日：抽 60 只、2012–2022 年的 2177 个报告期，westock `InfoPublDate` 与 BaoStock 首次 `pubDate` 一致的占 99.6%，其余 0.4% 更早，没有更晚的。因此以 `InfoPublDate` 作为生效日：公告日之后的第一个交易日起可用，三张表取最晚的公告日。
  - 数值：抽 40 只、2015–2020 年的 316 个报告期，比较资产负债率。半年报 100% 一致；年报有 33% 不一致，差异中位数 0.4%，90% 分位 15%。westock 每个报告期只保留一个版本，年报资产负债表有一部分是之后追溯调整过的数值。利润表毛利率的差异在四个季度均匀分布（约 11%），更像口径差异，没有追溯调整的迹象。
  - 结论：公告日可靠；年报资产负债表存在残余前视，在 panel `warnings` 里注明。dev 上的结果因此可能略偏乐观，holdout 与 fresh 区间的检验不受影响。
- 资金流向：westock `fund flow` 按单笔金额分档（超大单、大单、中单、小单）给出每日净流入，2020 年起才有数据。单次请求有行数上限，按年分段拉取；全 A 2020 至今 778 万行，约 17 分钟。字段除以当日成交额，得到 `mf_*` 比例。
- 批量请求会在负载高时静默丢掉部分股票（首轮有 233 个“股票-年份”空洞，财报少了约 100 个文件）。provider 对批量结果里缺失的代码逐只补查，补查后空洞为 0。
- 融资融券：westock 的 `fund margin` 每次只能查一只股票、一个日期（历史从 2018 年起），全 A 逐日回补需要数百万次调用，不可行。折中：
  - 对 `hs300_2020` 的约 505 只股票按周回补（每周最后一个交易日），2019-10 至今约 18 万次调用；两融余额变化慢，周频足够；
  - 交易所次日早上公布前一日两融数据，所以快照在其日期之后的第一个交易日才可用，最多沿用 10 个交易日；
  - 全 A 每天做一次快照，从首次运行起积累；全 A 的历史回补需要付费数据源（见 [17-data-vendors.md](17-data-vendors.md)）。
  - 搜索空间新增 `margin` 域（默认预测周期 20 天）：融资余额/流通市值、融资余额 4 周变化、融资买入占买入加偿还的比例、融券余额/融资余额。
- 搜索空间 v3 新增 `fin_quality`、`fin_growth`（默认预测周期 20 天）、`fund_flow`（5 天）和 `margin`（20 天）。新增股票池 `ashare_2020`：全 A 规则股票池，dev 为 2020-03 至 2022-12，与资金流向的覆盖区间一致。
- 平台上只放 dev 区间的原始数据：上传到 taijifs 的 tar 截断到 2022-12-31。检查时发现之前的全 A tar 含有 2023 年以后的日线（S-4 的疏漏），已换成截断版并删除旧文件。

**D-31 训练任务与 mandate 的实现口径（2026-09-29）**
- 按 [19-training-tasks.md](19-training-tasks.md) 实现训练任务（`src/alphasieve/training/`），四个 mandate 的配置在 `configs/training_tasks/`。一次完整运行是一个策略层 trial；换任何配置都是新 trial。
- 验收基准用全收益口径：组合收益是后复权的全收益，而指数是价格指数。A、D 对照中证 500 成员按流通市值加权的全收益代理，C、B 对照各自股票池的全收益基准；对价格指数的结果作为参考一并报告。
- 指数增强的组合构建增加线性规划选项（`construction: lp`）：在个股主动权重、行业、市值、beta、单边换手这些线性约束下最大化分数加权持仓。原来的启发式方法会向基准混合，实际在复制基准。
- ledger：`trials` 表新增 `layer` 和 `scope`，只有策略层记录把这两列纳入哈希；新增 `strategy_holdout_requests` 表；启用 `void` 记录，只能作废重复的结果记录，被作废的行仍留在哈希链里。按 mandate 计算搜索折扣：零假设下 N 次尝试的最优年化信息比率期望，从实测值中扣除。
- 策略层 holdout 每个 mandate 一次读取，只能由人工申请和批准，批准后按锁定的配置（同一配置哈希、同一冻结特征集）重跑。
- dev 结果与阻塞项见 [acceptance-training.md](acceptance-training.md)。

**D-32 训练第二轮：事件与因子接入 A、B 行业映射、D 真实期货（2026-09-30）**
- 按 [20-training-round2.md](20-training-round2.md) 实现，dev 结果见 [acceptance-training.md](acceptance-training.md) §2a；四项都没有达到验收门槛。
- A 保持 v4。C 事件分数（按 1 日滞后、月初前 250 日分位）和 13 个衍生因子提高了 RankIC，但没有提高组合 IR。A 的策略层预算（N ≤ 9）已用完，新的 A 配置需要先扩大预算并记录理由。
- 衍生因子的筛选只用覆盖率和与现有特征的相关性，不看收益；筛选报告存于 store 的 `models/_screens/`。
- B 的 ETF 映射固定在 2026-09-30 的当前前二十大持仓和当前证监会行业，对全历史使用；bundle 冻结持仓快照的 sha256。headline 和验收只看真实 ETF 段，新增 RankIC ≥ 0.03。
- 股指期货数据用新浪期货接口：IC 连续合约 2017-01 起，逐合约 2019-04 起。D 只在逐合约精确段判定。精确段对冲后为负收益，主要是贴水成本（对“多股票、空期货”的组合，贴水是成本；18 §6 原先写反，已更正）。D 不再作为独立 mandate 推进，降级为 A 的风险管理模块；`basis_head` 不实现。
- 期货和 ETF 行业特征都只构建 dev 层。holdout 层由人工在批准读取前在本机构建，不上传平台。

**D-33 A 的组合构建预算与结论（2026-09-30）**
- 用户批准把 A 的策略层预算从 9 扩到 13，用于 [21-a-portfolio.md](21-a-portfolio.md) 的四个组合构建配置。各 mandate 的预算写入 `mandates.STRATEGY_TRIAL_BUDGET`（A 13、B 5、C 4、D 4），`start_trial` 超出即拒绝；再扩预算需要新的决策记录。
- 组合 trial 复用 A v4 冻结的样本外分数（bundle 冻结 sha256），不重训模型。
- 配置哈希对后来新增、取默认值的字段不敏感，已入账配置的哈希不随 schema 扩展而变化。
- 四个配置都没有通过验收。成本感知目标最接近（超额 5.82%，只差 6% 门槛），但按预注册规则停止 A 的组合研究；v4 仍是 A 的 dev 参考。继续成本感知方向需要新的预算。

**D-34 A 的成本感知续研预算（2026-10-02）**
- 用户批准 A 再增加恰好 2 个策略层 trial，累计预算从 N≤13 扩至 N≤15，按 [22-a-cost-aware.md](22-a-cost-aware.md) 的固定顺序执行 C1 `a_csi500_portfolio_cost_scale_v2`、C2 `a_csi500_portfolio_cost_capacity_v2`。两者分别只改因果 10 日收益尺度和按 20 亿设计规模定价冲击，不互相继承，复用 v4 冻结分数；每配置至多一次 started，失败和 abandoned 也占预算。
- 沿用原 A 验收与 [22-a-cost-aware.md](22-a-cost-aware.md) §4 的稳健性、容量筛选和停止规则。合格候选按统一 N≤15 中的 **N=15** 搜索折扣后 IR 排序；IR 差不超过 0.02 时依次比较 20 亿容量下降、5 亿实际单边换手及 C1→C2 固定顺序。两者都不通过即再次停止 A 组合研究，无救援、调参、换种子或重跑名额；有胜出者也结束本轮，留待人工评审，不自动读取 holdout。

**D-35 人工审批采用 SSH 签名（2026-10-02）**
- 策略与因子 holdout 申请、review 决定及 agent 请求回复，由 human 用笔记本私钥对一次性、短期有效的规范化挑战签名；服务端用 git 跟踪的 allowed_signers 公钥校验，默认要求签名。公钥为空时审批失败。
- 待签内容绑定目标、决定、当前证据哈希、nonce 和过期时间。签名与内容保存在独立的追加式批准链中，并锚定当时 trial 账本头；`ledger verify` 和状态库备份复核签名。历史无签名决定继续有效，界面注明机制上线前。
- 此机制提供不可伪造的 human 决定证据和事后审计。root 可改代码和本机状态库，因此不能保证本机记录不被删除；需结合外部备份核对完整性。理由和回复正文不在签名内容内。

**D-36 前瞻观察政策与部署重拟合（2026-10-02）**
- human 决定“模拟资金100w RMB，其他全部选择默认”。A 观察与 paper 账簿固定 100 万元人民币、初始 NAV=1、全现金、零申赎；基准为 CSI 500 PIT 前日流通市值加权全收益代理，sh.000905 价格指数单列参考。100 万元下参与率截断和平方根冲击接近零；前瞻结果不能证明 dev 的 5 亿/20 亿大资金容量。
- 先完成策略链路。是否登记 A v4 单一 diagnostic shadow 留待届时 human 签名决定，本决定不登记 cohort。批准仅 system、本机、固定参数与成熟标签的 operational_refit 部署例外：rolling 5 年、每月首个交易日重训，不年度选参；不得用于 dev 搜索或新 holdout 评估。
- 冻结 [23-forward-paper.md](23-forward-paper.md) §3.6 默认统计政策：因子 60 个成熟有效日、策略 120 个有效收益日、覆盖率 ≥95%、HAC 单侧 p≤0.05、BH q=0.10、三态裁决，证据不足不自动延长。forward Web 强制 human 认证；最多 2 worker、每 worker 8 线程/32 GiB，重训 2 小时、日任务 4 小时；关键缺口连续 2 个交易日暂停，超时目标不追补。真实启用仍逐 cohort 签名批准。
- 本机 agent 以 root 运行，独立 system 用户和目录 0700 均不能阻止 root 读取。真正隔离须把 agent 排除在运行机器或容器之外；是否接受同机 root 残余风险（沿 D-21/D-22 现状）或迁移，仍待 human 决定。当前不能宣称隔离完成。

**D-37 westock 研报作为卖方预测来源（2026-10-02）**
- 采用 westock 历史卖方研报提取年度 EPS 预测、评级与覆盖度，构建一致预期类普通因子；先按 L1–L4 既有流程评估，不改 mandate、训练配置或搜索预算。报告发布日期 T 的内容从 T 之后的第一个交易日起可用；每券商在回看窗口只取最新报告，不用当前 `consensus` 快照回填历史。
- 研报列表和正文原始 SQLite 仅保存在本机 `data/raw/westock/reports/`，解析结果另存 parquet。上传平台前只制作截至 dev 末日的截断副本；agent 不读取 holdout/fresh 研报或 panel。原始抓取库可继续增量追加，解析与同步不能覆盖历史快照。
- westock 的指数和申万行业成分只有当前快照、无官方权重。Tushare `report_rc` 不再是卖方预测的先决条件；是否以 10000 积分补充须按覆盖与口径抽检后由 human 决定。官方指数权重、2024 年以前全收益指数、PIT 申万行业历史仍需 2000 积分档或等效来源，购买决定仍由 human 做。

## 待定问题

| 编号 | 问题 | 影响 | 计划决定时间 |
|---|---|---|---|
| Q-1 | ~~数据源选择~~ 已决定：见 D-18。需要中证 1000 或申万 PIT 行业时再评估 Tushare / 商业数据 | — | 已关闭 |
| Q-2 | holdout 区间长度（默认 2023-01-01 至项目启动日）是否足够 | L4 统计功效 | M1 结束时 |
| Q-3 | ~~是否发布 gate_policy v1~~ 已决定：见 D-19；v1 已发布（L1/L2 不变，新增 L3/L4，见 D-22）。L1 在 L3 下的重新校准（T6）仍待做 | L1 通过率 | 试点 campaign 结束后 |
| Q-4 | ~~agent 默认模型与费用上限~~ 已决定：见 D-20（金额上限待试点实测后确定） | — | 已关闭 |
| Q-5 | ~~Researcher 与 Approver 是否分离~~ 已决定：暂由同一人担任，见 D-21 | — | 已关闭 |
| Q-6 | ~~部署与认证方式~~ 已决定：本机 IP + 登录，见 D-21 | — | 已关闭 |
| Q-7 | 通知渠道：试点阶段不做（D-21），F2 时再定 | 通知实现 | F2 |
| Q-8 | ~~paper 组合的资金规模与基准选择~~ 已决定：100 万元人民币及 CSI 500 PIT 前日流通市值加权全收益代理，见 D-36 | — | 已关闭 |
| Q-9 | dev panel 能否随任务镜像进入 Nexus Cloud 沙盒（数据授权与合规）。暂缓：M5 没有实施，广度计算改走平台 Ray 集群（D-23），dev panel 已在平台上，holdout 仍只在本机 | M5 能否启动 | 重启 M5 时 |
| Q-10 | westock-data 实测：`kline` 支持按日期范围查询，默认前复权（现金分红减法调整、送转按比例缩放），指定不复权报服务错误；已用于日价格变动与成交量对账。是否提供历史指数成分仍未确认 | 事件数据源与中证 1000 成分 | 事件驱动启动前 |
| Q-11 | E3 受限程序因子的沙箱实现：进程级限制还是容器 | M4 的 E3 任务 | M4 开始前 |
| Q-12 | ~~预测周期是否按信号类型设定~~ 已决定：见 D-24 | — | 已关闭 |
| Q-13 | ~~L2 成本后超额是否改为只记录~~ 已决定：见 D-24 | — | 已关闭 |
| Q-14 | ~~是否先做最小模型层与组合层，并把 holdout 的最终判断放在策略层~~ 已决定并实现：见 D-28、D-31（每个 mandate 一次人工批准的策略层 holdout 读取） | — | 已关闭 |
| Q-15 | ~~ledger 按层记账与风险模型 v0~~ 已决定：按层记账见 D-31。风险模型 v0 只实现了行业、市值、beta 约束，风格暴露与协方差的设计见 [25-risk-model.md](25-risk-model.md)，实现另行决定 | — | 已关闭 |
| Q-16 | ~~扩容顺序 S-1…S-7 各项是否采纳~~ 已全部采纳并实施：S-2 见 D-24，S-3 见 D-25，S-4 见 D-26，S-5 见 D-27，S-6 / S-7 见 D-28（S-6 后由 D-31 的训练任务取代），S-1 见 [16-scaling.md](16-scaling.md) §2.1 | — | 已关闭 |
