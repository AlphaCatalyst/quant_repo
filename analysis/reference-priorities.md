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

## P1: 应该作为系统形态参考

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

## P3: 只在特定方向需要时深挖

### quant-ashare

适合：

- A 股市场制度、Level2、涨跌停、停牌、事件屏蔽、冲击成本、作废实验记录。

注意：

- 需要逐模块验证成熟度，不要只按 README 宣称采纳。

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

1. `RD-Agent`：先学 Qlib research loop 怎么包装给 agent。
2. `QuantaAlpha`：再学因子演化、trajectory、regulator。
3. `QuantMind-yj_exp`：学习 Factor Lab 如何把候选因子管成有 gate、有 memory、有 promotion 的研究工厂。
4. `llm-quant`：补研究治理和 robustness gate。
5. `QuantDesk`：补独立 RM 审查和实验工作台。
6. `AgentQuant`：抽取一个最小可实现 ReAct loop。
7. `joinquant-skill` / `finlab-ai` / `worldquant-skill`：学习 skill 化和 SOP。
8. `lumibot` / `OpenAlice` / `mmr`：等需要 paper/live 或 broker 边界时再读。

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
