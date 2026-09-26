# Reference Priorities

本文件给出后续深入阅读和复用优先级。优先级依据是：架构清晰度、对量化研究 OS 的可迁移价值、代码/文档完整度、和本地低频量化研究的相关性。

## P0: 应该深读源码

### RD-Agent

为什么：

- 最接近 Qlib-first 因子/模型 R&D loop。
- scenario、experiment、runner、workspace 的分层成熟。

重点读：

- `rdagent/scenarios/qlib/experiment/factor_experiment.py`
- `rdagent/scenarios/qlib/developer/factor_runner.py`
- `rdagent/scenarios/qlib/proposal/factor_proposal.py`
- `rdagent/scenarios/qlib/experiment/factor_template/`

适合借鉴：

- Qlib 因子实验模板。
- 新因子和 SOTA 因子合并/去重。
- agent-facing scenario 描述方式。

### QuantaAlpha

为什么：

- 因子挖掘的演化控制、轨迹池、表达式 AST 和 regulator 都有直接参考价值。

重点读：

- `quantaalpha/pipeline/factor_mining.py`
- `quantaalpha/pipeline/evolution/controller.py`
- `quantaalpha/factors/library.py`
- `quantaalpha/factors/regulator/factor_regulator.py`
- `quantaalpha/factors/coder/factor_ast.py`

适合借鉴：

- trajectory-based factor mining。
- mutation/crossover 的调度。
- 因子库 metadata 结构。

### QuantDesk

为什么：

- 最值得参考的不是交易引擎，而是 agent 实验工作台和 Risk Manager gate。

重点读：

- `server/src/services/agent-runner.ts`
- `server/src/services/prompts/risk-manager.ts`
- `server/src/services/risk-manager-context.ts`
- `server/src/mcp/server.ts`
- `packages/engines/src/docker.ts`

适合借鉴：

- analyst/RM 双 agent。
- run history + code diff + analyst trail 审查。
- Docker sandbox 执行策略代码。

### llm-quant

为什么：

- 研究治理最强：DSR、CPCV、paper gate、surveillance、append-only registry。

重点读：

- `src/llm_quant/backtest/robustness.py`
- `src/llm_quant/backtest/walk_forward.py`
- `src/llm_quant/risk/manager.py`
- `src/llm_quant/surveillance/`
- `docs/research/`

适合借鉴：

- anti-overfit gate。
- track-based research program。
- paper trading 前的完整性门槛。

### QuantMind-yj_exp

为什么：

- 最接近“把因子研究做成受控工厂”的本地样本。
- 不只是 agent 生成因子，还覆盖 candidate pipeline、admission、sandbox、evaluation、registry、memory、feature catalog、promotion、training approval 和 rollback。
- 对个人低频研究 OS 最有价值的是 Factor Lab 子系统，不是整套 QuantMind 平台。

重点读：

- `backend/services/engine/factor_lab/README.md`
- `backend/services/engine/factor_lab/orchestrator/candidate_pipeline.py`
- `backend/services/engine/factor_lab/evaluation/`
- `backend/services/engine/factor_lab/memory/`
- `backend/services/engine/factor_lab/promotion/promotion_gate.py`
- `backend/services/engine/factor_lab/portfolio_backtest/qlib_backtest_adapter.py`
- `backend/services/engine/research/`
- `electron/src/features/factorLab/`
- `docker/factor_lab_sandbox/README.md`

适合借鉴：

- FactorSpec/ABI + artifact registry + lineage/report。
- 默认 no-execute、Codex admission、Docker sandbox、只读 UI workbench。
- shadow feature/signal、promoted vs baseline training、approval audit、rollback。

### QuantMind-qm2（2026-09 新增）

为什么：

- 同一 fork 的上游 qm2 路线，是目前“证据隔离 + agent 只提案”做得最完整的样本。
- 与 yj_exp 互补：yj_exp 管受控候选工厂，qm2 管 holdout/blind/fresh 隔离与优化治理。

重点读：

- `docs/quantmind2/architecture/QUANTMIND_2_ARCHITECTURE_V1.md`、`RESEARCH_DECISION_CONTRACT_V1.md`
- `docs/quantmind2/contracts/AUTONOMOUS_FACTOR_RESEARCH_CAMPAIGN_V2.md` + `backend/services/engine/autonomous_factor_campaign/`
- `docs/quantmind2/contracts/FACTOR_OPTIMIZATION_POLICY_V2.md` + `optimization_governance/`、`parameter_optimization_ablation/`
- `docs/quantmind2/architecture/ROLLING_BLIND_ALPHA_DISCOVERY_V1.md` + `rolling_blind_alpha_discovery/`
- `backend/services/engine/research_campaign/agent.py`
- `docs/quantmind2/context/CURRENT_STATE.md`

适合借鉴：

- locked holdout、rolling blind、fresh lock 的证据分级与污染标记。
- default-first 参数治理和搜索暴露账本。
- Decision/Control/Execution 分离，Codex 只产出结构化决策。

### FactorMiner（2026-09 新增）

为什么：

- 经验记忆（成功模板 + 禁区）和四级验证级联是 failure memory、admission gate 最干净的实现。

重点读：

- `factorminer/core/ralph_loop.py`
- `factorminer/evaluation/pipeline.py`、`admission.py`
- `factorminer/memory/experience_memory.py`、`formation.py`
- `factorminer/core/types.py`

适合借鉴：

- memory formation / evolution / retrieval 三个操作的划分。
- admit / replace 规则：相关过高但显著更强时替换旧因子。

### AlphaAgent（2026-09 新增）

为什么：

- 已重写为 A 股 FactorZoo 框架，是最接近“A 股因子对象 + 交付门”的现成实现。
- 注意：论文里的 AST 原创性等机制不在当前代码中，读它是为了 FactorZoo，不是为了论文方法。

重点读：

- `alphaagent/factor/zoo/zoo.py`、`similarity.py`
- `alphaagent/factor/eval.py`、`metrics.py`
- `alphaagent/factor/mining/submit.py`、`mls_thresholds.py`

适合借鉴：

- memmap 因子库 + 表达式 git 同步。
- 按库内分位校准的交付门槛。

### kph（2026-09 新增）

为什么：

- 把“harness 只调用、只展示、不重算”写成硬规则并在插件层实现，是 agent boundary 的最佳样板。

重点读：

- `AGENTS.md`
- `harness/dsh/plugin/kp/kp-tools.js`
- `backend/cli/commands/`（gate / artifact / run / rd）
- `backend/agents/rd_loop/loop_os/burn_budget_guard.py`

适合借鉴：

- `kp.response/v1` JSON 信封 + 语义退出码。
- sealed holdout burn budget；只读工具直通、写操作 fail-closed 审批。

## P1: 应该作为系统形态参考

### Research Harness：Auto-Quant / autoresearch-trading（2026-09 新增）

适合看：

- 不可变 evaluator + 可编辑工件 + git keep/discard ratchet，是蓝图最小合格版本的极简形态。
- `Auto-Quant` 的 `versions/*/retrospective.md`：oracle-gaming 实证与多策略对照的防御。
- `autoresearch-trading` 的结构/参数拆分：LLM 只动结构，优化器管连续参数。

不建议直接复用：

- 没有 artifact registry、ledger 和 promotion gate，只适合做外环快速试错。

### deepseek-harness-quant（2026-09 新增）

适合看：

- 驱动层 / 写死引擎 / 事实层三层边界。
- `factors/opportunities/pitch_track.py` 的五池远期验证：按决策来源（机器 / 人 / AI）分池追踪 T+1/5/20/60。
- 九步入池与证伪留档的 skill 规程。

不建议直接复用：

- 因子引擎实际只注册 6 个因子，九步链依赖未随仓发布的模块；借鉴设计，不借鉴实现。

### AlphaEvo / EvoQuant（2026-09 新增）

适合看：

- `AlphaEvo`：四窗口 IC + IC gap + 去相关的评估配方（`alphaevo/evaluator.py`、`eoh.py`）。
- `EvoQuant`：Research Artifact entry point + IC runtime skill（`skills/quant-experiment-runtime/`），研报复现流程。

不建议直接复用：

- `AlphaEvo` 是单资产时序；`EvoQuant` 全栈 agent 产品较重，只抽 skills。

### TradingAgents / ai-hedge-fund（2026-09 新增）

适合看：

- `TradingAgents`：SEC as-filed + 前视测试、append-only decision log。
- `ai-hedge-fund`：mandate YAML、同一 `run_cycle`、LLM blind backtest。

不建议直接复用：

- 两者都是 LLM 决策框架，不是因子研究内核。

### langalpha

适合看：

- persistent workspace。
- Programmatic Tool Calling。
- sandbox、vault、redaction、SSE replay、checkpoint。

不建议直接复用：

- 全套基础设施太重。

### Vibe-Trading

适合看：

- finance skill taxonomy。
- 多市场 data fallback。
- alpha zoo benchmark。
- shadow account。
- MCP/API server 组合。

不建议直接复用：

- 能力面很大，容易把研究 OS 做散。

### lumibot

适合看：

- Strategy/Broker/BacktestingData 抽象。
- 同代码路径 backtest/paper/live。
- AI trading team 的 replay/audit 思路。

不建议直接复用：

- 如果核心是 A 股低频因子，`lumibot` 的 broker/live 优先级可能不是第一阶段重点。

### OpenAlice

适合看：

- Alice/UTA 拆分。
- Trading-as-Git。
- workspace + native CLI。
- 凭证隔离和 guard pipeline。

不建议直接复用：

- 适合交易执行平台，不是因子研究核心。

### QuantMind / QuantDinger / mmr

适合看：

- 交易服务边界。
- order/position/risk/account model。
- audit log 和 proposal approval。
- `QuantMind-yj_exp` 中研究结果进入模型/信号前的 shadow/promotion/approval/rollback 状态机。
- 2026-09 起 `mmr` 新增 `trader/simulation/`（PIT universe、walk-forward、selection bias、PBO/DSR gauntlet）和 `tests/invariants`，研究诚实性部分值得单独深读。

不建议直接复用：

- 平台化包袱较重，容易引入过多与研究无关的模块。

## P2: 适合作为 tool/skill/API 参考

### joinquant-skill

价值：

- 平台 skill 的最佳样本之一。
- reference routing、template、lint、MCP 暴露都完整。

适合借鉴：

- 如果要做任意平台适配，先照这个结构做。

### finlab-ai

价值：

- 高质量 skill 文档。
- 完整可运行示例和市场 caveat。

适合借鉴：

- 文档标准和 code example 规范。

### worldquant-skill

价值：

- 因子回测 SOP 和研究日志模板。

适合借鉴：

- Rule-based workflow。
- session/round/final_summary 结构化记录。

### kis-ai-extensions

价值：

- 多 agent 插件安装结构。
- secret/prod guard hooks。
- strategy -> backtest -> order 的阶段确认。

适合借鉴：

- live/prod 安全保护。

### data-mcp / qlib-mcp / quantcontext-mcp-server / quantconnect-mcp-server

价值：

- MCP tool schema、tool registry、response truncation、platform API exposure。

适合借鉴：

- typed financial data tools。
- Qlib 最小 MCP surface。
- backtest/factor_analysis tool contract。

### deflated-sharpe / AlphaBench（2026-09 新增）

价值：

- `deflated-sharpe`：可直接嵌入的 DSR、minimum backtest length、BH-FDR 与实盘衰减监控。
- `AlphaBench`：`FFO_ENGINE` 可切换的因子评估后端（Qlib ⇄ Assay PIT），以及 T1–T4 能力评测任务。

适合借鉴：

- promotion 出口的统计 gate；DSR 的 `num_trials` 必须来自完整 trial ledger。
- 因子评估服务的可替换后端接口。

## P3: 只在特定方向需要时深挖

### quant-ashare

适合：

- A 股市场制度、Level2、涨跌停、停牌、事件屏蔽、冲击成本、作废实验记录。

注意：

- 需要逐模块验证成熟度，不要只按 README 宣称采纳。
- 2026-09 起执行、组合优化等模块已删除，转向顾问/雷达栈，可迁移面进一步变窄。

### QuantEvolver（2026-09 新增）

适合：

- 有 agentic RL 训练资源、想把因子评估器变成 RFT 奖励时（`quant_evolver/rft/reward_bridge.py`、`novelty.py`）。

注意：

- 公开版没有数据和权重，只能验证接口；论文的 DiCo reward 需对照代码自行对齐。

### automated-quant-research

适合：

- 快速做商品期货/LightGBM/DSL factor evolution proof-of-concept。

注意：

- shell pipeline 不适合作为长期系统主干。

### AI-Trader

适合：

- agent 信号平台、copy trading、leaderboard、community/reward。

注意：

- 更偏平台网络效应，不是本地研究系统核心。

### revolut-x-api

适合：

- crypto exchange API + CLI + MCP + skill 封装。

注意：

- 市场和交易所绑定强。

### cbt-framework

适合：

- Claude Code slash-command 工作流。
- backtest 项目脚手架。

注意：

- 更像通用开发框架，量化 engine 深度有限。

### awesome-quant

适合：

- 查找基础库和生态资源。

注意：

- 不是架构样本。

## 推荐阅读顺序

如果目标是设计自己的本地低频 quant research OS：

1. `Auto-Quant` / `autoresearch-trading`：先用最小 ratchet 理解“不可变 evaluator + 可编辑工件”的闭环和 oracle-gaming 风险。
2. `RD-Agent`：学 Qlib research loop 怎么包装给 agent。
3. `QuantaAlpha` / `FactorMiner`：学因子演化、trajectory、regulator，以及经验记忆和四级 admission。
4. `QuantMind-yj_exp`：学习 Factor Lab 如何把候选因子管成有 gate、有 memory、有 promotion 的研究工厂。
5. `QuantMind-qm2` / `kph`：补证据隔离（holdout/blind/fresh）、优化治理、harness 不重算契约。
6. `llm-quant` / `mmr` 的 `trader/simulation/` / `deflated-sharpe`：补研究治理、PIT 与多重检验 gate。
7. `QuantDesk`：补独立 RM 审查和实验工作台。
8. `AlphaAgent` / `deepseek-harness-quant`：A 股落地时参考 FactorZoo 交付门和五池远期验证。
9. `AgentQuant`：抽取一个最小可实现 ReAct loop 与 as-of 记忆。
10. `joinquant-skill` / `finlab-ai` / `worldquant-skill` / `EvoQuant` skills：学习 skill 化和 SOP。
11. `lumibot` / `OpenAlice` / `ai-hedge-fund`：等需要 paper/live 或 broker 边界时再读。

## 可直接迁移的设计清单

- Factor schema：表达式、数据源、lag、universe、period、metrics、artifact。
- Factor parser/regulator：复杂度、重复、算子白名单、时序/截面语义。
- Factor Lab boundary：默认不执行、admission 后沙箱执行、promotion 只进人工 review。
- Experiment registry：append-only，记录失败、作废、参数、代码 hash。
- RM review：默认 reject，读 run history、code diff、trade count、turnover、drawdown。
- Data provenance：provider、adjustment、timestamp、fallback path。
- Workspace artifacts：每次研究输出报告、图表、trace、factor card。
- Skill routing：按平台/任务加载最小 reference，模板优先，lint 必跑。
- Execution boundary：paper/live 显式切换，真实订单前确认，audit log 全覆盖。
- Trial ledger：所有评估都经唯一入口并入账，DSR/PBO 的试验数与方差取自 ledger（honest evaluation、`QuantMind-qm2` 搜索暴露账本）。
- Holdout 预算：holdout 打开前锁 shortlist、冻结 failure memory，读取次数计入 burn budget（`QuantMind-qm2`、`kph`）。
- Default-first 优化：默认参数过门即冻结，失败只允许有限邻域救援，全量搜索仅诊断（`QuantMind-qm2`）。
- Harness 不重算：agent 侧工具只透传 JSON CLI，计算和 gate 判定只在后端（`kph`）。
- 经验记忆：成功模板 + 禁区 + 策略洞察，分 formation / evolution / retrieval（`FactorMiner`）。
- 远期分池验证：按决策来源分池追踪多个 horizon 的前瞻收益（`deepseek-harness-quant`）。
- LLM 回测去记忆：blind prompt 隐去 ticker 与日历（`ai-hedge-fund`）。
