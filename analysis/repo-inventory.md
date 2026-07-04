# Repo Inventory

本文件逐一说明 `/data/codebase/quant_repo/open_source` 下 29 个仓库的定位、架构、问题域和量化思路。

## 总览表

| Repo | 类型 | 面向问题 | 核心架构思路 | 量化方法关键词 | 参考价值 |
|---|---|---|---|---|---|
| `RD-Agent` | Qlib R&D agent | 自动化数据驱动 R&D、因子/模型实现 | scenario + coder + runner + experiment workspace | Qlib、IC、SOTA 因子去重、factor/model co-evolution | 很高 |
| `QuantaAlpha` | 演化式因子挖掘 | 从研究方向自动挖因子 | planning + trajectory + mutation/crossover + regulator | 因子表达式、IC、RankIC、演化搜索 | 很高 |
| `QuantGPT` | 因子研究引擎 | WorldQuant/Cloud 风格因子自动迭代 | expression parser + backtest + anti-overfit + knowledge base | WQ 表达式、group backtest、anti-overfit、cross-review | 高 |
| `AgentQuant` | ReAct 研究 agent | 策略参数研究和记忆复用 | analyze -> hypothesize -> backtest -> reflect -> store | regime、grid-constrained LLM、warmup guard | 高 |
| `llm-quant` | 研究治理系统 | 多 track alpha lab 和 paper trading | data + brain + backtest + risk + surveillance + DuckDB | DSR、CPCV、TSMOM、macro、structural arb | 高 |
| `automated-quant-research` | 轻量因子演化实验 | 商品期货 LightGBM 因子进化 | shell pipeline + Claude + DSL + LightGBM + rolling OOS | RankIC、Sharpe、permutation、deflated Sharpe | 中高 |
| `Qlib-with-Claudex` | Qlib/RD-Agent 模板 | 快速跑 Qlib + RD-Agent loop | submodule + scripts + Claude skills | Qlib 数据、IC、R&D loop | 中 |
| `QuantDesk` | 策略实验工作台 | AI 写策略、回测、RM 审查、paper | monorepo + analyst agent + RM gate + Docker engines | Freqtrade、Nautilus、overfit review、run history | 很高 |
| `OpenAlice` | agent trading workspace | 研究/工作区和 broker 执行隔离 | Alice process + UTA service + workspace + MCP | trading-as-git、guard pipeline、workspace automation | 高 |
| `langalpha` | 持久投研 workspace | 长周期投资研究和 PTC | FastAPI + sandbox + MCP wrappers + LangGraph + Redis/Postgres | DCF、fundamentals、macro、options、workspace memory | 很高 |
| `Vibe-Trading` | 多市场研究 agent | 自然语言研究、回测、报告、shadow account | skills + data routing + MCP + alpha zoo + swarm | A/HK/US/crypto、alpha zoo、walk-forward、trade journal | 很高 |
| `lumibot` | backtest/live 框架 | 同一策略代码跑回测和实盘 | Strategy + BacktestingData + Broker + Trader + AI agents | broker abstraction、agent teams、point-in-time tools | 很高 |
| `mmr` | agent-native live trading | Claude 通过 CLI 操作实盘系统 | ZMQ services + DuckDB + JSON CLI + proposal approval | proposal pipeline、risk gate、strategy runtime | 高 |
| `QuantMind` | 微服务量化平台 | Qlib 训练、回测、推理、交易一体化 | API/Engine/Trade/Stream services + PG/Redis | Qlib、Pandas backtest、AI model lifecycle、risk | 高 |
| `QuantMind-yj_exp` | Factor Lab / QuantMind 分支 | 受控因子工厂、候选因子评估、shadow feature 到训练/审批桥接 | factor_lab modules + research APIs + workers + UI workbench + Docker sandbox | FactorSpec、Codex admission、IC/RankIC、promotion gate、Qlib fallback、shadow signal | 很高 |
| `QuantDinger` | 自托管 AI 交易平台 | 策略、回测、AI、执行、计费 | Vue + Flask/Gunicorn + Postgres + Redis + adapters | indicator strategy、grid、pending orders、broker adapters | 中高 |
| `AI-Trader` | agent signal/copy platform | agent 发信号、社区协作、copy trading | FastAPI service + skills + OpenAPI + frontend | signals、paper trading、leaderboard、team missions | 中 |
| `cbt-framework` | Claude command framework | 从想法到回测/上线的命令式流程 | slash commands + templates + agents + optional MCP | pandas/fast engine、walk-forward、live deployment | 中高 |
| `data-mcp` | 金融数据 MCP | 给 agent 提供金融数据 | TS MCP registry + typed API client | SEC、13F、ETF、macro、equity、crypto、Polymarket | 中高 |
| `qlib-mcp` | Qlib MCP | 快速把 Qlib 暴露给 agent | FastMCP + Qlib global init + data/factor/backtest tools | Qlib expression、TopK、IC analysis | 中 |
| `quantcontext-mcp-server` | 确定性研究 MCP | screen/backtest/factor analysis | FastMCP + deterministic engines + response truncation | US screen、monthly rebalance、Fama-French regression | 中 |
| `quantconnect-mcp-server` | 平台 API MCP | 操作 QuantConnect 项目/回测/live | FastMCP + 64 platform tools | LEAN、compile、backtest、optimization、live | 中 |
| `revolut-x-api` | crypto API/CLI/MCP | Revolut X 行情、订单、grid 策略 | typed API + CLI + MCP + skills | crypto candles、order book、grid backtest、monitoring | 中 |
| `joinquant-skill` | 平台 skill | 让 AI 正确生成聚宽代码 | references + templates + lint + factor_lab + MCP | JoinQuant API、future-function lint、IC、grouping | 高 |
| `finlab-ai` | 平台 skill | 让 AI 正确使用 FinLab | skill docs + examples + best practices | FinLabDataFrame、sim、TW/US/HK/JP/KR | 中 |
| `worldquant-skill` | 平台 SOP skill | WorldQuant BRAIN 因子回测流程 | factor_backtest + recorder + knowledge search | WQ expression、Rule of 8、neutralization、research log | 中高 |
| `kis-ai-extensions` | KIS agent plugin | KIS 策略、Lean 回测、订单安全 | multi-agent install bundle + hooks + skills + MCP | presets、Lean backtest、prod order guard | 中高 |
| `quant-ashare` | A 股研究框架 | A 股特有数据/制度/因子/执行 | data adapters + factors + LLM layer + pipelines | Level2、涨跌停、Barra、冲击成本、OOS 修正 | 高 |
| `awesome-quant` | 资源索引 | 找量化库和资源 | curated list + generated site | pricing、backtesting、risk、factor、data | 中 |

## 研究引擎类

### RD-Agent

定位：通用数据驱动 R&D agent，在金融场景里重点支持 Qlib 因子和模型迭代。

架构：

- `rdagent/scenarios/qlib/` 把 Qlib 场景拆成 factor/model experiment、proposal、developer runner、workspace template。
- `QlibFactorScenario` 给 agent 提供背景、数据说明、接口、输出格式、simulator 和 train/valid/test setting。
- `QlibFactorRunner` 处理因子数据，合并 SOTA 因子和新因子，用相关性阈值去重，再写 `combined_factors_df.parquet` 进入 Qlib workflow。

量化思路：

- 把 LLM 生成的因子当成 feature engineering R&D。
- 用 Qlib 的 dataset/model/backtest 作为验证后端。
- 通过“现有 SOTA 因子 + 新因子”的组合增量评价，而不是孤立评价单因子。

可借鉴点：

- scenario 化上下文很强：agent 不需要知道所有系统细节，只要遵守接口。
- 因子去重和 workspace 注入是研究闭环中真正有工程价值的部分。

风险：

- 框架大，迁移成本高。
- 更适合做上层参考，不适合直接嵌入小系统。

### QuantaAlpha

定位：LLM + evolution 的自动因子挖掘系统。

架构：

- `pipeline/factor_mining.py` 控制 original、mutation、crossover 三类 round。
- `pipeline/evolution/controller.py` 管理 trajectory pool、parent selection、phase transition。
- `factors/regulator/` 和 `factors/coder/factor_ast.py` 做表达式解析、复杂度和重复度约束。
- `factors/library.py` 把因子、代码、metadata、backtest result、cache path 统一保存。

量化思路：

- 输入不是固定因子，而是研究方向。
- 先发散多个方向，再通过 trajectory 评估选择父代，做 mutation/crossover。
- 用 IC/RankIC/回测指标和复杂度/重复度约束共同决定下一轮。

可借鉴点：

- evolution controller 和 factor library 设计值得深读。
- factor regulator 比单纯“让 LLM 别复杂”可靠。

风险：

- 研究原型味道较重。
- 部分质量门默认配置可能偏宽，需要结合自己的严谨性要求重设。

### QuantGPT

定位：面向 WorldQuant/QuantGPT Cloud 的 autonomous factor mining engine。

架构：

- `expression_parser.py` 是核心，支持截面/时序/非线性/条件等表达式。
- `iteration.py` 做 factor scoring、prompt building、mutation/crossover/explore。
- `anti_overfit.py` 做 IC stability、subsample stress、placebo、half-life。
- `mcp_server.py`、REST API、Web UI 让 agent 和人类都能访问。

量化思路：

- 因子表达式是核心资产。
- 通过 group backtest、IC/IR、Cloud alignment、anti-overfit 评分给因子分级。
- 用知识库记录 rules/findings/failures，减少重复试错。

可借鉴点：

- 自研 parser + validator 是因子系统核心，不应只保存字符串。
- 失败知识库和 dual-LLM review 对 agent 研究很重要。

风险：

- 强绑定 WQ/Cloud 风格，通用性弱于 Qlib-first 系统。

### AgentQuant

定位：小型自主量化研究 agent。

架构：

- `agent_graph.py`：analyze -> hypothesize -> backtest -> reflect -> store。
- `parameter_grid.py`：LLM 在 canonical grid 中选参数，而不是无限自由生成。
- `lookback_guard.py`：warmup enforcement，防 lookahead。
- SQLite memory 保存历史策略结果，变成下次 prompt context。

量化思路：

- 以 regime context 驱动策略候选。
- 通过 backtest tournament 排序候选。
- 结果进入 memory，形成跨 session 的策略经验。

可借鉴点：

- 结构很清楚，适合作为研究 agent 的最小闭环参考。
- grid-constrained LLM 是控制搜索空间的好办法。

风险：

- 策略空间偏 ETF/单资产参数化策略，不是完整多因子平台。

### llm-quant

定位：严谨研究治理 + paper trading 的 LLM quant lab。

架构：

- `data/` 负责 Yahoo/FRED/COT/指标。
- `brain/` 负责 Claude prompt、market context、JSON 解析。
- `backtest/` 负责策略、walk-forward、robustness、meta-label。
- `risk/` 负责 14 项 pre-trade checks、CVaR、相关性。
- `surveillance/` 负责 post-trade detectors 和 kill switches。
- DuckDB 记录 trades、decisions、portfolio snapshots 和 hash chain。

量化思路：

- 四条 track：defensive alpha、aggressive alpha、structural arb、sprint alpha。
- 所有 track 都有 DSR、CPCV、perturbation 等完整性门槛。
- 把“假设被证伪”作为研究成果。

可借鉴点：

- research governance 很强：spec freeze、append-only registry、robustness gate。
- 风控和研究分层清楚。

风险：

- 领域偏 ETF/宏观/结构套利，和 A 股低频多因子不是完全同构。

### automated-quant-research

定位：商品期货上的 Claude-driven LightGBM factor evolution 实验。

架构：

- shell scripts 串起 prepare data、generate initial、evolve、select best、robustness、OOS backtest。
- Python 模块处理 DSL 表达式、feature matrix、LightGBM、permutation、deflated Sharpe、subsample。

量化思路：

- LLM 生成 DSL factor set，LightGBM 训练预测。
- rolling walk-forward：train 7 年、valid 2 年、OOS 1 年。
- 通过 robustness verdict 决定是否继续搜索。

可借鉴点：

- 轻量 pipeline 很适合做单方向 proof-of-concept。
- `run_until_robust.sh` 体现了自动搜索和停止条件。

风险：

- shell pipeline 可维护性一般。
- 商品期货领域特化。

## 工作台和交易平台类

### QuantDesk

定位：AI 策略实验工作台，目标是快速把交易想法变成可验证策略。

架构：

- pnpm monorepo，Express backend、React UI、PostgreSQL/Drizzle。
- Analyst agent 写策略、取数据、跑回测。
- Risk Manager agent 独立读 run history、code diff、analyst trail，并通过 MCP verdict tool 审批。
- Docker engine wrappers 隔离 Freqtrade、Nautilus、Generic scripts。

量化思路：

- 重点不在 live trading，而在研究迭代和 validation。
- 支持 classic candle strategy、realtime event-driven strategy 和 generic 脚本。
- 独立 RM gate 专门抓 overfit、lookahead、trade count 不足、sudden jump、cherry-picking。

可借鉴点：

- RM prompt + code diff + run history 是非常好的 agent 风控架构。
- 实验工作台要把每次 run 绑定 commit hash。

风险：

- 当前更像策略研发平台，非因子研究核心。

### OpenAlice

定位：one-person Wall Street agent，强调 workspace 和交易凭证隔离。

架构：

- Alice process：workspace、tool center、market data、analysis、news、UI、MCP。
- UTA service：broker connections、Trading-as-Git、guards、FX、snapshots。
- Workspace 是 agent 的家：目录 + git + 原生 CLI session。
- Guardian supervisor 管理 Alice 和 UTA。

量化思路：

- agent 负责 deciding，UTA 负责 doing。
- 订单像 git 一样 stage/commit/push，所有变更有历史。
- guard pipeline 做 pre-execution safety checks。

可借鉴点：

- 交易系统最值得借鉴的是凭证隔离和执行隔离。
- Workspace-based native CLI 比自己实现 LLM loop 更贴合现代 code agent。

风险：

- 架构复杂，主要价值在执行边界，不是因子研究。

### langalpha

定位：持久化投资研究 workspace。

架构：

- FastAPI backend、React frontend、PostgreSQL、Redis、Daytona sandbox。
- PTC：agent 写 Python，在 sandbox 内通过 generated MCP wrappers 处理数据。
- LangGraph checkpoint、Redis SSE buffer、steering queue 支撑长任务。
- Skills 覆盖 DCF、comps、earnings、morning note、coverage、dashboard 等。

量化思路：

- 更偏 fundamental/investment research，而不是高频回测。
- 通过 multi-tier data provider 获取价格、fundamentals、macro、options。
- 通过 workspace memory 和 `agent.md` 让研究跨 thread 复利。

可借鉴点：

- PTC 是处理金融大数据和长研究任务的好范式。
- workspace vault、redaction、sandbox 设计值得参考。

风险：

- 产品和基础设施很重；如果只做本地低频 alpha factory，会显得过度。

### Vibe-Trading

定位：多市场自然语言量化研究和 backtesting workspace。

架构：

- agent CLI/API/Web UI/MCP。
- 77 个 finance skills，36 个 MCP tools。
- 数据 fallback 覆盖 A/HK/US/crypto/futures/forex。
- 有 alpha zoo、shadow account、swarm、report/export。

量化思路：

- 通过 market data loaders 和 skills 把用户问题落到 backtest、factor analysis、trade journal、report。
- alpha zoo 支持 Qlib 158、Kakushadze 101、GTJA 191 等批量 bench。
- shadow account 从个人交易记录提取行为策略并回测。

可借鉴点：

- 多市场数据 fallback 和 skill taxonomy 很有参考价值。
- shadow account 是“用自己的交易日志做量化反省”的独特方向。

风险：

- 能力面很广，核心边界容易膨胀。

### lumibot

定位：Python trading framework，强调 backtest/paper/live 同一策略代码路径。

架构：

- `Strategy` 抽象策略生命周期。
- `backtesting/` 和 `brokers/` 分别承接历史回放和真实 broker。
- `Trader` 负责运行策略。
- AI trading team 作为策略内部的 agent runtime。

量化思路：

- deterministic Python strategy 和 AI agent strategy 都是一等公民。
- 回测中可以复盘 agent 决策、orders、trace、artifacts。
- broker 支持 Alpaca、IBKR、Tradier、Schwab、Tradovate、ProjectX、Bitunix、CCXT 等。

可借鉴点：

- 同一策略跑 backtest 和 live 的接口抽象非常重要。
- AI 决策要能 replay，不然不能称为可审计回测。

风险：

- 面向交易执行广泛场景，和纯因子研究系统重点不同。

### mmr

定位：LLM-native live trading platform。

架构：

- Claude Code 通过 `mmr --json` CLI 操作。
- ZMQ RPC/PubSub/MessageBus 连接 trader_service、data_service、strategy_service。
- DuckDB 保存 tick_data、event_store、proposal_store、position_groups。
- proposal state machine：PENDING -> APPROVED -> EXECUTED，终态不可变。

量化思路：

- agent 每轮 MONITOR -> ANALYZE -> PROPOSE -> DIGEST。
- 永不自动执行，先生成 proposal，用户 approve。
- 策略 runtime 用 RxPY pipelines 接 live tick stream。

可借鉴点：

- CLI JSON surface 是 code agent 最稳的集成方式之一。
- proposal approval pipeline 比“agent 直接下单”安全。

风险：

- live trading 系统复杂度高；ZMQ/msgpack/dill 需要安全审计。

### QuantMind

定位：Qlib 内核驱动的微服务量化平台。

架构：

- API Gateway、Engine Service、Trade Service、Stream Service。
- PostgreSQL、Redis、Celery、本地存储。
- Qlib + Pandas 双回测引擎。
- strategy templates、model registry、inference、trade runner。

量化思路：

- 训练、回测、推理、交易、风控和 dashboard 一体化。
- 偏产品化的 full-stack quant platform。

可借鉴点：

- 微服务边界划分可用于大型系统。
- Qlib service 化和 trade service 分离值得参考。

风险：

- 对个人研究 OS 可能过重。

### QuantMind-yj_exp

定位：`QuantMind` 的 `yj_exp` 分支 worktree，核心价值是把全栈量化平台中的“因子研究”抽成受控 Factor Lab。

架构：

- `backend/services/engine/factor_lab/` 是独立研究子系统，包含 `factor_ir`、`validation`、`evaluation`、`registry`、`orchestrator`、`search_control`、`memory`、`feature_catalog`、`portfolio_backtest`、`promotion`、`scheduler`、`cli` 和 `runbooks`。
- `backend/services/engine/research/` 把 QuantGPT/本地 evaluator 接入 QuantMind 数据和训练链路，覆盖 candidate、run、factor values、campaign、promotion、training、approval、shadow signal、rollback、health。
- `electron/src/features/factorLab/` 提供只读/受控 UI workbench，展示 batch draft、admission、execution、evaluation、memory、portfolio backtest、promotion request 等面板。
- `docker/factor_lab_sandbox/` 定义执行沙箱：无网络、只读、drop capabilities、no-new-privileges、只挂载输入和输出目录。

量化思路：

- 把因子候选当成生命周期对象：candidate -> run -> factor values -> shadow feature promotion -> materialize -> shadow training -> approval -> optional rollback。
- 因子评估不只看一次回测：本地 evaluator、hardened evaluator、IC/RankIC、coverage、missing ratio、turnover、existing feature correlation、holdout/walk-forward、portfolio backtest 都有对应模块或 gate。
- 自动挖掘 campaign 采用有边界的 mutation/crossover，多代 generation 有 quota、worker claim、retry、event log 和 health/SLO。
- Qlib 不是默认强耦合执行内核，而是作为数据兜底和受控 portfolio backtest adapter；adapter 默认 disabled，需要显式策略和配置放行。

可借鉴点：

- “研究工厂”和“生产交易”边界划得很清楚：默认 no-execute，真实 Codex 输出需要 admission、approval 和 Docker，promotion gate 也只表示可进入人工 review，不代表生产启用。
- FactorSpec/Factor ABI、artifact registry、lineage、memory-aware planning 和 review packet 组合起来，比单纯因子 YAML 更接近可运营的研究系统。
- shadow feature、shadow signal、training comparison、approval audit、rollback tool 是从因子研究走向模型/交易前很有参考价值的桥接层。

风险：

- 分支功能面很大，已经接近产品化平台；如果只做个人低频研究 OS，需要抽取 Factor Lab 子系统，不宜整体搬运。
- 当前很多能力依赖 QuantMind 原有 DB、训练快照、API、前端和权限模型，迁移时要先剥离 contract，再考虑复用实现。

### QuantDinger

定位：自托管 AI 量化/交易平台。

架构：

- Vue SPA + Nginx。
- Flask + Gunicorn API。
- PostgreSQL 16 + Redis 7。
- strategy/backtest engine、execution adapters、AI analysis、billing 等模块。
- MCP server 暴露平台工具给 agent。

量化思路：

- 支持 IndicatorStrategy 和 ScriptStrategy。
- 支持 crypto exchanges、IBKR、MT5、Alpaca 等。
- 有 grid runtime、pending order worker、strategy review。

可借鉴点：

- strategy/backtest compute 和 order execution 分离的原则清晰。
- platform routes/services 很全，适合看产品化接口。

风险：

- 商业/计费/平台特性较多，研究核心可能被噪音淹没。

## MCP/Skill/平台接口类

### data-mcp

定位：LLMQuant Data 的 TypeScript MCP server。

架构：

- `register-tools.ts` 汇总所有工具注册。
- 每个 tool 使用 schema + execute function。
- API client 把请求转发到 hosted data API。

量化思路：

- 提供研究所需证据层：wiki、papers、SEC、13F、ETF、macro、equity、crypto、Polymarket。
- 不直接做回测，强调数据可得性和 agent 可调用性。

可借鉴点：

- 工具 registry 和测试结构清楚。
- 适合参考 typed MCP data layer。

### qlib-mcp

定位：把 Qlib 暴露为 MCP tools。

架构：

- FastMCP server。
- 全局 qlib init 状态。
- 工具包括 init、download command、list instruments、get data、factor analysis、TopK backtest、expression help。

量化思路：

- 让 agent 通过 Qlib expression 做快速因子验证。
- 偏轻量 smoke/evaluation，不是完整实验管理。

可借鉴点：

- 最小 Qlib tool surface 可参考。

### quantcontext-mcp-server

定位：确定性 screen/backtest/factor analysis MCP。

架构：

- FastMCP instructions 明确要求调用工具前说明策略和所有参数。
- tools：screen_stocks、backtest_strategy、factor_analysis。
- response truncation 保留 equity_curve contract。

量化思路：

- 从 screen 到 backtest 到 Fama-French factor regression。
- 默认 universe/rebalance/sizing/backtest period。

可借鉴点：

- “调用前明确参数”是很好的 agent UX 和风控模式。

### quantconnect-mcp-server

定位：QuantConnect 官方 MCP server，但本地 README 标注当前 preferred path 是 VSCode embedded MCP。

架构：

- FastMCP 注册 account/project/files/compile/backtests/optimization/live/object_store/AI tools。
- 64 个工具覆盖平台全生命周期。

量化思路：

- 本身不是研究方法，而是 LEAN 平台自动化接口。

可借鉴点：

- 工具 annotations、platform API coverage 和测试布局。

### joinquant-skill

定位：让 AI 正确生成聚宽代码。

架构：

- 14 个 API reference chunk。
- 5 个 strategy template。
- strategy_lint 检查 hallucinated API、future function、复权、commission、slippage、交易时段。
- factor_lab 做 IC、RankIC、分组回测。
- research_importer 做研报 -> prompt -> schema -> 聚宽策略。

量化思路：

- 平台准确性比泛化能力重要。
- 策略模板和 lint 是防幻觉的核心。

可借鉴点：

- 对任何封闭平台，应该先做 reference routing 和 lint。

### finlab-ai

定位：FinLab package skill。

架构：

- skill docs 覆盖 data.get、FinLabDataFrame、sim、factor examples、ML、US market。
- 强调代码例子必须可运行。

量化思路：

- 条件组合 -> position DataFrame -> sim backtest。
- 跨 TW/US/KR/JP/HK 市场，但默认偏 FinLab 数据生态。

可借鉴点：

- 文档质量标准和完整策略例子值得参考。

### worldquant-skill

定位：WorldQuant BRAIN skill/SOP。

架构：

- factor_backtest：Rule of 8、expression validation、create multiSim、monitor、get result。
- alpha-research-recorder：session_meta、round、final_summary 模板。
- knowledge_base_search：字段、操作符、优化经验和好例子检索。

量化思路：

- 批量表达式回测和中性化选择。
- 研究日志结构化，强调经济学假设和解释。

可借鉴点：

- SOP 化和日志模板对 agent 因子研究很有价值。

### kis-ai-extensions

定位：KIS Open API 的多 agent 插件包。

架构：

- 支持 Claude/Cursor/Codex/Gemini 安装。
- skills：strategy builder、backtester、order executor、team、CS。
- hooks：secret guard、prod guard、trade log、mcp log。
- Lean backtester 通过 MCP 暴露 run_backtest、optimize、get_report。

量化思路：

- preset strategy + indicators -> `.kis.yaml` -> Lean backtest -> signal/order。
- 实盘订单必须经 prod guard 和用户确认。

可借鉴点：

- live/prod 安全 hook 设计值得参考。

### revolut-x-api

定位：Revolut X crypto API 的 typed client、CLI、MCP 和 skills。

架构：

- `api/` typed HTTP client。
- `cli/` 提供账户、行情、订单、监控、grid strategy。
- `mcp/` 提供 market data、account、trading、backtest、setup tools。

量化思路：

- crypto market monitoring、candles/order book、grid strategy backtest/optimize/run。

可借鉴点：

- 交易 API 的 CLI + MCP + skill 三层封装。

## A 股和本土化类

### quant-ashare

定位：A 股研究框架。

架构：

- data_adapter：akshare、东财、Sina、Level2、公告、龙虎榜、fundflow。
- factors：Alpha158 lite、reversal、limit、announcements、microstructure、intraday、seat network。
- llm_layer：Hermes XML、多 agent、market context、radar candidates。
- pipeline：research 和 daily_trading。
- execution：simulator、slicers、impact router。

量化思路：

- 明确处理 A 股制度：涨跌停、停牌、T+1、冲击成本、Barra 中性化、事件屏蔽。
- README 记录了 label leak 修复和早期漂亮数字作废。

可借鉴点：

- 本土市场正确性意识强。
- 作废实验和原因写清楚，是成熟研究文化。

风险：

- README 宣称能力很多，实际需要逐模块验证成熟度。

## 资源/索引类

### awesome-quant

定位：量化资源 curated list。

架构：

- README 和 site/projects.csv 管理大量量化库条目。
- 分类包括 numerical、pricing、indicators、backtesting、risk、factor、alternative data、time series、market data 等。

量化思路：

- 不提供策略或回测实现，只提供生态索引。

可借鉴点：

- 技术选型时查漏补缺。
