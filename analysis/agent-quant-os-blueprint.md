# Agent + Quant OS Blueprint

本文整理一个合格的 agent + quant OS 应该包含哪些组件、哪些 pipeline 节点必须有人类干预，以及在当前 code agent 与 harness 能力持续发展的背景下，什么设计路线更可持续。

这里的 agent + quant OS 不是“聊天机器人回答买什么股票”，也不是单一因子挖掘脚本。它应该是一个可持续运行的研究操作系统：能让 agent 提出假设、写代码、调工具、跑实验、沉淀记忆，同时让数据真值、回测真值、风控和交易权限保留在确定性系统与人工审批里。

## 1. 定义边界

一个系统只有同时满足以下条件，才算接近 agent + quant OS：

1. 有长期 workspace：研究不只存在于 chat history，而是有文件、实验、artifact、memory、报告和可恢复 session。
2. 有 typed tool surface：agent 通过 CLI JSON、MCP 或受控代码执行调用数据、因子评估、回测和报告生成。
3. 有结构化研究对象：factor、strategy、dataset、run、experiment、artifact、approval 都有 schema 和生命周期。
4. 有确定性评估链：数据对齐、特征计算、IC/RankIC、组合回测、交易成本、OOS 和反过拟合由程序完成。
5. 有 human gate：数据源、研究目标、promotion、paper/live、风险预算和异常处置需要人工参与。
6. 有审计和复盘：每次工具调用、代码 diff、指标、审批、失败原因都能追溯。

因此，`QuantDesk` 和 `langalpha` 是完整工作台形态的强参考；`QuantMind-yj_exp` 的 Factor Lab 是低频因子研究 OS 的强参考；`QuantGPT` 更像强因子引擎，不是完整 OS，但其 parser、anti-overfit 和 knowledge base 很有价值。

## 2. 核心组件

### 2.1 Workspace 与 Session 层

职责：

- 保存用户目标、研究线程、agent turn、artifact、报告和长期记忆。
- 让下一次研究能恢复上下文，而不是从 prompt 重新开始。
- 把 agent 的中间过程暴露给人类审查。

应该包含：

- `workspace_id`、`thread_id`、`experiment_id`。
- 可写文件区：代码、配置、临时数据、图表、报告。
- run log：agent message、tool call、stdout/stderr、error、retry。
- session resume：Claude/Codex 原生 session 或系统自己的 thread state。
- memory：成功模式、失败模式、因子 lineage、偏好、风险规则。

不应该做：

- 把完整历史无限塞进 prompt。
- 把 workspace 当作不可追溯的临时目录。
- 让 agent 在没有 artifact registry 的情况下随意覆盖实验结果。

### 2.2 Agent Adapter 层

职责：

- 把不同 agent runtime 接到同一套 quant harness。
- 支持 Codex、Claude Code、本地 LLM server、LangGraph 或其他模型后端。
- 避免系统强耦合某一个 CLI 的默认配置和会话目录。

常见形态：

- 原生 code agent：按 turn 启动 `claude` / `codex exec`，stdin 注入 prompt，stdout 解析 JSONL stream。
- LLM server：后端直接调用模型 API 或 LangGraph/deepagents，把工具和状态留在服务端。
- 混合形态：前期用 code agent，后期对长任务和多用户场景补 LLM server。

设计要求：

- agent adapter 只能负责模型交互，不拥有数据真值和交易权限。
- 每次 run 应有明确 `agent_type`、model、prompt version、tool config、session id。
- tool config 应 per-run/per-turn 注入，尽量不要继承用户本地默认工具全集。

### 2.3 Tool Interface 层

职责：

- 给 agent 提供稳定、可校验、可审计的操作接口。
- 把“模型想做什么”变成“系统允许做什么”。

推荐分层：

```text
Skill / AGENTS.md / workflow SOP
  -> JSON CLI
  -> MCP tools
  -> Python SDK / internal service
  -> Qlib / DB / Parquet / broker adapter
```

各层定位：

- Skill：告诉 agent 正确流程、禁止事项、平台约束和验收标准。
- JSON CLI：最小稳定接口，适合 Codex/Claude Code 直接用 Bash 调用。
- MCP：适合强 schema、高频工具、多客户端接入和 tool discovery。
- SDK/internal service：给 CLI/MCP 调用，不直接暴露给 agent。

接口原则：

- 所有重要命令支持 `--json`。
- 大结果返回 artifact path，不直接灌进上下文。
- 写入类、交易类、删除类工具必须有权限标识和审批路径。
- 每个工具返回 machine-readable status、metrics、artifact ids、error code。

### 2.4 Data Truth 层

职责：

- 管理市场数据、财务数据、公告、研报、宏观和特征快照。
- 保证 point-in-time、复权、停牌、涨跌停、延迟、缺失值处理一致。

关键对象：

- `DatasetSpec`：数据源、市场、频率、时间范围、字段、复权、权限。
- `DataSnapshot`：一次可复现实验的数据版本。
- `FeatureSnapshot`：因子值/特征值的物化版本。
- `UniverseSpec`：股票池、可交易约束、行业/市值过滤。

必须避免：

- agent 临时从网页抓一份数据就做结论。
- 用未来财报、未来成分股、全样本标准化。
- 数据修正后不记录版本，导致回测无法复现。

### 2.5 Research Object 层

职责：

- 把 agent 生成的东西变成系统可验证对象。

核心对象：

- `FactorSpec`：表达式/代码、字段、lookback、lag、方向、假设、数据依赖、tags。
- `StrategySpec`：选股逻辑、组合构建、调仓频率、成本、约束、risk budget。
- `ExperimentSpec`：研究问题、universe、时间切分、baseline、指标、停止条件。
- `RunResult`：指标、图表、日志、代码版本、数据版本、失败原因。
- `PromotionRequest`：为什么值得进入下一阶段、风险和人工审批记录。

关键原则：

- 因子不能只是字符串，要能 parse、validate、hash、version。
- 策略不能只是 notebook，要能配置化、复现和比较。
- 实验不能只保存最终好结果，失败 run 也要保存。

### 2.6 Execution Harness 层

职责：

- 执行 agent 写的代码和系统的评估任务。
- 提供隔离、重试、超时、资源限制和日志。

应该支持：

- sandbox/container 执行。
- 固定入口：`factor eval`、`strategy backtest`、`experiment report`。
- run id 和 artifact id。
- stdout/stderr capture。
- timeout、retry、resource quota。
- deterministic seed。
- job resume 和 partial artifact。

不应支持：

- agent 直接在主进程里执行任意研究代码。
- agent 直接写生产数据库或 broker。
- 指标只由 LLM 自述，没有机器验证。

### 2.7 Evaluation 与 Risk 层

职责：

- 提供研究真值和 promotion gate。

因子评估：

- IC、RankIC、ICIR、年度稳定性、正 IC 占比。
- 分组收益、多空收益、换手、coverage、missing。
- 行业/市值/风格暴露。
- 重复度和相关性检查。
- OOS、rolling、subsample、permutation。

策略评估：

- 年化收益、波动、Sharpe、Calmar、最大回撤。
- turnover、交易成本、滑点、容量、成交可行性。
- benchmark-relative return、beta、tracking error。
- trade count、单票集中度、行业集中度、风险预算。

搜索强度与泄漏（2026-09 补充）：

- 泄漏隔离必须是结构性的：agent 只能通过注册过的类型化工具构造因子/策略，前视在工具层不可表达。统计校正替代不了这一层，honest evaluation 论文中故意植入的泄漏 oracle（Sharpe 35）能完整通过 DSR 与 PBO。
- 所有评估必须经唯一入口并写入 trial ledger；DSR/PBO 的试验数和 Sharpe 方差取自 ledger，agent 搜得越多门槛越高。
- 证据分级：development、adaptive discovery、locked holdout、blind、fresh forward 分开计数；holdout 打开前锁定 shortlist 并冻结 failure memory，读取次数计入预算（`QuantMind-qm2`、`kph`）。
- 参数治理：default-first，默认参数过门即冻结，失败只允许有限邻域救援，全量搜索只做诊断（`QuantMind-qm2`）。

Risk gate：

- 默认拒绝不完整证据。
- 读 run history、code diff、数据版本和指标，而不是只读最终收益。
- 将 promotion 分成 research pass、paper candidate、live candidate，不混为一谈。

### 2.8 Human Workbench 层

职责：

- 让人类以低成本审查 agent 的研究过程。

应该展示：

- 当前目标、假设、universe、数据版本。
- 候选因子/策略列表。
- 每次 run 的 metrics、chart、artifact、代码 diff。
- agent reasoning 摘要和 tool call trail。
- Risk Manager verdict。
- promotion request 和审批记录。
- 失败知识库与下次建议。

关键点：

- UI 不是为了漂亮，而是为了让人类能做判断。
- 应优先支持对比、筛选、追溯、审批和复盘。

### 2.9 Execution Boundary 层

职责：

- 把 research、paper、live 隔离。

建议状态机：

```text
research candidate
  -> evaluated
  -> research approved
  -> paper candidate
  -> paper monitored
  -> live proposal
  -> human risk approval
  -> limited live
  -> monitored live
  -> scale / rollback / retire
```

原则：

- Factor Lab 默认 no-execute。
- paper 不等于 live。
- live 必须有额度、资产范围、最大损失、kill switch。
- agent 可以提出 proposal，但不能绕过审批直接交易。

## 3. 标准 Pipeline

一个合格的低频 agent + quant OS 可以采用如下 pipeline：

```text
1. Research Intake
2. Universe and Data Contract
3. Hypothesis and Candidate Generation
4. Static Validation
5. Sandbox Execution
6. Factor Evaluation
7. Strategy Construction
8. Portfolio Backtest
9. Anti-overfit Review
10. Report and Factor/Strategy Card
11. Human Research Review
12. Paper / Shadow Deployment
13. Paper Monitoring
14. Live Proposal
15. Human Risk Approval
16. Limited Live
17. Ongoing Monitoring and Retirement
```

### Pipeline 责任表

| 节点 | Agent 负责 | Deterministic harness 负责 | Human 负责 |
|---|---|---|---|
| Research Intake | 澄清目标，拆解问题，给出研究计划 | 记录任务和约束 | 确认研究方向、市场、时间尺度 |
| Universe and Data Contract | 建议数据源和字段 | 校验数据可用性、PIT、版本 | 确认数据授权、股票池、不可用数据处理 |
| Hypothesis Generation | 生成因子/策略候选和理由 | 去重、schema 初始化 | 判断假设是否有经济含义 |
| Static Validation | 修复表达式/代码 | parser、lint、leakage check、字段检查 | 对异常设计做取舍 |
| Sandbox Execution | 写代码、调工具、解释错误 | 隔离执行、超时、日志、artifact | 允许大规模/高成本任务 |
| Factor Evaluation | 解释 IC、RankIC、分组收益 | 计算指标、暴露、覆盖、相关性 | 判断是否继续迭代 |
| Strategy Construction | 生成组合构建和参数 | 约束检查、成本模型 | 确认 risk budget 和交易假设 |
| Portfolio Backtest | 解释回测结果 | 组合回测、成本、benchmark 对比 | 判断结果是否足够进入 review |
| Anti-overfit Review | 提出风险解释和修正 | OOS、rolling、permutation、重复度 | 决定是否 research approve |
| Report/Card | 生成报告初稿 | 写入 DB、artifact、固定模板 | 审阅结论和 caveat |
| Paper/Shadow | 生成 deployment proposal | paper account、shadow signal、监控 | 审批 paper 范围 |
| Live Proposal | 总结 paper 证据 | 风险限额、回滚计划、合规检查 | 最终 live approval |
| Limited Live | 监控异常并汇报 | broker adapter、kill switch、审计 | 异常处置、扩容/回滚 |

## 4. 必须 Human-in-the-loop 的节点

不是所有节点都需要人类确认，否则系统会变成低效表单。但以下节点必须有人类参与：

### 4.1 研究目标确认

原因：

- LLM 可以拆任务，但不能替用户决定收益目标、风险偏好、资金规模、市场范围。
- 不同目标会改变全部评估口径。

需要确认：

- 市场：A 股、港股、美股、ETF、期货、crypto。
- 频率：日频、周频、月频。
- 目标：alpha research、组合增强、风险对冲、择时、信号解释。
- 约束：成本、容量、换手、行业暴露、最大回撤。

### 4.2 数据合同确认

原因：

- 数据质量和授权是量化真值的基础。
- agent 容易为了推进任务临时找替代数据。

需要确认：

- 数据源是否可信和可用。
- 是否 point-in-time。
- 财报/公告延迟如何处理。
- 复权、停牌、涨跌停、退市、成分股历史如何处理。

### 4.3 候选进入大规模评估

原因：

- 自动生成候选可以很多，但计算资源和多重测试风险不是免费的。
- 需要控制搜索空间，避免无边界挖掘。

需要确认：

- campaign quota。
- 最大候选数。
- 是否允许扩展字段或引入新数据。
- 是否允许更复杂模型。

### 4.4 Research Promotion

原因：

- 指标过关不等于经济意义成立。
- agent 可能 cherry-pick 最好的一次 run。

需要确认：

- 经济解释是否合理。
- OOS 是否足够。
- 是否有明显 beta、行业、市值或流动性暴露。
- 是否和已有因子重复。
- 失败案例是否被记录，而不是被删除。

### 4.5 Paper / Shadow Deployment

原因：

- paper 虽不动真实资金，但会污染监控、信号和后续决策。

需要确认：

- paper 资金规模和 universe。
- 运行周期。
- 停止条件。
- 监控指标。

### 4.6 Live Approval

原因：

- 真实交易涉及资金、权限、合规、交易成本和事故处置。

需要确认：

- 最大仓位、最大亏损、最大换手。
- broker 权限和 API key。
- kill switch。
- 回滚计划。
- 人类责任人。

### 4.7 异常处置和 Retire

原因：

- 市场结构变化、数据源变化、执行异常都不是模型能自行承担责任的事项。

需要确认：

- 是否暂停策略。
- 是否降低额度。
- 是否废弃因子。
- 是否触发 incident review。

## 5. 当前 Research 结论：哪些会保留，哪些会被替代

这里不从广义交易系统出发，而从当前 repo research 得到的事实出发：code agent 的能力会继续增强，很多“平台包裹层”会被替代；真正可持续的是量化研究对象、数据真值、评估真值、artifact 证据链和 human research ownership。

### 5.1 会被 Code Agent 逐步替代的层

这些层现在有用，但不应该成为系统长期护城河：

| 当前形态 | 为什么会被替代 | 代表 repo 里的信号 |
|---|---|---|
| 自研厚 agent runtime | Codex/Claude Code 会持续强化 repo 理解、代码修改、工具调用、长任务恢复；自研 runtime 很难长期追上 | `langalpha` 的 agent backend 很完整，但基础设施重；对个人研究 OS 不宜先复制 |
| prompt-heavy workflow | 越复杂的 prompt 越难维护，后续会被 skills、tool schema、validators 和 harness gate 替代 | `joinquant-skill`、`worldquant-skill` 都说明平台规则要沉淀成 reference/template/lint |
| notebook 作为研究载体 | code agent 能写 notebook，但 notebook 不能天然提供版本化对象、可比较 run 和 promotion gate | 多数研究型 repo 最后都需要 factor library、experiment workspace 或 registry |
| UI 驱动的手工操作流 | code agent 会越来越能直接操作 CLI/MCP，UI 的价值会从“操作入口”变成“审查和对比界面” | `QuantDesk`/`QuantMind-yj_exp` 的 UI 价值在 review，而不是替 agent 点按钮 |
| 单一 agent CLI 适配 | 具体 CLI 参数、session 行为、MCP 支持会变；绑定某个 CLI 私有行为会脆弱 | `QuantDesk` 中 Claude 和 Codex 的 MCP 注入方式已经不同 |
| LLM 直接调研后给结论 | 随着工具能力增强，裸结论会更不可接受；结论必须由 artifact/metrics/data version 支撑 | `QuantGPT`、`QuantMind-yj_exp` 都把结果放回 parser/evaluator/gate |

### 5.2 会长期保留的层

这些层不会因为 code agent 变强而消失，反而会更重要：

| 长期保留层 | 原因 | 对应设计 |
|---|---|---|
| Data truth | agent 越强，越需要明确什么数据可以被用、何时可得、如何复权、如何版本化 | `DatasetSpec`、PIT、data snapshot、feature snapshot |
| Research object | agent 生成越多候选，越需要把因子/策略变成可验证对象 | `FactorSpec`、`StrategySpec`、expression parser、hash、lineage |
| Evaluation harness | 模型不能自己证明 alpha，有效性必须由固定 evaluator 给出 | IC/RankIC、group return、OOS、rolling、portfolio backtest |
| Artifact registry | 长任务和自动搜索会产生大量中间结果，没有 artifact 就无法复盘 | run id、data version、code version、metrics、chart、stderr |
| Evidence gate | agent 会更会“解释”，所以更要防止没有证据的解释 | 结论必须引用指标、artifact、代码版本、数据版本 |
| Trial ledger | agent 搜索越快，“最好结果是运气”的概率越高 | 唯一评估入口、全量试验记录、按 ledger 折扣的 DSR/PBO、holdout 读取预算 |
| Failure memory | 自动探索规模越大，失败样本越有价值 | duplicate factor、leakage case、bad regime、overfit pattern |
| Human research ownership | agent 可以执行研究，但不能替人决定目标函数和研究价值 | research direction、data contract、promotion decision |

当前最有迁移价值的不是某个完整平台，而是这些稳定抽象：

- `RD-Agent`：scenario、experiment、runner、workspace 分层。
- `QuantaAlpha`：trajectory、mutation/crossover、regulator。
- `QuantGPT`：expression parser、anti-overfit、failure knowledge。
- `QuantDesk`：agent turn、run history、code diff、Risk Manager review。
- `QuantMind-yj_exp`：FactorSpec/ABI、artifact registry、lineage、promotion gate、sandbox。
- `langalpha`：sandbox 内执行数据处理、workspace memory、大结果不进上下文。
- `QuantMind-qm2`（2026-09）：Decision/Control/Execution 分离、locked holdout / rolling blind / fresh 证据分级、default-first 优化治理、搜索暴露账本。
- `kph`（2026-09）：harness 只透传 JSON CLI、不重算，写操作 fail-closed 审批，holdout burn budget。
- `FactorMiner`（2026-09）：经验记忆（成功模板 + 禁区）与四级 admission 级联。
- `Auto-Quant`（2026-09）：不可变 evaluator + 可编辑工件 + keep/discard ratchet 的极简闭环，以及 oracle-gaming 的实证教训。

### 5.3 会成为过渡层的东西

这些不是没用，而是应该按“可替换接口”设计：

- MCP：大概率会保留为 typed tool 重要形态，但具体 SDK、transport、client 支持会变。不要把领域模型藏进 MCP server 内部，MCP 只是入口。
- JSON CLI：作为最低摩擦、最稳的 agent 接口会长期有价值，尤其适合本地研究 OS。
- Skills：skill 文件格式可能变，但领域 SOP、模板、lint、guardrails 会保留。
- LLM server：适合多用户、Web、长任务队列、计费和产品化；不应成为低频研究 OS 的第一性核心。
- Workbench UI：会从“操作台”转成“审查台”，重点展示 evidence、diff、run 对比、promotion packet。

## 6. 哪种路线更可持续

### 6.1 不可持续路线

1. 以自研 agent runtime 为中心。
   - 会被通用 code agent 的能力升级持续挤压。
   - 系统复杂度花在模型编排，而不是量化研究真值。

2. 以聊天体验为中心。
   - chat 可以是入口，但不是研究系统。
   - 没有结构化对象、run 和 artifact，长期不可复盘。

3. 以 notebook 为中心。
   - notebook 适合探索，不适合成为 factor/strategy 的生命周期对象。
   - code agent 越强，越容易生成大量不可比较 notebook。

4. 以 MCP 数量为中心。
   - MCP 只是工具入口，不等于研究纪律。
   - 没有 FactorSpec、evaluator、registry 和 gate，工具再多也只是自动化脚本集合。

5. 以交易闭环为中心。
   - 对当前低频研究 OS 过早。
   - 会把注意力从 factor truth、evaluation truth 和 research memory 拉到 broker/risk/live 复杂性。

### 6.2 可持续路线

更可持续的路线是把系统核心压到 harness 和研究对象上：

```text
Code agent as replaceable researcher/operator
  -> Skill/SOP as research contract
  -> JSON CLI as stable minimum interface
  -> MCP as optional typed tool surface
  -> FactorSpec / StrategySpec / ExperimentSpec
  -> Deterministic evaluator and artifact registry
  -> Evidence gates and failure memory
  -> Human research promotion
  -> Optional workbench / LLM server
```

判断标准：

- 如果一个组件只是“帮 agent 做它未来会更擅长的事”，它应该薄、可替换。
- 如果一个组件定义“研究对象是什么、证据怎么算、结果怎么复现”，它应该稳定、强约束。
- 如果一个组件承担“是否值得继续研究”的判断，它应该有 human gate。

### 6.3 随 Code Agent 进化的分工变化

| 能力 | 现在需要系统提供 | 未来更可能由 code agent 承担 | 系统仍需保留 |
|---|---|---|---|
| 读代码/改代码 | 模板、脚手架、示例 | 多文件实现、修 bug、迁移接口 | 合约测试、lint、sandbox |
| 工具调用 | 固定命令说明 | 自动选择 CLI/MCP、组合工具 | typed schema、权限、日志 |
| 实验推进 | 手写 workflow | 自动拆任务、重试、修复失败 | run registry、quota、停止条件 |
| 结果解释 | 报告模板 | 生成可读分析和下一步建议 | 指标计算、证据引用、反过拟合 gate |
| 知识沉淀 | 手工文档 | 自动总结失败/成功模式 | memory schema、去重、可追溯来源 |
| UI 操作 | 人点按钮 | agent 直接调工具 | 人类审查视图和审批记录 |

所以，系统不要和 agent 争夺“执行智能”，要提供 agent 无法自证的东西：数据契约、对象契约、评估契约、证据契约和人类决策契约。

## 7. 合格形态

一个合格的 agent + quant OS 应该先是研究 OS，而不是交易 OS。它应该长这样：

```text
Human researcher
  -> research question / data contract
  -> code agent operator
  -> skill-guided research workflow
  -> CLI JSON / optional MCP tools
  -> sandboxed jobs
  -> FactorSpec / StrategySpec / ExperimentSpec registry
  -> deterministic evaluator
  -> artifact evidence pack
  -> factor/strategy card + failure memory
  -> human research promotion decision
  -> optional downstream paper/shadow bridge
```

### 最小合格版本

最小版本不需要 Web UI，也不需要自研 LLM server，但必须有：

- `FactorSpec` / `StrategySpec` schema。
- `factor eval --json`。
- `strategy backtest --json`。
- artifact registry。
- trial ledger：所有评估经唯一入口入账，promotion 时按试验数折扣。
- factor card / strategy card。
- failure memory。
- research promotion gate。
- human approval 记录。
- 明确 research-only 边界：默认不进入 paper/live。

### 成熟版本

成熟版本再增加：

- workbench review UI。
- agent turn stream。
- MCP server。
- 多 agent role：Researcher、Reviewer、Data Steward。
- campaign manager。
- report index 和 evidence packet 对比。
- failure memory / hypothesis memory / lineage graph。
- optional LLM server，用于多用户、长任务和产品化。
- optional paper/shadow bridge，但保持为下游边界，不是研究 OS 核心。

### 不合格信号

出现以下情况，说明系统还不是合格 OS：

- 只有 chat，没有实验对象。
- 只有 notebook，没有可复现 run。
- 只有收益图，没有 OOS 和成本。
- 只有 agent 结论，没有 artifact evidence。
- 只有自动挖掘，没有 quota 和停止条件。
- 只报告最好的一次结果，不记录搜索了多少次。
- holdout 可以被反复查看，没有读取预算或污染标记。
- 把 paper/live 当成研究 OS 的默认目标。
- 人类只能看到最终答案，看不到中间过程。

## 8. 对 AlphaSieve 的落地建议

短期优先级：

1. 先把 CLI JSON 和 artifact contract 打牢。
2. 把 factor/strategy workflow 写成 skills 和报告模板。
3. 做 FactorSpec、StrategySpec、ExperimentSpec 的 schema 和 registry。
4. 把 IC、RankIC、group return、turnover、exposure、OOS 变成固定 gate。
5. 从第一天就记录 trial ledger，并预留 locked holdout；gate 的 DSR/多重检验以 ledger 为准。
6. 增加 human promotion request，而不是自动把好结果升级成策略。

中期优先级：

1. 增加 MCP server，暴露 read-only data、factor eval、backtest、report 查询。
2. 增加 campaign manager，支持 quota、retry、failure memory。
3. 增加独立 Risk Manager review，读取 run history、code diff、artifact。
4. 增加 workbench 或至少 report index，便于人工审查。

长期优先级：

1. 如果需要多人使用或长任务服务化，再引入 `langalpha` 式 LLM server。
2. 如果需要产品化审查体验，再做 workbench UI；UI 重点是 evidence review，不是替代 CLI/MCP。
3. paper/shadow 只作为下游桥接层；live execution 不属于当前研究 OS 的核心目标。

最终定位应该是：AlphaSieve 不需要先做一个“大模型金融聊天平台”或“自动交易平台”，而应该先做一个低频量化研究 harness。随着 Codex/Claude Code 变强，真正会留下来的不是 agent wrapper，而是 FactorSpec、Evaluator、Artifact Registry、Evidence Gate、Memory 和 Human Promotion 这些研究真值层。
