# Quant Patterns Across The Repos

本文件按“量化怎么做”而不是按 repo 名归纳。

## 1. 因子挖掘：从表达式搜索到 R&D loop

代表：

- `RD-Agent`
- `QuantaAlpha`
- `QuantGPT`
- `QuantMind-yj_exp`
- `automated-quant-research`
- `Qlib-with-Claudex`

共同问题：

- LLM 可以提出因子，但提出不等于有效。
- 因子表达式容易重复、过度复杂、过拟合、未来函数或平台不可执行。
- 单轮回测结果容易误导，需要闭环迭代和严格记录。

主要思路：

1. **把因子变成结构化对象**
   - 因子不能只是字符串。
   - 需要 name、expression/code、data sources、lookback、universe、period、metrics、artifact path。
   - `QuantaAlpha` 的 factor library、`QuantGPT` 的 expression parser、`QuantMind-yj_exp` 的 FactorSpec/Factor ABI 都体现这一点。

2. **用 parser/regulator 限制搜索空间**
   - `QuantGPT` 自研 parser 区分截面和时序算子。
   - `QuantaAlpha` 用 AST 和 regulator 控制复杂度、重复子树、基础字段数量。
   - `joinquant-skill` 用 lint 防止平台 API 幻觉。

3. **让搜索带记忆和轨迹**
   - `QuantaAlpha` 用 trajectory pool。
   - `RD-Agent` 有 based experiments 和 SOTA factor combination。
   - `QuantGPT` 记录 rules/findings/failures。
   - `QuantMind-yj_exp` 把 experiment memory、failure memory、hypothesis memory、watchlist memory 和 lineage/report 都做成独立模块。
   - `FactorMiner` 把经验记忆拆成 formation / evolution / retrieval，显式记录成功模板和“禁区”（与库内高相关的因子族），并在相关过高但显著更强时替换旧因子。
   - `QuantEvolver` 走另一条路：把 novelty、family、残差互补写进 RFT 奖励，让记忆进入模型参数而不是 prompt。

   LLM 与搜索器分工（2026-09 补充）：
   - `AlphaEvo`：LLM 负责种子和结构变异，GP/EoH 按四个时间窗口的 IC 与 IC gap 决定存亡。
   - `autoresearch-trading`：LLM 只改离散结构，连续参数交给 BiteOpt 在 walk-forward fold 内优化。

4. **用演化而不是一次性 prompt**
   - mutation：窗口、算子、复杂度、方向调整。
   - crossover：组合高质量父代因子/策略片段。
   - explore：跳出当前类别，避免局部最优。
   - `QuantMind-yj_exp` 的 campaign 还增加 quota、worker claim、retry、event log、health/SLO，避免自动挖掘变成无边界任务。

适合复用的最小模式：

```text
factor idea
  -> schema validation
  -> expression parser
  -> data availability check
  -> IC / RankIC / group return
  -> redundancy check
  -> OOS / rolling check
  -> factor card + memory
  -> next mutation/crossover
```

## 2. 反过拟合与样本外纪律

代表：

- `llm-quant`
- `QuantGPT`
- `automated-quant-research`
- `quant-ashare`
- `AgentQuant`
- `QuantDesk`
- `QuantMind-yj_exp`

共同问题：

- 量化系统最容易把数据泄露、幸存者偏差、参数偶然性、牛市 beta 当成 alpha。
- agent 更容易 cherry-pick，因为它会被“好看的结果”奖励。

主要思路：

1. **显式 OOS 和 walk-forward**
   - `automated-quant-research` 使用 rolling windows。
   - `llm-quant` 使用 walk-forward validation 和 CPCV。
   - `quant-ashare` 明确记录 label horizon 泄露修正。

2. **多重统计门槛**
   - DSR、PBO/CPCV、permutation、subsample stability、deflated Sharpe。
   - `QuantGPT` 的 anti-overfit 包含 IC stability、subsample stress、placebo、half-life。

3. **Risk Manager / adversarial review**
   - `QuantDesk` 的 RM 默认 reject，检查 trade count、drawdown、sudden jump、repeat submission、cherry-picking。
   - 这类 review 应该读 run history 和 code diff，不只读最终指标。
   - `QuantMind-yj_exp` 的 promotion gate 明确只是进入人工 review，不等于生产启用；gate 读取 IC/RankIC、coverage、missing、turnover、feature correlation、hardened evaluation 和 portfolio backtest 摘要。

4. **失败也是资产**
   - `llm-quant` 记录被证伪的 mechanism families。
   - `QuantGPT` 维护 failures knowledge base。
   - `quant-ashare` 把泄露导致的漂亮数字作废写进 README。
   - `deepseek-harness-quant` 公开留档被证伪的假设；`Auto-Quant` 用 `versions/*/retrospective.md` 冻结每一版的失败教训。

5. **搜索强度折扣与证据隔离（2026-09 补充）**
   - honest evaluation 论文：所有评估进 trial ledger，DSR/PBO 按 ledger 折扣；泄漏必须在工具层结构性排除，因为泄漏 oracle 能通过统计检验。
   - `QuantMind-qm2`：locked holdout 前锁 shortlist 并冻结 failure memory，rolling blind 60 日盲窗 + BH，fresh forward 不回填；default-first 优化，全量搜索只做诊断。
   - `kph`：sealed holdout burn budget，内容寻址 replay。
   - `mmr`：`trader/simulation/` 的 selection-bias、lookahead check、PBO/DSR gauntlet，用 `tests/invariants` 固化研究算术。
   - `Auto-Quant`：单一可编辑文件 + 单一 oracle 会被 agent 钻空子（表面 Sharpe 1.44、真实约 0.19），多策略对照与独立 OOS 才能让 gaming 可见。

适合复用的门槛：

- 样本分割：train/valid/test 或 rolling windows。
- 因子稳定性：年度 IC、正 IC 占比、RankIC IR、IC decay。
- 策略稳定性：trade count、turnover、max drawdown、cost-adjusted return。
- 多重测试：DSR 或至少 permutation/bootstrap，试验数取自 trial ledger（`deflated-sharpe` 可直接嵌入）。
- 证据隔离：locked holdout 读取预算、blind 窗口、fresh forward 观察。
- 审计：每次参数变更、代码 diff、run artifact、结论都要保存。

## 3. Agent Research Loop

代表：

- `AgentQuant`
- `RD-Agent`
- `QuantDesk`
- `langalpha`
- `Vibe-Trading`
- `OpenAlice`

共同问题：

- 让 agent 研究不是让它“给建议”，而是让它执行可验证的循环。
- 循环需要状态、工具、记忆和停止条件。

几种模式：

### ReAct/backtest loop

代表：`AgentQuant`

```text
analyze market context
  -> hypothesize candidate strategies
  -> backtest tournament
  -> reflect against threshold
  -> retry or store
```

适合小系统，边界清晰。

### R&D loop

代表：`RD-Agent`

```text
scenario context
  -> hypothesis proposal
  -> implementation
  -> execution
  -> feedback
  -> next experiment
```

适合因子/模型研究。

### Analyst + Risk Manager loop

代表：`QuantDesk`

```text
analyst writes strategy
  -> run backtest
  -> inspect metrics
  -> request validation
  -> RM reads metrics + diff + trail
  -> approve/reject paper
```

适合策略工作台和 paper promotion。

### Workspace/Persistent research loop

代表：`langalpha`、`Vibe-Trading`、`OpenAlice`

```text
workspace goal
  -> load prior files/memory
  -> discover tools/skills
  -> execute code/tools
  -> write artifacts
  -> inbox/report
  -> future thread resumes
```

适合长周期基本面/多资产研究。

## 4. 数据层：从 DataFrame 到 MCP

代表：

- `data-mcp`
- `qlib-mcp`
- `quantcontext-mcp-server`
- `langalpha`
- `Vibe-Trading`
- `quant-ashare`

共同问题：

- agent 不能靠网页搜索做严肃量化。
- 金融数据有覆盖范围、时点可得性、复权、延迟、权限和体量问题。

主要思路：

1. **Typed tool schema**
   - `data-mcp` 用 TypeScript tool registry。
   - `quantcontext`/`qlib-mcp` 用 FastMCP + Python。
   - `quantconnect-mcp-server` 按平台 API 暴露完整生命周期。

2. **Provider fallback**
   - `Vibe-Trading` 明确 A/US/HK/crypto 的 fallback chain。
   - `langalpha` 分 hosted proxy、FMP、Yahoo 三层。
   - `quant-ashare` 聚焦 akshare、东财、Sina、Level2。

3. **大数据不进上下文**
   - `langalpha` 的 PTC 让 agent 写 Python 在 sandbox 里处理数据。
   - `quantcontext` 对 response 做 truncation，保留 contract 必需字段。

4. **PIT 和可观测性**
   - `finlab-ai` 特别强调 US fundamentals 要按 filing date。
   - `quant-ashare` 强调 label horizon 可观测性和 A 股停牌/涨跌停。

适合复用的原则：

- 每个数据点要有 provider、timestamp、market、adjustment、latency/provenance。
- 网络 fallback 不能静默改变语义；本地数据优先或显式指定。
- agent 看到的是 summary 和 artifact path，而不是未压缩大表。

## 5. Backtest-to-Live 边界

代表：

- `lumibot`
- `mmr`
- `OpenAlice`
- `QuantMind`
- `QuantMind-yj_exp`
- `QuantDinger`
- `kis-ai-extensions`
- `revolut-x-api`

共同问题：

- 回测系统和实盘系统不能完全割裂，否则策略迁移成本高。
- 但研究代码也不能直接共享实盘资金路径，否则风险太大。

几种设计：

1. **同策略代码，不同 broker**
   - `lumibot`：同一个 `Strategy` 用 backtesting broker 或 live broker。

2. **agent 只提 proposal**
   - `mmr`：agent 提 proposal，人类 approve，proposal state machine 记录。

3. **决策和执行进程隔离**
   - `OpenAlice`：Alice 负责 research/decision，UTA 负责 broker/execution。

4. **微服务隔离**
   - `QuantMind`：Engine/Trade/Stream 分服务。
   - `QuantMind-yj_exp`：Factor Lab 默认 no-execute；候选因子只能经 shadow feature、materialize、shadow training、approval、rollback 进入模型链路。
   - `QuantDinger`：market data、strategy/backtest、execution adapter 分层。

5. **hook 阻断 live 操作**
   - `kis-ai-extensions`：prod order guard、secret guard。

适合复用的最低安全线：

- paper/live 必须显式切换。
- 真实订单前必须有人类确认或明确授权策略。
- 订单、风控、成交、拒单和 reasoning 必须进入 audit log。
- agent 工具层要区分 read-only、state-changing、destructive。

## 6. Platform Skill 化

代表：

- `joinquant-skill`
- `finlab-ai`
- `worldquant-skill`
- `kis-ai-extensions`
- `revolut-x-api`

共同问题：

- 通用 LLM 会乱编平台 API。
- 平台规则太多，不能一次塞进 prompt。
- 真实交易/回测中，细节错误会产生严重误导。

主要思路：

1. **Progressive disclosure**
   - `joinquant-skill` 把 294KB API 拆成 14 类 reference。
   - 只有命中场景才加载对应文件。

2. **模板优先**
   - JoinQuant 的 5 个策略模板。
   - KIS 的 10 个 preset strategy。
   - CBT 的 strategy/backtest/config templates。

3. **lint/validation**
   - JoinQuant strategy_lint。
   - KIS YAML validate。
   - WorldQuant expression validate。

4. **SOP 和错误恢复**
   - WorldQuant Rule of 8 和 zombie backtest breaker。
   - KIS backtester job polling/retry。
   - FinLab 文档要求例子可运行。

适合复用的模式：

```text
user intent
  -> route to platform skill
  -> load minimal reference
  -> start from template
  -> validate/lint
  -> run or hand off to platform
  -> record result and caveats
```

## 7. 产品形态取舍

这些 repo 的产品路线可以归纳为四种：

1. **Library first**
   - `lumibot`、`qlib-mcp`。
   - 优点：可嵌入、边界清楚。
   - 缺点：需要用户自己搭工作流。

2. **Workspace first**
   - `langalpha`、`OpenAlice`、`Vibe-Trading`、`QuantDesk`。
   - 优点：适合 agent 长任务和可审计研究。
   - 缺点：基础设施重。

3. **Platform/plugin first**
   - `joinquant-skill`、`finlab-ai`、`kis-ai-extensions`、`revolut-x-api`、`quantconnect-mcp-server`。
   - 优点：落地快，减少 API 幻觉。
   - 缺点：平台迁移性弱。

4. **Research governance first**
   - `llm-quant`、`quant-ashare`。
   - 优点：防过拟合和失败记录强。
   - 缺点：不一定有通用产品界面。

如果要构建自己的量化研究 OS，比较稳的组合是：

```text
Qlib-like research backend
  + QuantaAlpha/RD-Agent style factor loop
  + llm-quant style robustness gate
  + QuantDesk style RM review
  + langalpha/Vibe style workspace artifacts
  + JoinQuant/FinLab style skill routing
  + lumibot/mmr/OpenAlice style execution boundary
```
