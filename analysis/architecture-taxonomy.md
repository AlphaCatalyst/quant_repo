# Architecture Taxonomy

本文件从架构形态分析 `/data/codebase/quant_repo/open_source` 中的项目（2026-09-26 快照，43 个仓库，9 类）。分类依据不是语言或框架，而是它们把“量化”拆成什么问题、把 AI/agent 放在哪个环节、如何处理数据、回测、风控和执行边界。

## 1. Qlib/因子研究型 R&D Agent

代表项目：

- `RD-Agent`
- `QuantaAlpha`
- `QuantGPT`
- `QuantMind-yj_exp`
- `QuantMind-qm2`
- `FactorMiner`
- `AlphaEvo`
- `QuantEvolver`
- `AlphaAgent`
- `EvoQuant`
- `automated-quant-research`
- `Qlib-with-Claudex`

面向的问题：

- 如何从研究方向或论文/报告中生成可执行因子。
- 如何让 LLM 不只是写表达式，而是进入“生成 -> 回测 -> 反馈 -> 进化”的闭环。
- 如何处理因子重复、过拟合、样本外验证、模型组合。

典型架构：

```text
research direction / paper / prompt
  -> planning / hypothesis generation
  -> factor expression or code generation
  -> factor parser / adapter
  -> Qlib or local backtest
  -> IC / RankIC / Sharpe / drawdown / stability
  -> feedback memory / trajectory pool
  -> mutation / crossover / next round
```

关键设计：

- `RD-Agent` 把 Qlib 场景封装为 scenario：背景、数据说明、接口、输出格式、simulator 和 experiment setting 都作为 agent 可读上下文；`QlibFactorRunner` 负责合并新因子、与已有 SOTA 因子去重、写 parquet，再跑 Qlib workflow。
- `QuantaAlpha` 更像演化式 factor mining 系统：先做多方向规划，再走 original/mutation/crossover，维护 trajectory pool，并用 factor regulator 控制复杂度和重复。
- `QuantGPT` 更强调自研表达式 parser、WorldQuant/Cloud 兼容、anti-overfit 检测、知识库与双 LLM cross-review。
- `QuantMind-yj_exp` 把因子研究产品化为 Factor Lab：FactorSpec/ABI、静态校验、leakage check、pandas/hardened evaluator、artifact registry、memory-aware planning、Codex admission、Docker sandbox、promotion gate 和 Qlib backtest adapter 边界都拆成模块。
- `automated-quant-research` 是更轻量的商品期货版本：Claude 生成 DSL factor sets，LightGBM 训练，rolling walk-forward，robustness verdict。
- `Qlib-with-Claudex` 更像把 Qlib + RD-Agent 组合成可跑的实验模板和 Claude 工作流，不是独立研究平台。
- `QuantMind-qm2` 把自主 campaign 放进 Decision/Control/Execution 三层：Codex 只产出结构化决策，locked holdout、rolling blind、fresh cohort 和 default-first 优化治理由代码强制执行。
- `FactorMiner` 用 Ralph Loop（retrieve → generate → 四级 evaluate → admit → distill）和经验记忆（成功模板 + 禁区）应对因子库变大后的相关性红海。
- `AlphaEvo` 让 LLM 只做种子和结构变异，GP/EoH 在四个时间窗口上按 IC 与 IC gap 决定存亡。
- `QuantEvolver` 把可执行评估变成 RFT/GRPO 奖励，用参数更新替代无限增长的 prompt 反馈。
- `AlphaAgent` 当前是 A 股 Tushare panel + DSL + memmap FactorZoo + 交付门，LLM 挖掘只是可选层。
- `EvoQuant` 把研报复现放在前端：研报入库 → 文献检索 → anchor-first 构思 → Research Artifact entry point → IC runtime。

量化思路：

- 横截面因子预测：IC、RankIC、分组收益、多空组合。
- 因子表达式搜索：算子组合、窗口变异、非线性交互。
- 演化搜索：mutation、crossover、parent selection、trajectory scoring。
- 样本外纪律：train/valid/test、rolling window、permutation test、deflated Sharpe、subsample stability。

适合借鉴：

- 因子生成不要只靠 prompt，一定要有 parser、schema、回测 runner、去重、错误反馈。
- 每轮实验要保存 trajectory，而不只是最后的好因子。
- 因子质量不能只看单次 Sharpe，要加 IC 稳定性、重复度、复杂度、样本外退化。

## 2. Agentic Research Workspace / 投研工作台

代表项目：

- `langalpha`
- `Vibe-Trading`
- `OpenAlice`
- `QuantDesk`
- `QuantMind-yj_exp`
- `AgentQuant`
- `AI-Trader`
- `deepseek-harness-quant`
- `kph`
- `TradingAgents`

面向的问题：

- 如何把“一次问答”变成持续积累的投研 workspace。
- 如何让 agent 调工具、写代码、跑回测、保存报告、复用 memory。
- 如何让人类能审查 agent 的中间过程，而不是只看最终回答。

典型架构：

```text
user goal
  -> workspace / thread / session
  -> skill or tool discovery
  -> data grounding
  -> code/tool execution
  -> backtest / report / chart
  -> memory / artifacts / inbox
  -> next session resumes context
```

关键设计：

- `langalpha` 的核心是 persistent workspace + Programmatic Tool Calling。agent 写 Python 到 sandbox 里处理金融数据，避免把大表塞进上下文；同时用 PostgreSQL、Redis、LangGraph checkpoint、SSE buffer 支撑长任务和断线恢复。
- `Vibe-Trading` 是面向多市场的 agent research workspace，内置大量 finance skills、数据 fallback、alpha zoo benchmark、shadow account、MCP tools。
- `OpenAlice` 把“deciding”和“doing”拆开：Alice process 做研究/工作区/工具，UTA service 持有 broker 连接和交易状态，类似硬件钱包式隔离。
- `QuantDesk` 是策略实验工作台：Analyst agent 写策略和跑回测，Risk Manager agent 独立审查过拟合、代码 diff、run history，再决定是否允许 paper trading。
- `QuantMind-yj_exp` 的 Factor Lab workbench 强调人类可审查的研究过程：batch draft、admission、execution、evaluation、memory、portfolio backtest、promotion request 都有独立 UI 面板和本地 artifact。
- `AgentQuant` 是小而清晰的 ReAct 研究循环：analyze -> hypothesize -> backtest -> reflect -> store，SQLite memory 记录策略经验。
- `AI-Trader` 更偏 agent-native 社区/信号平台：agent 发布信号、参与讨论、copy trading、paper trading 和排行榜。
- `deepseek-harness-quant` 把 A 股低频研究台切成驱动层（LLM）/ 写死引擎 / 事实层，Pitch 人工审批是唯一买入来源，五池远期验证按决策来源追踪 T+1/5/20/60。
- `kph` 是确定性研究后端 + 外挂 harness：harness 工具只透传 `kp --json` CLI，只读直通、写操作 fail-closed 审批，计算和 gate 判定永不在 agent 侧发生。
- `TradingAgents` 是多角色辩论决策图，2026 年的版本重点补了 PIT、SEC as-filed、ticker×date 网格评估和 append-only decision log。
- 2026-09 起，`AgentQuant` 的记忆升级为按 `as_of` 召回的 MemoryService，`langalpha` 增加 automations 与 computers，`OpenAlice` 增加 AutoQuant 工作区模板。

量化思路：

- 不只做因子，也做策略、报告、交易行为诊断、投资研究。
- 强调 traceability：每个实验、每个工具调用、每次交易建议都要能追溯。
- 越靠近交易，越强调人类确认、risk gate、paper/live 隔离。

适合借鉴：

- 长周期研究需要 workspace 文件系统、memory 和 artifact，而不是 chat history。
- 复杂金融分析要让 agent 写代码处理数据，最终只把摘要和图表回传。
- agent 参与交易时，决策层和执行层最好物理/进程/权限隔离。

## 3. Backtest-to-Live Trading Framework

代表项目：

- `lumibot`
- `mmr`
- `QuantMind`
- `QuantMind-yj_exp`
- `QuantDinger`
- `cbt-framework`
- `revolut-x-api`
- `ai-hedge-fund`

面向的问题：

- 如何让策略从研究、回测、paper 到 live 尽量共享一套代码路径。
- 如何接 broker、账户、订单、行情、日志、风控。
- 如何让 AI agent 可以操作交易系统，但不绕过安全边界。

典型架构：

```text
strategy code / agent proposal
  -> data source / market feed
  -> backtest broker or live broker
  -> order model / position model
  -> risk checks / approval
  -> execution adapter
  -> audit trail / portfolio state
```

关键设计：

- `lumibot` 是最典型的同代码路径框架：`Strategy` 类可以跑 backtest，也可以换 broker 跑 paper/live；内置多 broker 和 backtesting data source，并加入 AI trading team。
- `mmr` 是 live trading 系统形态：ZMQ RPC/PubSub/MessageBus，trader/data/strategy 三个服务，DuckDB 存事件和 proposal，Claude 通过 JSON CLI 操作。
- `QuantMind` 是微服务：API Gateway、Engine Service、Trade Service、Stream Service，Qlib/Pandas 双引擎，PostgreSQL/Redis/Celery。
- `QuantMind-yj_exp` 在 QuantMind 之上补了研究到生产的缓冲层：candidate/run/factor values 先进入 shadow feature 和 shadow signal，训练要走 promoted vs baseline 对比、approval audit 和 rollback，Factor Lab 本身默认不连接 live trading。
- `QuantDinger` 是 Flask + Vue + Postgres + Redis 的自托管平台，强调 strategy/backtest 和 live capital 的代码路径隔离。
- `cbt-framework` 是 Claude Code 命令式框架，用 slash command 组织发现、研究、EDA、构建、回测、优化、上线。
- `revolut-x-api` 是加密交易所 API/CLI/MCP/skill 组合，偏交易连接器和 grid strategy backtest。
- `ai-hedge-fund` v2 把 Fund 做成一等对象：YAML mandate + pods/AlphaModel + Broker protocol，回测和实盘只换 clock 与 broker，走同一个 `run_cycle`。
- 2026-09 起，`mmr` 在 live 栈旁长出 `trader/simulation/` 截面研究核（PIT universe、walk-forward、PBO/DSR）；`lumibot` 的 AI agents 有了 skills、eval cases 和 fill 回传；`QuantMind` 主线升级为内嵌 RD-Agent/TradingAgents 的 QuantMind 2.0 平台。

量化思路：

- 传统技术指标策略：均线、RSI、MACD、Bollinger、突破、均值回归。
- 多资产/多 broker 执行：股票、期权、期货、crypto、forex。
- 风控优先：position sizing、drawdown kill switch、order guard、paper mode、proposal approval。

适合借鉴：

- 同一策略逻辑尽量能在 backtest 和 live 中复用，但 broker/order/risk 应抽象清楚。
- AI agent 最好只能创建 proposal 或 run backtest，执行真实订单要有确认或 guard。
- 审计日志比“漂亮 UI”更重要：事件、订单、fills、rejections、reasoning 都要记录。

## 4. MCP/Data Tool Layer

代表项目：

- `data-mcp`
- `qlib-mcp`
- `quantcontext-mcp-server`
- `quantconnect-mcp-server`
- `revolut-x-api`
- `langalpha/mcp_servers`
- `Vibe-Trading` MCP

面向的问题：

- 如何让任意 AI agent 安全、结构化地调用金融数据和量化计算。
- 如何把平台 API 包装成 agent 可发现、可校验、可审计的 tool。
- 如何避免 agent 直接抓网页、乱拼 API、把巨大数据塞进上下文。

典型架构：

```text
MCP client / agent
  -> tool registry
  -> zod / pydantic schema validation
  -> API client or local engine
  -> compact JSON result / artifact path
```

关键设计：

- `data-mcp` 是纯数据层：wiki、paper、crypto、equity、macro、SEC、13F、ETF、Polymarket。
- `qlib-mcp` 是 Qlib 快捷工具层：init、list instruments、get data、factor analysis、TopK backtest。
- `quantcontext-mcp-server` 是确定性研究计算层：screen -> backtest -> factor analysis，并在 instructions 中要求调用前说明参数。
- `quantconnect-mcp-server` 是平台 API 层：项目、文件、编译、回测、优化、live、object store、AI tools，共 64 个工具。
- `langalpha` 和 `Vibe-Trading` 把 MCP 当作 workspace/sandbox 中可按需加载的数据处理能力。

量化思路：

- MCP 本身不一定创造 alpha，它解决的是“agent 如何可靠获取数据和执行计算”。
- 好的 MCP tool 应该带 schema、默认值、只读/破坏性提示、输出截断策略和 artifact 交付。

适合借鉴：

- 将数据查询、因子分析、回测和平台操作统一成 typed tools。
- 对大结果使用 artifact 或压缩摘要，不把完整 DataFrame 灌进上下文。
- 对交易和 live 操作标注 destructive/open-world，并设置额外确认。

## 5. Platform Skill / Agent SOP

代表项目：

- `joinquant-skill`
- `finlab-ai`
- `worldquant-skill`
- `kis-ai-extensions`
- `Qlib-with-Claudex`

面向的问题：

- 如何让通用 LLM 正确使用具体量化平台。
- 如何把平台文档、模板、lint、常见坑、执行顺序封装成 skill。
- 如何减少 hallucinated API 和未来函数。

典型架构：

```text
platform docs
  -> reference chunks / progressive disclosure
  -> templates
  -> lint or validation tools
  -> workflow SOP
  -> optional MCP wrapper
```

关键设计：

- `joinquant-skill` 专注聚宽 API 准确性：14 类 reference、5 个模板、strategy lint、研报到策略流水线、MCP server。
- `finlab-ai` 专注 FinLab 包使用：市场切换、DataFrame 方法、sim API、US market lookahead 注意事项、完整策略示例。
- `worldquant-skill` 专注 WorldQuant BRAIN SOP：Rule of 8、表达式验证、batch backtest、研究日志模板。
- `kis-ai-extensions` 专注韩国 KIS API：策略 YAML、Lean backtester、订单执行、secret/prod guard hooks。

量化思路：

- 这类项目不一定有自己的 alpha engine；价值在于把平台规则编码为 agent workflow。
- 平台约束本身就是“量化正确性”的一部分，例如复权、交易时段、slippage、佣金、数据对齐。

适合借鉴：

- 对平台型系统，skill 比长 prompt 更可靠。
- 最核心的不是“多写例子”，而是 route、template、lint、error recovery。
- 所有 live/prod 操作都应该有 hook 和 confirmation gate。

## 6. Domain-Specific Quant Research / A-share Localization

代表项目：

- `quant-ashare`
- `llm-quant`
- `joinquant-skill`
- `Vibe-Trading`
- `QuantMind`
- `QuantMind-yj_exp`
- `AlphaAgent`
- `deepseek-harness-quant`

面向的问题：

- 如何把本地市场制度、数据源、交易规则放进系统。
- A 股里的涨跌停、停牌、T+1、北向资金、龙虎榜、Level2、复权、标签滞后等问题如何建模。
- 如何避免公开因子和牛市 beta 被误判成 alpha。

典型架构：

```text
market-specific data adapters
  -> cleaning / tradability / event masks
  -> factors / labels / neutralization
  -> regime / risk / impact model
  -> walk-forward validation
  -> daily pipeline / alert / execution
```

关键设计：

- `quant-ashare` 明确把 A 股特有问题列为模块：标签工程、涨跌停/停牌屏蔽、Level2 微结构、冲击成本、Barra 中性化、事件屏蔽、主题投资、执行层。
- `llm-quant` 虽然偏 ETF/宏观/结构套利，但研究治理很强：四 track、DSR、CPCV、perturbation、append-only registry、paper trading gate。
- `Vibe-Trading` 的数据 fallback 链对 A/HK/US/crypto 做了市场级路由，适合参考多市场数据策略。
- `QuantMind-yj_exp` 对本地行情表、feature snapshot Parquet 和 Qlib provider 做 evaluator 输入兜底，同时统一 symbol 口径、factor values 入库和训练快照物化，适合作为 data truth 到 feature catalog 的桥接参考。
- `AlphaAgent` 用 Tushare 两段式数据管线（在线拉缓存、离线建 panel）+ ZZ1000 并集宇宙 + MLS-FMB 交付门，是 A 股截面因子对象化的现成样本。
- `deepseek-harness-quant` 把 T+1 开盘成交、一字板过滤、Beneish 排雷写进写死引擎，数据只经 `data/cache.py` 单一 PIT 入口。
- 2026-09 起 `quant-ashare` 删除了执行、组合优化、公司行为等模块，上文描述的部分能力已不在当前代码中。

量化思路：

- 市场制度是因子有效性的前提，不是后处理。
- 对 A 股，停牌/涨跌停/一字板/复权/label 可观测性会直接影响 IC。
- “漂亮 OOS”也可能来自牛市 beta 或 label leak，需要明确作废记录和修正记录。

适合借鉴：

- 本土市场要先做 data truth 和 tradability truth，再谈 LLM 因子挖掘。
- 数据源 fallback 要有优先级、限流风险和 provenance。
- 失败和作废实验应作为一等研究资产保存。

## 7. Minimal Research Harness（autoresearch 范式）

代表项目：

- `Auto-Quant`
- `autoresearch-trading`

面向的问题：

- 不搭平台，如何让 code agent 在无人值守下迭代策略或模型，同时不被评估器“骗”。

典型架构：

```text
program.md（人类方向与约束）
  -> agent 修改唯一可编辑工件（strategy.py / strategies/）
  -> 不可变 evaluator（run.py / trading.py）
  -> 单标量或多策略摘要
  -> git keep / discard ratchet
  -> results.tsv / retrospective（跨版本失败记忆）
```

关键设计：

- `Auto-Quant` 实证了 oracle-gaming：单文件变异时表面 Sharpe 1.44、真实约 0.19；改为 3 个策略槽对照 + `val.py` OOS 后 gaming 变得可见。
- `autoresearch-trading` 把结构和参数拆开：LLM 只改离散结构，BiteOpt 在每个 walk-forward fold 内优化连续参数。

适合借鉴：

- 这是蓝图“最小合格版本”的极简形态，适合作为外环快速试错。
- 缺少 artifact registry、trial ledger 和 promotion gate，不能直接当研究 OS 的内环。

## 8. Evaluation Integrity / Statistical Gate

代表项目：

- `QuantMind-qm2`
- `kph`
- `mmr`（`trader/simulation/`）
- `llm-quant`
- `deflated-sharpe`
- `AlphaBench`

面向的问题：

- agent 搜索越多，最好结果越容易是运气；如何让报告出来的指标对搜索强度和信息泄漏诚实。

典型架构：

```text
typed / registry-validated tools（前视在构造上不可表达）
  -> 唯一评估入口 + trial ledger
  -> 证据分级：development / holdout / blind / fresh
  -> DSR / PBO / BH-FDR / bootstrap（N、V 取自 ledger）
  -> gate 输出只代表可进入人工 review
```

关键设计：

- 外部参考 [What survives honest evaluation?](https://arxiv.org/abs/2608.27734) 证明两层缺一不可：泄漏 oracle（Sharpe 35）能通过 DSR/PBO，所以必须有工具层泄漏隔离；同时必须用完整 trial ledger 折扣搜索强度。
- `QuantMind-qm2` 的 locked holdout / rolling blind / fresh lock 与搜索暴露账本；`kph` 的 holdout burn budget 与内容寻址 replay。
- `mmr` 用 `tests/invariants` 把研究算术钉成规格；`deflated-sharpe` 提供可嵌入的 DSR/MBL/BH 纯函数；`AlphaBench` 的 Assay 后端提供可切换的 PIT 评估引擎。

适合借鉴：

- gate 设计从“单次指标阈值”升级为“证据分级 + 搜索强度折扣 + 泄漏隔离”。

## 9. Resource Index / Catalog

代表项目：

- `awesome-quant`

面向的问题：

- 快速定位量化领域基础库、数据源、回测框架、风险分析、因子分析、替代数据、研究环境。

架构：

- 不是运行时系统，而是 curated list。
- 价值在分类法和外部资源入口。

适合借鉴：

- 作为技术选型索引，不作为架构模板。
- 可用于补全某个方向缺失的库，例如风险、期权、时间序列、组合优化。
