# Architecture Taxonomy

本文件从架构形态分析 `/data/codebase/quant_repo/open_source` 中的项目。分类依据不是语言或框架，而是它们把“量化”拆成什么问题、把 AI/agent 放在哪个环节、如何处理数据、回测、风控和执行边界。

## 1. Qlib/因子研究型 R&D Agent

代表项目：

- `RD-Agent`
- `QuantaAlpha`
- `QuantGPT`
- `QuantMind-yj_exp`
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

量化思路：

- 市场制度是因子有效性的前提，不是后处理。
- 对 A 股，停牌/涨跌停/一字板/复权/label 可观测性会直接影响 IC。
- “漂亮 OOS”也可能来自牛市 beta 或 label leak，需要明确作废记录和修正记录。

适合借鉴：

- 本土市场要先做 data truth 和 tradability truth，再谈 LLM 因子挖掘。
- 数据源 fallback 要有优先级、限流风险和 provenance。
- 失败和作废实验应作为一等研究资产保存。

## 7. Resource Index / Catalog

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
