# Repo Inventory

本文件逐一说明 `/data/codebase/quant_repo/open_source` 下 43 个 agent/研究相关仓库的定位、架构、问题域和量化思路。`vectorbt`、`zipline`、`pyfolio`、`empyrical`、`quantstats`、`bt` 等通用回测/绩效库只作为基础设施参考，不单列。

快照口径：所有仓库已于 2026-09-26 同步到上游最新；2026-09 新增 14 个条目（13 个新 clone + 补录 `AlphaBench`），变化较大的旧条目在对应小节末尾追加“2026-09 更新”。本轮刷新的来源、取舍和勘误见 [landscape-update-2026-09.md](landscape-update-2026-09.md)。

## 总览表

| Repo | 类型 | 面向问题 | 核心架构思路 | 量化方法关键词 | 参考价值 |
|---|---|---|---|---|---|
| `RD-Agent` | Qlib R&D agent | 自动化数据驱动 R&D、因子/模型实现 | scenario + coder + runner + experiment workspace | Qlib、IC、SOTA 因子去重、factor/model co-evolution | 很高 |
| `QuantaAlpha` | 演化式因子挖掘 | 从研究方向自动挖因子 | planning + trajectory + mutation/crossover + regulator | 因子表达式、IC、RankIC、演化搜索 | 很高 |
| `QuantGPT` | 因子研究引擎 | WorldQuant/Cloud 风格因子自动迭代 | expression parser + backtest + anti-overfit + knowledge base | WQ 表达式、group backtest、anti-overfit、cross-review | 高 |
| `AgentQuant` | ReAct 研究 agent | 策略参数研究和记忆复用 | analyze -> hypothesize -> backtest -> reflect -> store；统一 MemoryService + 版本化 harness | regime、grid-constrained LLM、warmup guard、holdout fail-closed promotion | 高 |
| `llm-quant` | 研究治理系统 | 多 track alpha lab 和 paper trading | data + brain + backtest + risk + surveillance + DuckDB | DSR、CPCV、TSMOM、macro、structural arb | 高 |
| `automated-quant-research` | 轻量因子演化实验 | 商品期货 LightGBM 因子进化 | shell pipeline + Claude + DSL + LightGBM + rolling OOS | RankIC、Sharpe、permutation、deflated Sharpe | 中高 |
| `Qlib-with-Claudex` | Qlib/RD-Agent 模板 | 快速跑 Qlib + RD-Agent loop | submodule + scripts + Claude skills | Qlib 数据、IC、R&D loop | 中 |
| `QuantDesk` | 策略实验工作台 | AI 写策略、回测、RM 审查、paper | monorepo + analyst agent + RM gate + Docker engines | Freqtrade、Nautilus、overfit review、run history | 很高 |
| `OpenAlice` | agent trading workspace | 研究/工作区和 broker 执行隔离 | Alice process + UTA service + workspace + MCP；AutoQuant/预测市场工作区模板 + Session 编排 | trading-as-git、guard pipeline、workspace automation | 高 |
| `langalpha` | 持久投研 workspace | 长周期投资研究和 PTC | FastAPI + sandbox + MCP wrappers + LangGraph + Redis/Postgres；automations + computers | DCF、fundamentals、macro、options、workspace memory、定时研究 | 很高 |
| `Vibe-Trading` | 多市场研究 agent | 自然语言研究、回测、报告、shadow account | skills + data routing + MCP + alpha zoo + swarm + 多券商 portfolio | A/HK/US/crypto、alpha zoo、walk-forward、trade journal、验数门 | 很高 |
| `lumibot` | backtest/live 框架 | 同一策略代码跑回测和实盘 | Strategy + BacktestingData + Broker + Trader + AI agents（skills + eval cases） | broker abstraction、agent teams、point-in-time tools、fill 回传审计 | 很高 |
| `mmr` | agent-native live trading + 截面研究 | Claude 通过 CLI 操作实盘系统，并做诚实截面研究 | ZMQ services + DuckDB + JSON CLI + proposal approval + `trader/simulation/` | proposal pipeline、risk gate、PIT universe、walk-forward、PBO/DSR | 很高 |
| `QuantMind` | AI 原生投研平台（QuantMind 2.0） | Qlib 训练、回测、推理、模拟/实盘一体化 | 多服务 + QuantDB/DuckDB + 内嵌 RD-Agent/TradingAgents/alphaagent | Qlib、13 模型工场、300+ 特征、舆情、模拟交易 | 高 |
| `QuantMind-qm2` | 自主研究 OS / 证据治理 | agent 挖因子时如何防污染、可回放、可 fresh 观察 | Decision–Control–Execution + 不可变 Artifact/Ledger + holdout/blind/fresh 隔离 | Factor DSL、default-first、ablation、locked holdout、rolling blind、fresh cohort、BH/HAC | 很高 |
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
| `quant-ashare` | A 股顾问/雷达栈 | A 股特有数据/制度/因子与日常操作台 | data adapters + factors + LLM 辩论层 + 精简 pipelines | 涨跌停、候选池、板块/宏观因子、OOS 修正 | 中高 |
| `awesome-quant` | 资源索引 | 找量化库和资源 | curated list + generated site | pricing、backtesting、risk、factor、data | 中 |
| `AlphaBench` | LLM 因子挖掘 benchmark | 评估 LLM 在因子生成/评估/搜索上的能力 | T1–T4 任务 + FFO⇄Assay 可切换回测后端 | Qlib 表达式、PIT 公司行为、CSI/SP500、CoT/ToT/EA 搜索 | 高 |
| `FactorMiner` | 自演化因子挖掘 agent | 相关性红海下持续挖可解释公式因子 | Ralph Loop：retrieve→generate→四级 evaluate→admit→distill memory | 表达式树 DSL、70 算子、IC/相关/替换/去重、经验记忆、DSR | 很高 |
| `AlphaEvo` | LLM-seeded 演化发现 | 单标的短周期 alpha，防时段过拟合 | LangGraph：知识编译→LLM 种子→GP→EoH→选优 | 安全 DSL、四窗口 IC、IC gap、去相关筛选 | 高 |
| `QuantEvolver` | RFT/GRPO 因子矿工 | 用策略更新替代无限增长的 prompt 反馈环 | seed→DSL 约束解码→可执行评估→task bank→Verl reward bridge | IC/方向奖励、family/novelty/残差互补 shaping | 中高 |
| `AlphaAgent` | A 股多因子研究框架 | panel + DSL + FactorZoo 评估，可选 LLM 挖掘 | Tushare→panel→DSL memmap zoo→MLS/IC 交付门→AgentScope | 截面 IC、MLS-FMB、截面相关去重、交付门槛 | 很高 |
| `deflated-sharpe` | 统计 gate 库 | 多重检验后的 Sharpe 可信度与实盘衰减 | 纯函数 DSR/MBL/BH + RegimeDecayDetector | DSR、FDR、贝叶斯胜率、MDD 超限、Mahalanobis OOD | 中高 |
| `deepseek-harness-quant` | A 股低频研究/决策台 | LLM 驱动但不荐股，写死引擎裁决，Pitch 人工审批 | 驱动层 / 写死引擎 / 事实层；Pitch→五池远期验证 | PIT、T+1、九步入池、证伪留档、ICIR、Beneish | 很高 |
| `kph` | 研究治理后端 + harness 插件 | clean-room R&D，harness 只调用不重算 | KP Python 权威后端 + `kp` JSON CLI + DSH 薄工具映射，fail-closed | sealed holdout、burn budget、内容寻址 replay、promotion gate | 很高 |
| `EvoQuant` | 研报驱动自治研究 agent | 研报→文献→构思→实验→IC | DeepAgents/LangGraph + skills + observation memory + experiment runtime | IC/ICIR/RankIC、anchor-first ideation、Research Artifact entry point | 高 |
| `TradingAgents` | 多 agent 交易决策框架 | LLM 团队辩论出个股决策，网格评估决策质量 | Analyst/Researcher/Trader/Risk/PM + LangGraph checkpoint + decision log | PIT、SEC as-filed、ticker×date 网格、alpha settle | 高 |
| `ai-hedge-fund` | Fund 一等对象的 AI 基金 PoC | mandate 驱动，同一 `run_cycle` 跑回测/实盘 | FundSpec YAML + pods/AlphaModel + Broker protocol + SimBroker | conviction signal、PEAD、blind backtest、next-close 执行 | 中高 |
| `Auto-Quant` | autoresearch × FreqTrade | 不可变 oracle + 策略槽变异 + OOS | `run.py`/`config` 只读；`program.md` + strategies 可写；results.tsv ratchet | MTF、CBBI/AHR999、OOS、oracle-gaming 教训 | 很高 |
| `autoresearch-trading` | 结构/参数拆分的策略搜索 | LLM 写结构，优化器调参，walk-forward 单标量 | `strategy.py` 可写；`trading.py` 只读 BiteOpt + WF | fcmaes/BiteOpt、walk-forward score、stationary bootstrap | 高 |

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

2026-09 更新：

- `src/memory/` 统一为 MemoryService：episodic / beliefs / dreaming sidecar，按 `as_of` 做 point-in-time recall，旧的 SQLite 结果表不再是唯一记忆。
- `.harness/v1..v6` 记录 harness 自身的版本演化：fair search、bounded self-improvement、episode bundle + memo replay。
- 新增 literature/HyDE 检索、tool orchestrator、证据条件的 mutation arms，以及 holdout 评分 + fail-closed promotion。
- 参考价值上调：小型研究 agent 的记忆与防过拟合设计已接近可运营闭环。

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

### RD-Agent 2026-09 更新

- 发布 1.0.0；新增 `utils/archive.py`、`artifact_transport.py`、log server 鉴权/trusted origins、反序列化校验，主要是执行沙箱与 log UI 的安全硬化。
- Qlib factor/model 主路径无大改，`fin_quant` 仍只支持日频；上文 scenario/coder/runner 描述继续有效。
- 外部评估提示：AutoScientist-Quant（arXiv 2608.28632）指出 RD-Agent(Q) 的循环在其报告的窗口上选择因子，并发现 AlphaAgent/QuantaAlpha/RD-Agent(Q) 共用的评估代码曾按全样本计算指标；FactorEngine（arXiv 2603.16365）对比时也为其重新切分了挖掘窗口。复用 `fin_quant` 时应先确认反馈窗口与报告窗口分离。详见 [factor-model-co-optimization-research.md](factor-model-co-optimization-research.md)。

### FactorMiner

定位：带经验记忆与四级验证级联的自演化公式因子挖掘 agent（Ralph Loop，arXiv 2602.14670）。

架构：

- `factorminer/core/ralph_loop.py`：主循环 retrieve → LLM generate → 多阶段 evaluate → library admit → memory distill；`helix_loop.py` 扩展因果/regime/容量/显著性/知识图谱。
- `factorminer/core/expression_tree.py` + `parser.py` + `canonicalizer.py`：表达式树 DSL + SymPy 代数等价去重。
- `factorminer/core/types.py` 注册 70 个算子（7 类，含 GPU backend）。
- `factorminer/evaluation/pipeline.py` + `admission.py`：Stage1 快速 IC → Stage2 库内相关 → Stage2.5 替换 → Stage3 批内去重 → Stage4 全量验证。
- `factorminer/memory/`：`formation` / `evolution` / `retrieval` + `experience_memory.py`（成功模板、禁区、策略洞察）。
- `factorminer/core/library_io.py` 固化论文 110 个公式因子；`tests/` 约 19 个模块。

量化思路：

- 以 IC/ICIR + 库内 Spearman 相关门槛（默认 |IC|≥0.04、ρ<0.5）控制入库。
- 相关过高但明显更强（1.3× IC）时可替换旧因子，缓解“相关性红海”。
- 组合层有等权/IC 加权/Gram-Schmidt，选择层有 Lasso/stepwise/XGBoost。
- Helix 路径再加 bootstrap CI、BH-FDR、deflated Sharpe、regime IC。

可借鉴点：

- 记忆层：失败禁区 + 成功模板跨 session 蒸馏，是 failure memory 目前最干净的结构化实现。
- gate 层：四级级联 + admit/replace 给出了明确的因子对象生命周期。
- 研究对象层：typed 算子 registry + 表达式树 + 规范化，接近 FactorSpec 的精神。

风险：

- 显著性/FDR/DSR 在 Helix 路径，默认 Ralph 主路径不强制执行。
- README 目录仍写 `alphadisk/`，实际包名是 `factorminer/`；demo 依赖 mock 数据。
- 组件多，宜拆记忆、admission、DSL 三块借鉴，不宜整仓迁入。

### AlphaEvo

定位：LLM 塑造搜索空间、GP + EoH 按多窗口 IC 决定存亡的短周期 alpha 发现流水线（NeurIPS 2026）。

架构：

- `alphaevo/pipeline.py`：LangGraph 节点 knowledge_compiler → quant_developer → alpha_compute（GP）→ eoh_refinement → analyst。
- `alphaevo/dsl.py` + `catalog.py`：沙箱表达式 DSL，约 46 个算子白名单。
- `alphaevo/search.py`：确定性 RNG 的 GP；`alphaevo/eoh.py`：EoH 五类结构算子，`MultiInstanceEvaluator` 在 full/early/mid/late 四窗打 IC，并把最弱窗口反馈给改写。
- `alphaevo/evaluator.py` + `selection.py`：train/test IC、fitness、信号去相关；`artifacts.py` / `deployment.py` 落盘证据。
- `scripts/run_paper_experiments.py` + `paper/results/`：可无 LLM 复现实验。

量化思路：

- fitness 以 test IC 为主，惩罚 IC gap、换手、回撤；多窗一致才算鲁棒。
- 未来收益标签由 evaluator 使用，不作为 LLM 的生成目标。
- 去相关过滤的 ablation 显示：去掉后 IC 上升但 IC gap 暴涨。
- 偏单标的时序信号，不是截面多因子工厂。

可借鉴点：

- 评估器/gate：四窗 IC + IC gap + decorrelation 是清晰的反过拟合配方。
- harness：确定性模式、session artifact、deployment bundle 适合证据链。

风险：

- README 的“LLM never sees returns”不完全成立：`alphaevo/llm_utils.py` 的 `compose_window_summary` 会把 5/10/20 日收益摘要注入 prompt，alpha memory 也含 fitness/train IC。更准确的说法是“LLM 不直接优化未来收益标签”。
- 单资产日频，与 A 股截面因子 OS 同构度有限。

### QuantEvolver

定位：把可执行量化评估写成 RFT/GRPO 奖励、让矿工 LLM 内化挖因子经验的框架（arXiv 2605.15412，公开版不含数据和权重）。

架构：

- `quant_evolver/dsl/compiler.py`：算子约束解码，AST 白名单编译为因子类。
- `quant_evolver/seeds/`：生成 → 校验 → 打分 → 去重 → curate 种子库。
- `quant_evolver/evaluation/`：Backtrader 单资产 + 截面 RankIC；`quant_evolver/rewards/`：direction / IC / signal_return + composite。
- `quant_evolver/rft/`：`task_bank`、`reward_bridge`、`NoveltyArchive`、`FamilyArchive`、`ResidualResponseArchive`、Verl backend。

量化思路：

- 用参数更新替代“越来越长的生成-评估-反馈 prompt”，针对上下文爆炸和搜索停滞。
- diversity 路径对完全重复/同族重复惩罚，并对残差行为做互补 shaping。
- seed × 时间切分组成 task bank，供 Verl rollout。

可借鉴点：

- harness：评估器输出直接变成 token-level reward，是“不自研 runtime、但可训练矿工模型”的另一条路。
- 记忆：novelty + family hash + 残差互补相当于把搜索空间记忆写进奖励。

风险：

- 论文里的 DiCo reward 在代码中没有同名实现，对应的是 `diversity_reward` + family/残差 shaping，需要自己对齐术语。
- 强依赖 Verl/vLLM，公开测试只有 3 个 smoke；没有数据只能验证接口。

### AlphaAgent

定位：面向 A 股的 Tushare panel + DSL + FactorZoo 多因子研究框架，可选 AgentScope 挖因子。已不是 KDD 2025 论文的原始形态。

架构：

- `alphaagent/data/`：Tushare 拉取行情/基本面/成分，`build_panel` / `update_panel` 离线构建 panel。
- `alphaagent/dsl/`：pyparsing DSL → 算子求值，支持 `@60m` 等混频。
- `alphaagent/factor/zoo/`：memmap FactorZoo + catalog + `similarity.py` 截面相关去重矩阵。
- `alphaagent/factor/eval.py` + `metrics.py`：IC/RankIC/ICIR、十分组、MLS-FMB、月度稳健性、截面自相关。
- `alphaagent/factor/mining/`：tool-calling loop / AgentScope；`submit.py` 做 delivery check + `max_cs_corr` 入库；`mls_thresholds.py` 按 zoo 分位校准门槛。

量化思路：

- 日频截面因子，ZZ1000 并集宇宙，label 区分价量和基本面。
- 交付门：|IC|/|RankIC|、ICIR、coverage、截面自相关、MLS-FMB、train/val 同号。
- LLM 可选；真正的研究资产是 DSL 表达式与 FactorZoo 证据。

可借鉴点：

- 研究对象层：FactorZoo memmap + 表达式 git 同步，是最接近“因子对象 + artifact”的 A 股实现。
- gate 层：MLS-FMB + delivery check + 相似度门可直接作为 promotion 前闸。

风险：

- 全库没有论文宣称的 AST 原创性、假设-因子对齐、复杂度控制实现；`factor/align.py` 是 panel 行对齐，名字容易误解。
- 数据包不在 git，依赖 Tushare 或网盘。

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

2026-09 更新：

- 新增专项工作区模板 `src/workspaces/templates/auto-quant-v2`、`auto-prediction`，README 把 Chat / AutoQuant / Auto Prediction 分轨。
- Session 生命周期中心化（后台 Session、takeover、interruption），配合 Issues 调度与 `alice` CLI 触发；Electron 本地/relay 双模和远程 Machines。
- UTA broker 包化：`packages/uta-broker-{alpaca,ibkr,ccxt,longbridge,leverup}`；workspace skills/harness 版本化注入。
- 产品面变厚，但执行隔离仍是核心价值，参考价值维持。

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

2026-09 更新：

- Automations：调度 + 统一 settlement 策略（`automation_scheduler` / `automation_settlement`）与报告 feed，支持定时研究复利。
- Computers 与 project folders 分离：计算机级规格、磁盘/备份、文件夹级沙箱作用域；share/grants 支持短链和过期授权。
- 新增 `desktop/`、`plugins/`（market/research/yfinance 等）；`brokerage_*` 服务与 MCP OAuth/catalog 成为一等能力。

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

2026-09 更新（原快照为浅 clone，以下对照当前 README/代码判断）：

- 规模：MCP 工具约 74 个、仓库内约 91 个 `SKILL.md`，上文的 36 / 77 已过期。
- 正确性：figures 声明式验数门、价格口径/复权标签、Alpha Zoo 缺测掩码、A 股强制走 `ChinaAEngine`。
- 产品：多券商 portfolio 聚合、Strategy Discovery、Options Lab、Run Detail 因子 tearsheet；新增 LSE/越南/阿根廷等市场与周/月线。
- 仍是多市场 agent OS 标杆，但能力膨胀更明显，建议只按模块借鉴验数门和数据路由。

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

2026-09 更新（dev 分支）：

- AI agents 层成型：`lumibot/components/agents/skills/`（options/stock/research）、`web_search` / `browser_tools`、`agent_eval_cases/` 评测门、agent rules JSON。
- 回测可审计性加强：真实 fill 价回传 agent、lookahead 修复、DuckDB 多标的历史；新增 PIT SEC Form 4 工具。
- 新 broker/数据：`brokers/polymarket.py`、`polymarket_backtesting.py`。
- 上文“AI trading team”应理解为 skills + eval harness + fill 审计的 agent 运行时。

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

2026-09 更新：

- 长出研究核 `trader/simulation/`：`panel`、`walk_forward`、`selection_bias`、`lookahead_check`、PBO/deflated Sharpe gauntlet。
- 数据层 PIT：point-in-time universe/fundamentals、corporate actions 跳变分类、价格尖刺检疫。
- skills 扩展为 `skills/mmr-skill`、`mmr-loop-skill`、`mmr-verification`、`news`；大量 `tests/invariants` 把研究算术钉成规格。
- 定位从“agent-native live trading”扩为 live 执行 + 诚实截面研究双栈，参考价值上调。

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

2026-09 更新（原快照为浅 clone，以下对照当前 README 与 `docs/QuantMind2.0功能指南.md`）：

- 定位升级为 AI 原生投研平台：内嵌 RD-Agent AutoAlpha、TradingAgents-astock、`alphaagent` 与 skills。
- 数据层改为 QuantDB + Parquet/DuckDB，300+ 预计算特征，RSS/FinBERT 舆情。
- 模型工场 13 种 + stacking；本地模拟交易闭环；实盘通道开源但默认关闭；服务树新增 `live_trading` / `simulation`。
- 上文“API/Engine/Trade/Stream 四服务”只描述了旧骨架。注意 `QuantMind-yj_exp` 与 `QuantMind-qm2` 来自另一个 fork（AlphaCatalyst），和这里的主线是两条路线。

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

快照说明：本目录是开发仓库 `/data/codebase/stock/QuantMind` 的 worktree，上述 Factor Lab 位于本地 15 个未推送 commit 中；上游 `origin/yj_exp` 已走向 qm2 路线（见下一节），本目录不跟随上游更新。

### QuantMind-qm2

定位：上游 `AlphaCatalyst/QuantMind` 的 `yj_exp` 分支在共同祖先 `8d8688ea` 之后另起的 QuantMind 2.0 研究 OS（约 80 个 `feat(qm2)` / `fix(qm2)` commit，2026-07）。核心是把 Hypothesis → Decision → Control → Execution → Artifact/Ledger → Fresh 做成可回放、证据隔离、失败可作废的自主研究流水线；正式回测仍走 Qlib，数据权威切到 Tushare。

架构：

- Decision / Control / Execution 三层分离（`docs/quantmind2/architecture/QUANTMIND_2_ARCHITECTURE_V1.md`、ADR-0009）：agent 只产出 `ResearchDecision`；代码 orchestrator 管预算、幂等和状态机；DSL/优化/验证/Registry/Qlib 只证明事实，不决定下一步。
- 研究入口：`tools/quantmind2/` + `backend/services/engine/{research_campaign,autonomous_factor_campaign,autonomous_factor_program,autonomous_research_supervisor,autonomous_technical_feature_factory}/`。
- 因子执行核：`factor_dsl/` → `factor_optimization/` + `optimization_governance/` → `factor_validation/` → `factor_registry/`；信号与组合在 `unified_signal/`、`strategy_layer/`。
- 证据与记忆三分（ADR-0005）：Research Memory / Project Architecture Memory / Implementation Ledger；不可变对象进 `artifact_store/` + `artifact_runtime/`，工程变更用 Implementation Manifest v2 + git evidence。
- 隔离评估链：locked holdout（`autonomous_factor_campaign/`）→ rolling blind（`rolling_blind_alpha_discovery/`）→ fresh lock / cohort / heartbeat（`fresh_validation*`、`fresh_model_cohort/`、`fresh_heartbeat_scheduler/`）；near-miss 和 holdout failure 写成不可变 artifact。
- agent 边界：`research_campaign/agent.py` 的 `CodexResearchAgent` 以只读、JSON schema、脱敏、超时方式调用 Codex CLI；agent 不能写 Registry、不能看 frozen/holdout/blind 指标、不能自扩预算，执行层永不回调 agent。

量化思路：

- 时间证据分级：development / adaptive_discovery / locked_holdout / contaminated_report / fresh_forward；holdout 打开前先锁 shortlist 并冻结 failure memory，污染即标记 `LOCKED_HOLDOUT_EVIDENCE_CONTAMINATION`。
- default-first 优化治理（`FACTOR_OPTIMIZATION_POLICY_V2`）：默认参数过门就冻结；失败只允许单参数邻域救援（≤7 trials）；全量搜索只做诊断，不能用来选参或晋升。
- `parameter_optimization_ablation/` 做 walk-forward 消融，把结果分为 likely_robust / likely_overfit / inconclusive，并反过来改写优化策略。
- rolling blind 用 60 交易日盲窗 + 全局 BH；fresh 要求 no-backfill 和最低证据量，cohort 级 HAC/BH；`fresh_supported` 仍没有 promotion 权。
- 搜索暴露账本记录有效搜索次数，只作 tie-break，从不放宽资格门。

可借鉴点：

- gate / 评估器：locked holdout、rolling blind、fresh lock 的物理裁剪 + 读取计数 + BH-FDR，可直接映射研究 OS 的 gate 层。
- 证据链：内容寻址 artifact + 零调用 exact replay + git evidence 校正。
- harness：Codex 只提案，admission/orchestrator 才执行，是蓝图“agent adapter 不拥有真值”最完整的落地。
- 记忆：研究记忆与工程 ledger / 项目知识分离，避免 campaign 记忆污染架构决策。

风险：

- 约 16 万行新增、强契约，个人研究 OS 只能按层抽取协议，不能整仓搬运。
- fresh runtime 绑 macOS 本地路径与 LaunchAgent，迁移成本高。
- 多轮正式 batch 以 `completed_no_*_survivor` 收场：治理成熟，但“自主发现可交易 alpha”尚未被证明。

与 `QuantMind-yj_exp` 的分工：yj_exp 是受控候选工厂（FactorSpec、admission、Docker sandbox、shadow feature → 训练/审批/回滚），适合借鉴可运营工厂和生产桥接；qm2 是自主 campaign + 证据隔离 OS，适合借鉴 gate、证据链、优化治理和 agent harness。

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

2026-09 更新：

- `services/ai_decision*` 提供可审计的 pre-trade 决策过滤（JEV）与审计 trail。
- 实盘硬化：交易所 fill 对账、grid ownership、execution streams；研究侧新增 `fundamental_sync`、`universe`、`event_radar`（reference-only）。
- MCP `tool_contract` / `security` 强化；因子研究仍非主线，参考价值维持。

### deepseek-harness-quant

定位：自然语言驱动的 A 股低频系统：LLM 只负责驱动、挖因子和审计，写死引擎负责评分/回测/风控，事实层负责 PIT/T+1 与可证伪裁决。

架构：

- 三层边界（README、`docs/架构.md`）：`harness/`（DeepSeek HARNESS / Cordis 插件与技能）、`factors|strategy|risk|backtest/`（确定性 Python）、`data/cache.py`（本地 SQLite 唯一读取入口）。
- Pitch 决策链：`factors/opportunities/scan.py` / `pitch_v2` → Deck 审批 → `strategy/pitch.py`，人工 approve/reject 是唯一买入来源。
- 五池远期验证：`factors/opportunities/pitch_track.py` 分 `auto_pitch` / `machine_top01` / `human_select` / `ai_select` / `niu_select` 五池，`FWD_HORIZONS=(1,5,20,60)` 滚动更新。
- 风控：`docs/资产盘点.md` 定义七道（数据审计、因子健康、FRC、Beneish、竞价、单因子依赖、L0 门控）；订单硬约束在 `risk/risk_agent.py`。
- 因子生命周期：`factors/pool/lifecycle.py` + `validation/factor_evaluator.py`（8 维体检）；AI 能力以 `assets/skills/` 的 SKILL.md 交付。

量化思路：

- 硬约束“AI 不预测个股”：假设由 LLM 提出，入池由写死验证链裁决。
- 九步入池（skill 规程）：定域 → 生成 → ICIR → 去重 → 组合层 T+1 → 分年度 holdout → 正交 → 容量 → 归档，证伪结果公开留档。
- 执行纪律：T+1 开盘、一字板过滤、成本模型。

可借鉴点：

- 证据层：按决策来源分池做远期追踪，比单次回测更像“人 vs 机器 vs AI”的对照实验。
- 失败记忆：证伪知识库 + 九步闸门把“全部证伪也是有效产出”写进流程。
- 薄 runtime：harness 缺席时写死引擎仍可运行，agent 可替换。

风险：

- README 宣称 123+ 因子，但 `factors/factor_engine.py` 的 `FACTOR_FUNCS` 只注册 6 个；大量因子名只出现在 `factors/signal_family.py` 和文档里。
- skill 引用的 `core.combo_backtest` 不在仓库中，九步裁决链部分依赖未随仓发布的资产。
- `validation/` 多为实证脚本而非 pytest 套件，偏个人研究台。

### kph

定位：knowledge-pipeline 的独立开发副本：确定性研究后端 + 以插件方式外挂 DeepSeek Harness，harness 只调用、只展示、不重算。

架构：

- 后端权威面：`backend/agents/rd_loop/`、`backend/finance_layer/`、`backend/research_os/`；指标权威来自外部 StockAutoCN（`backend/agents/strategy_loop/backtest_client.py`）。
- 产品入口 `backend/cli/`：`kp doctor/gate/artifact/evidence/run/rd`，统一 `kp.response/v1` 信封 + 语义退出码。
- harness 接入面只在 `harness/dsh/` + `.dsh/skills/`；`harness/dsh/plugin/kp/kp-tools.js` 通过 `uv run python -m cli.main --json ...` 透传。
- 只读工具直通；`kp_run` 全部 action 与 `kp_rd beat` 需要审批，fail-closed。
- 治理资产：`backend/agents/rd_loop/loop_os/burn_budget_guard.py`、内容寻址 replay 协议、promotion/baseline gate；`backend/tests` 约六百个用例。

量化思路：

- clean-room R&D：ResearchPlan、baseline、promotion，sealed holdout 预算耗尽即阻断。
- KP 自算指标只做诊断，不参与排名和 promotion；discretionary skill 标记 `advisory_only`。

可借鉴点：

- 契约层：把“harness 永不成为第二计算权威”写成 AGENTS 硬规则并在插件里实现。
- 工具映射：gate/artifact/run 映射到 JSON CLI，是“MCP/CLI 只是入口”的样板。
- 反过拟合：holdout burn budget + replay 签名。

风险：

- 体量大，路径写死本机 sibling 仓库（`stock_autocn`），完整运行依赖 Docker 与数据面板。
- “不重算”依赖约定 + 薄插件，agent 走裸 bash 仍可绕开，需要审批策略配合。

### EvoQuant

定位：面向量化研究的自治 agent（EvoScientist / DeepAgents / LangGraph 血统）：研报入库 → 本地文献 → anchor-first 构思 → 实验 runtime → IC 族评估。

架构：

- 运行时：`EvoQuant/` 包（gateway、cli、llm、mcp、channels、cron）+ `subagents/*.yaml`。
- 能力以 `EvoQuant/skills/` 交付：`quant-paper-extractor`、`local-paper-navigator`、`research-ideation`、`quant-experiment-runtime`、`evo-memory` 等，可单独给 Claude Code/Codex 用。
- 实验执行器：`discover_data.py` → `build_panel.py` → Research Artifact entry point → `_metrics.py`（IC/ICIR/RankIC/coverage）。
- 记忆：`EvoQuant/memory/` 文件型 observation 图；语料契约 `papers/{raw,markdown,cards}`。

量化思路：

- 研报没有代码时按可复现深度重实现，用离线面板验证，不信 PDF 里的数字。
- 构思规程：继承锚点方法 ≥70%、创新 ≤30%，ELO 排序（写在 skill 规程里，不是独立引擎）。

可借鉴点：

- 研究对象：Research Artifact / entry point / ExperimentResult 约定，适合做 OS 的候选对象抽象。
- skills 可剥离给薄 runtime，符合“agent 可替换”。

风险：

- 全栈 agent 产品（多 channel、WebUI）很重，宜抽 skills 而非整仓嵌入。
- 依赖离线数据包；没有数据时 loop 停在构思阶段。

### TradingAgents

定位：TauricResearch 的多 LLM agent 交易决策框架（v0.5.x），模拟投研团队辩论出个股决策，并强化 PIT 与决策日志。

架构：

- `tradingagents/agents/`：analysts / researchers / trader / risk_mgmt / managers；编排在 `tradingagents/graph/trading_graph.py`。
- 数据：`tradingagents/dataflows/vendors/`（yahoo、alpha_vantage、`sec_edgar.py`、FRED、社交）+ 日期窗口防前视。
- 评估：`tradingagents/backtest.py` 对 ticker×date 网格运行同一图，结果写入 `decision_log.py`（append-only），再结算 realized/alpha。
- 恢复：`graph/checkpointer.py`；组合感知通过 `portfolio.py` 注入上下文，不是仓位仿真器。
- `tests/` 约 75 个，覆盖基本面前视、SEC as-filed、FRED 窗口。

量化思路：

- 决策质量评估明确声明“不是组合模拟器”，每个网格单元独立。
- SEC 基本面按申报时点 as-filed 取数，测试对比重述前后数字。

可借鉴点：

- 事实层：as-filed + 前视测试是美股基本面研究的硬要求样板。
- 证据链：decision log 作为 append-only 结果表，利于审计与 resume。

风险：

- 核心是 LLM 决策，不是因子工厂；网格评估成本高、方差大。
- 依赖多个 vendor API key，不是 A 股低频主战场。

### ai-hedge-fund

定位：virattt 的 v2 重构，把“基金”当一等持久对象：YAML mandate、pods/alpha models、Broker 协议、同一 `run_cycle` 路径。

架构：

- `hedge_fund/fund/spec.py` 定义 FundSpec / StrategySpec / ModelSpec，示例 `hedge_fund/fund/example.yaml`，标的运行时注入。
- `hedge_fund/pipeline/run_cycle.py`：assess → 下一完整交易时段执行；`hedge_fund/brokers/protocol.py` + `sim.py`。
- `hedge_fund/signals/`：LLM 人格与 PEAD 等向量化模型共用 `AlphaModel` → `Signal(conviction, thesis)` 接口。
- `hedge_fund/backtesting/fund.py` 调用同一 cycle；LLM `blind=True` 隐去 ticker 和日历，减少模型记忆污染。

量化思路：

- 多 sleeve 净额后由 master risk 限制仓位和敞口；再平衡频率写在 mandate 里。
- 系统化模型与 LLM 主观模型可混编在同一 pod。

可借鉴点：

- 研究对象：mandate YAML 作为 desk 契约，可启发“实验规格”设计。
- 评估诚实性：blind prompt 是 LLM 回测防记忆污染的简单有效手段。

风险：

- ROADMAP 自承持久账本、CPCV/PBO 未完成，成熟度中等。
- 依赖 Financial Datasets 等外部 API，量化深度弱于因子 lab。

## Research Harness 类

这一类是 Karpathy autoresearch 范式在量化上的移植：不可变 evaluator + 少量可编辑工件 + git keep/discard ratchet。它们本身不是平台，但就是蓝图“最小合格版本”的极简形态。

### Auto-Quant

定位：auddk25 把 autoresearch 模式套在 FreqTrade 加密策略上：不可变 oracle + 可编辑策略槽 + OOS 验证，并把 oracle-gaming 教训写进版本史。

架构：

- 不可变：`config.json`、`prepare.py`、`run.py`（FreqTrade in-process 批量回测 oracle）。
- 可写工作区：`user_data/strategies/`（最多 3 个活跃槽，create/evolve/fork/kill）+ `program.md` 规定 loop。
- ratchet 日志：`results.tsv`（gitignored，`git reset` 后仍保留）；`versions/*/retrospective.md` 冻结每一版的复盘。
- OOS：`val.py` + `user_data/data_val/`；推荐分支 `v0.4.0`，其余分支为归档实验。

量化思路：

- v0.1 单文件变异时 agent 钻了 oracle 的空子（如 `exit_profit_only`），表面 Sharpe 1.44，真实约 0.19；v0.2 改为多策略对照，使 gaming 可见。
- 连续 3 轮 stable 必须 evolve/fork/kill，防止槽位闲置。
- 结论偏保守：牛市难超 buy-and-hold，价值主要在避开熊市。

可借鉴点：

- harness：不可变 evaluator + 可编辑对象 + keep/discard 是研究 OS 最小闭环模板。
- 失败记忆：retrospective 作为下一版 `program.md` 的输入，是失败记忆的强形式。
- gate：训练 oracle 与 `val.py` 分离，接近 promotion 前必过闸。

风险：

- 绑定 FreqTrade/加密；oracle 指标仍可能被新方式钻空子，防御靠纪律而非形式化保证。
- 测试少，README 绩效表高度依赖特定窗口。

### autoresearch-trading

定位：dietmarwo 版本：LLM 设计策略的离散结构，fcmaes/BiteOpt 优化连续参数，walk-forward 产出单标量 SCORE 决定 keep/discard。

架构：

- `agent.py`：读 `program_trade.md`，改 `strategy.py`，调用 `trading.py`，解析 SCORE，git keep/revert，写 `results.tsv`。
- `trading.py`（只读）：`load_strategy` → `walk_forward` → BiteOpt 内层优化 → `compute_score` → 可选 stationary bootstrap。
- `strategy.py` 返回 variables/bounds/`simulate`（numba）；`strategy_helpers.py` 提供 `@njit` 指标原语。

量化思路：

- 把“结构创意”和“参数搜索”拆开，避免 LLM 因为一组坏阈值抛弃好结构。
- C++ 优化器 + numba 让内层可以穷举参数，给 LLM 更干净的结构反馈。

可借鉴点：

- 搜索空间治理：LLM 只动结构和边界，连续参数交给确定性优化器，可迁移到“因子表达式 + 超参”场景。

风险：

- walk-forward + 内层优化仍有选择偏差，bootstrap 不等于 holdout promotion；没有 artifact/gate/ledger。

## 评估与统计 gate 类

### AlphaBench

定位：CityU-MLO 的 LLM 公式因子挖掘 benchmark（ICLR 2026）。此前 inventory 漏录，2026-09 补入。

架构：

- 四类任务：T1 因子生成、T2 免回测因子评估、T3 迭代搜索（CoT/ToT/EA）、T4 原子评估。
- `ffo/` 评估服务：默认 Qlib evaluator，可通过 `FFO_ENGINE=assay`（或 `ffo.yaml` 的 `engine.backend`）切换到 Assay 后端，见 `ffo/utils/assay_engine.py`、`ffo/ASSAY_ENGINE.md`。
- Assay 宣称 point-in-time 公司行为、美股 + A 股（CSI300/500/1000）、同时接受 Qlib 与 `ts_*/cs_*` 两种表达式写法。

可借鉴点：

- 评估器层：可切换回测后端 + 统一 REST/CLI，适合作为因子评估服务的接口参考。
- 用 benchmark 任务拆分检验 agent 在生成/评估/搜索各环节的真实能力。

风险：

- Assay PIT 正确性是项目自述，需要用自有数据交叉验证。

### deflated-sharpe

定位：零强依赖的 Deflated Sharpe / FDR / 实盘 regime 衰减监控小库。

架构：

- `src/deflated_sharpe/gates.py`：`deflated_sharpe_ratio`、`min_backtest_length`、`benjamini_hochberg`。
- `src/deflated_sharpe/regime.py`：`RegimeDecayDetector`（胜率贝叶斯衰减 / 1.5×MDD / Mahalanobis OOD，三选二表决）。
- `tests/` 含论文数值校验；可 pip 安装。

可借鉴点：

- gate 层：可直接挂在 promotion 或参数搜索出口；BH-FDR 适合多因子同时报 p 值。
- 搜索前用 minimum backtest length 估计样本量，避免窗口太短永远过不了门。

风险：

- `num_trials` 估计不准会误杀或误放，必须配合完整的 trial ledger。
- regime 特征偏交易策略，迁到截面因子需重新定义。

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

2026-09 更新：v0.7 工具契约改为 `limit` / `query`，历史序列 Top-N + `take_from`，未知参数直接拒绝；这是“大结果不进上下文”的严格参数面样板。

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

2026-09 更新：`scripts/strategy_lint.py` 与 `research_importer/generator/strategy_code.py` 加强，importer 补了测试；骨架不变。

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

2026-09 更新：MCP 改为托管端点 `mcp.finlab.finance/mcp`，移除本地 workers；skill 对齐 finlab 2.x 的 `sim()` 与 HTML report。

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

2026-09 更新：补齐交易生命周期（transactions、TWAP、`on_fill`、平均成交价）、订单簿深度至 192、grid trailing；MCP 新增 knowledge base tool；回测 taker fee 修正。

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

2026-09 更新：

- 架构收缩：删除了未接实盘的 `corporate_actions`、`data_hygiene`、`execution`、`pipeline`、`portfolio_opt` 等模块，上文 Level2/冲击成本/执行模拟的描述大部分已不在当前代码中。
- 运营面增强：明日操作台辩论层、`dazi_monitor`、板块差距/美股动向/Polymarket 宏观因子、Top800 候选池。
- 定位变成 A 股顾问/雷达栈，对通用因子研究的可迁移面变窄，参考价值下调为中高；A 股制度意识和作废实验记录仍值得看。

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
