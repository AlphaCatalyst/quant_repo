# Roadmap: Low-Frequency Quant Research OS

## 1. 定位

目标不是做一个“AI 自动交易机器人”，而是做一个低频量化研究 OS：

```text
research question
  -> candidate factor / signal
  -> evidence-backed evaluation
  -> reviewed research asset
  -> shadow feature / model / paper candidate
```

系统的第一性目标是持续产生、验证、沉淀 alpha 假设。交易执行只是下游，不应该成为第一阶段的主线。

## 2. 非目标

第一阶段不做：

- 自动实盘下单。
- 让 agent 绕过 review 直接发布交易信号。
- 为了覆盖所有资产类别而牺牲低频股票研究口径。
- 重型多用户 SaaS 平台。
- 只有聊天界面、没有 artifact 和 registry 的 research assistant。

第一阶段也不应该追求“模型越复杂越好”。如果 data truth、factor contract、evaluation gate 没有打牢，复杂模型只会放大误判。

## 3. 核心原则

### 3.1 Kernel 和 OS 分离

Kernel 是：

- 因子生成。
- 特征计算。
- label 构造。
- IC/RankIC/分组收益。
- 模型训练和 portfolio test。

OS 是：

- 数据口径。
- 实验状态机。
- artifact 管理。
- 反过拟合门禁。
- 研究记忆。
- 人工 review。
- shadow/paper/live 边界。

Kernel 可以替换，OS 不能随意漂移。

### 3.2 所有研究产物都要可追溯

每个 factor、model、strategy、report 都必须知道：

- 它来自哪个 research question。
- 使用哪些数据源和时间口径。
- 由哪个代码版本生成。
- 通过了哪些 gate。
- 被谁 review。
- 为什么被接受、拒绝、修复或回滚。

### 3.3 先 shadow，再 promotion

研究结果不能直接进入生产信号。

合理路径是：

```text
candidate
  -> evaluated
  -> reviewable
  -> shadow_feature
  -> materialized_feature_snapshot
  -> shadow_training
  -> baseline_comparison
  -> approval
  -> paper/watchlist
```

### 3.4 失败是一等资产

失败、作废、重复、泄露、过拟合、数据缺陷都必须入库。否则 agent 会反复生成相同错误。

### 3.5 按 verifier 分层让 agent loop

量化研究容易打分、难以验证。agent 只在便宜的验证层（结构约束、开发窗口、样本内稳健）自由循环；搜索折扣、锁定留出、前瞻验证按预算消耗，且对 agent 不可见。agent 只提案，确定性后端裁决，人批准预算和资金。详见 [agent-loop-verification.md](agent-loop-verification.md)。

## 4. 阶段路线

### Phase 0: Research Ledger

目标：先把研究记录变成结构化资产。

交付：

- `ResearchQuestion` schema。
- `FactorSpec` schema。
- `ExperimentRun` registry。
- `TrialLedgerEntry` ledger：所有评估经唯一入口 append-only 入账。
- 数据区间划分：development / sealed holdout，holdout 从第一天起封存。
- factor card / experiment card 模板。
- 失败原因 taxonomy。

不做：

- 自动挖掘。
- 复杂模型训练。
- paper/live。

验收：

- 任意一次人工因子研究可以完整落成 card。
- 失败实验不会丢失。
- 同一个 factor id 不会被静默覆盖。
- 任意时刻都能回答“这个研究问题下一共评估过多少个候选”。

### Phase 1: Data Truth + Fast Evaluation

目标：建立可信的低频数据口径和便宜的初筛。

交付：

- 数据源 provenance。
- universe membership 口径。
- 财报/事件 lag 规则。
- 停牌、涨跌停、复权、缺失值处理 contract。
- IC、RankIC、分组收益、覆盖率、turnover proxy、相关性去重。

验收：

- 每个 factor evaluation 都能说明数据源、样本区间、universe、label horizon。
- 任何缺数据、未来函数风险、全样本标准化风险都会阻断或 warning。

### Phase 2: Robust Evaluation Gate

目标：防止把偶然性当成 alpha。

交付：

- rolling / walk-forward。
- train/valid/test split。
- 分年份、分行业、分市值、分市场状态表现。
- neutralized IC。
- cost-adjusted long-short。
- correlation with existing factor pool。
- permutation / bootstrap / DSR 的最小实现，试验数取自 trial ledger。
- default-first 参数治理：默认参数过门即冻结，失败只允许有限邻域救援，全量搜索只做诊断。
- shortlist 锁定 + locked holdout 评估：打开前冻结 failure memory，读取计入预算，违规标记污染。

验收：

- 单次 Sharpe 或单年 IC 不能直接 promotion。
- 每个 reviewable factor 都有 robustness summary 和 holdout 结果。
- holdout 指标不出现在 agent 可读的任何上下文中。

### Phase 3: Factor Lab

目标：让 AI 参与候选生成和修复，但不能越过 gate。

交付：

- factor parser / regulator。
- mutation/crossover candidate generator。
- candidate budget 和重复度检查。
- no-execute 默认边界。
- sandboxed factor execution。
- memory-aware prompt context。
- ratchet 主循环：不可变 evaluator + 可编辑候选 + keep/discard，只运行在开发窗口。
- 经验记忆：成功模板、禁区（与因子库高相关的因子族）、策略洞察。

验收：

- AI 只能提交 candidate，不自动 promotion。
- evaluator 不在 agent 可写范围内；agent 无法读取 holdout / fresh 数据。
- 每个候选都经过 schema、data availability、leakage、complexity、duplicate check。
- 失败会进入 negative memory。

### Phase 4: Model and Portfolio Promotion

目标：评估新因子是否对模型和组合有边际价值。

交付：

- baseline model，按固定配置随因子库定期滚动重训。
- 参考模型组（ridge、默认 GBDT、排序模型）上的边际贡献对比。
- baseline + candidate factor 对比。
- feature snapshot materialization。
- shadow training。
- top-k / long-short / risk-adjusted portfolio test。
- promotion review packet。

验收：

- 新因子必须证明 marginal value，而不是只证明单因子好看。
- 模型设计改动（目标、损失、集成、模型族内配置）与因子改动交替进行、共用 trial ledger，晋升单位是（因子集版本，模型配置版本），见 [agent-loop-verification.md](agent-loop-verification.md) §3.3。
- promotion 只进入 shadow，不进入 live。

### Phase 5: Paper Boundary

目标：把少数通过 review 的研究产物放入 paper/watchlist。

交付：

- paper portfolio。
- fresh forward 观察：不回填、≥60 交易日、cohort 级统计；按决策来源（机器 / 人 / AI）分池追踪多个 horizon。
- signal monitor。
- drift / decay monitor。
- rollback / retire。
- approval audit。

验收：

- paper 和 live 显式隔离。
- 所有 signal 都能追溯到 factor/model/experiment。
- 任何 live 接入都必须另设权限和人工确认。

## 5. 优先级判断

优先做：

- schema。
- registry。
- evaluation contract。
- gate。
- report/card。

后做：

- 自动挖掘。
- UI。
- 多 agent 编排。
- paper/live。

暂缓：

- 多租户权限。
- 复杂交易执行。
- broker adapter。
- 高频或分钟级数据。
- RFT 训练矿工模型：等 ledger 积累足够多经过验证的正负样本后再做，且 holdout 永不进入奖励。
- 月频择时、单标的策略等独立观测量太少、verifier 统计功效低的问题。

## 6. 最小可用版本

MVP 应该能完成：

```text
write research question
  -> create factor spec
  -> run fast evaluation
  -> run robustness checks
  -> produce factor card
  -> mark accepted/rejected/needs_repair
  -> store result and memory
```

这个版本即使没有自动因子挖掘，也已经有研究 OS 的骨架。
