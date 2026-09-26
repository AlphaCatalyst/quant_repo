# Agent Loop 与分层 Verifier

本文回答一个问题：基于 `analysis/` 的调研，让 agent 自主 loop 做量化研究，合理的路线是什么。

结论先行：量化研究**容易打分，但难以验证**。能让 agent 持续 loop 的关键不是 agent 能力，而是把验证做成分层、有预算、agent 无法钻空子的结构。agent 可以在便宜的验证层无限循环；越可信的验证越稀缺，必须按预算消耗。

## 1. 前提修正：为什么不是“容易 verify”的任务

和代码、数学这类可验证任务相比，量化的 verifier 有三个根本缺陷。

| 缺陷 | 表现 | 调研中的证据 |
|---|---|---|
| 信噪比极低 | 好因子 IC 只有 0.03–0.05，回测指标本身是高方差随机变量 | — |
| verifier 会被优化穿透 | 搜索越多，最好结果越可能是噪声；agent 会找到 evaluator 的漏洞 | `Auto-Quant` v0.1 表面 Sharpe 1.44、真实约 0.19；honest evaluation 论文中泄漏 oracle（Sharpe 35）通过 DSR/PBO，所有 LLM 策略被否决；`QuantMind-qm2` 多轮 batch 无 survivor |
| 唯一可信的 verifier 是未来数据 | 历史数据会被反复查看，也被 LLM 预训练见过；只有上线后的新数据不受污染，但它慢且稀缺 | `QuantMind-qm2` fresh forward、`deepseek-harness-quant` 五池远期验证 |

因此瓶颈不是候选产出速度，而是**可信验证的带宽**。整条路线围绕“节省并保护可信验证”来设计。

## 2. 分层 Verifier

越往下越慢、越可信、越稀缺。agent 的自由度随层级下降。

```text
L0 结构约束   类型化 DSL / 注册工具 / PIT 数据，前视在工具层不可表达       即时     agent 无限次
L1 开发窗口   IC / RankIC / 与因子库去重                                  秒级     agent 无限次（主 loop）
L2 样本内稳健 多窗口一致、walk-forward、成本、中性化、default-first 参数     分钟级   自动
L3 搜索折扣   DSR / BH-FDR（试验数取自 trial ledger），锁定 shortlist       批次级   自动
L4 锁定留出   sealed holdout，按批次开启，读取计入预算                      稀缺     预算控制
L5 前瞻验证   fresh forward / shadow 分池追踪，≥60 交易日                  很慢     人决定是否上资金
```

### L0 结构约束

- 因子只能用白名单 DSL 或注册工具表达，未来数据、全样本标准化、非 PIT 基本面在构造上写不出来。
- 这一层不能被统计检验替代：泄漏 oracle 可以通过 DSR 与 PBO。
- 参考：honest evaluation 的 registry-validated tools；`AlphaEvo` 与 `QuantEvolver` 的 DSL 白名单；`kph` 的 PIT finance layer。

### L1 开发窗口（agent 主循环）

- 固定、不可变的 evaluator 计算 IC/RankIC/ICIR、覆盖率、与现有因子库的相关性。
- 使用 ratchet：不可变 evaluator + 可编辑工件 + keep/discard（参考 `Auto-Quant`、`autoresearch-trading`）。
- 经验记忆记录成功模板与禁区，避免反复挖同一族因子（参考 `FactorMiner`）。
- 只使用 development 窗口；holdout 与 fresh 数据在这一层物理不可见。

### L2 样本内稳健

- 多个不重叠时间窗口的 IC 与 IC gap（参考 `AlphaEvo`）。
- walk-forward、成本调整多空、行业/市值中性化后 IC 不塌。
- default-first 参数治理：默认参数过门即冻结；失败只允许单参数邻域救援（有限次数）；全量搜索只做诊断，不能用于选参或晋升（参考 `QuantMind-qm2` `FACTOR_OPTIMIZATION_POLICY_V2`）。
- 结构与参数分离：agent 只改离散结构，连续参数交给确定性优化器（参考 `autoresearch-trading`）。

### L3 搜索折扣

- 所有评估经唯一入口写入 trial ledger（见 `system-contracts.md` 的 `TrialLedgerEntry`），ledger 在构造上完整。
- DSR / PBO / BH-FDR 的试验数与方差只取自 ledger，agent 搜得越多门槛越高（参考 `deflated-sharpe`、`mmr` `trader/simulation/`）。
- 通过者进入 shortlist，shortlist 在打开 holdout 前锁定。

### L4 锁定留出

- 预留一段 sealed holdout（例如最近 2 年），按批次开启。
- 打开前冻结 failure memory，防止 holdout 信息经记忆回流到下一轮搜索。
- 每次读取计入 burn budget，超额或违规读取即标记污染，该批证据作废（参考 `QuantMind-qm2` locked holdout、`kph` burn budget guard）。
- holdout 指标对 agent 不可见，只进入 review packet。

### L5 前瞻验证

- 通过 holdout 的因子进入 fresh forward / shadow 观察：不回填，至少 60 个交易日，cohort 级统计（HAC / BH）。
- 按决策来源分池追踪多个 horizon（机器 / 人 / AI；T+1/5/20/60），把“谁的判断更可靠”也变成可验证对象（参考 `deepseek-harness-quant` `pitch_track.py`）。
- `fresh_supported` 本身不授予资金，是否上 paper/live 由人决定。

## 3. 角色与边界

| 角色 | 负责 | 不能做 |
|---|---|---|
| Agent | 提出假设、写因子实现、诊断失败、写报告初稿 | 修改 evaluator、读取 holdout/fresh 指标、写 registry、扩大预算、判定是否通过 |
| 确定性后端 | 计算全部指标、写 ledger、执行 gate、管理状态机与预算 | 决定下一步研究方向 |
| 人 | 定研究方向与数据契约、批准 holdout 预算、批准资金 | 在 gate 之外手工改结果 |

对应的工程形态：

- Decision / Control / Execution 分离：agent 只产出结构化的 `ResearchDecision`，orchestrator 管预算与状态机，执行层永不回调 agent（参考 `QuantMind-qm2`）。
- harness 不重算：agent 侧工具只透传 JSON CLI，计算与 gate 判定只在后端；只读工具直通，写操作 fail-closed 审批（参考 `kph`）。
- evaluator 放在 agent 可写范围之外，版本变更走人工 review。

### 3.1 与“按环节分 agent”路线的区别

常见做法是按流水线环节切 agent：idea agent 提假设、factor agent 写因子、model agent 训模型、eval agent 跑回测并给反馈（`RD-Agent`、`AlphaAgent` 早期形态都属于这一类）。本路线不按环节切，而是按**裁决权**和**验证预算**切：

| 环节 | 按环节分 agent | 本路线 |
|---|---|---|
| 挖因子 | factor agent 生成并迭代 | agent 的主循环：提假设、写表达式、诊断失败；能否入库由 evaluator 与 gate 决定 |
| 训模型 | model agent 调结构和超参 | 固定配置模型（如固定参数 LightGBM），用来衡量新因子的边际贡献，是 evaluator 的一部分，不是 agent 的优化对象 |
| 跑回测 | eval agent 执行并解读 | 回测就是 verifier；agent 只能经 CLI 触发，只能读开发窗口结果，回测代码不在 agent 可写范围内 |

按环节切的核心问题是：eval agent 本质上是 agent 在给 agent 打分，裁决权落在了会被“好看结果”奖励的一方，这正是过拟合和结论膨胀最常出现的地方。本路线中，训模型与跑回测属于 verifier，不交给 agent；模型固定配置也参考了 `QuantMind-qm2` 的 fixed configuration model program（不调超参、不按盲测结果选特征）。

### 3.2 agent 的工作范围与自主 loop 对象的扩展

agent 不只做挖因子。区分两件事：agent 能做什么，和 agent 能在什么对象上**自主循环**（结果直接进入 ledger 和 gate，不需要人逐次审）。

| 工作 | agent 的角色 | 裁决方 | 是否在自主 loop 中 |
|---|---|---|---|
| 研究规划：从研报/论文提炼假设、拆研究问题 | 起草 | 人确认研究方向 | 否 |
| 因子候选：表达式、实现、修复 | 主力 | evaluator + gate | 是（第一阶段唯一的 loop 对象） |
| 失败诊断与记忆整理 | 主力 | memory schema 约束 | 是（只写 development 层记忆） |
| evaluator / 回测 / 成本模型的工程开发 | 写代码 | 人 review + 版本化 | 否，属于工程变更 |
| 对抗式审查（读 run history、code diff 挑毛病） | Reviewer agent | 仅作参考，不构成 gate | 否 |
| 报告与 factor card 初稿 | 起草 | 人审阅结论 | 否 |
| 数据源与字段建议 | 建议 | 人确认数据契约 | 否 |

自主 loop 的对象按阶段扩展，每一层都要先有自己的 verifier、ledger 和 default-first 规则：

```text
阶段 1  因子候选                     verifier：IC/RankIC、去重、多窗口；固定下游模型
阶段 2  因子组合 / 特征集             verifier：固定模型下的边际贡献、正交性
阶段 3  模型配置（限定模型族）         verifier：同窗口 baseline 对比；默认配置过门即冻结
阶段 4  组合构建规则（调仓、约束）     verifier：成本后组合回测、换手与容量
```

先只让因子循环，是因为因子同时满足三个条件：搜索空间可以用 DSL 圈住；验证便宜且统计功效高（截面几千只股票）；被证伪的成本低。越往后的对象搜索维度越大、验证越贵，过早放开会迅速耗光 holdout 预算。扩展到阶段 3 时，可以参考 `RD-Agent(Q)` 交替优化因子与模型的做法，但每一次模型改动同样计入 trial ledger。

## 4. 三层节奏

| 循环 | 周期 | 内容 | 驱动者 |
|---|---|---|---|
| 内环 | 分钟级 | agent 在 L0–L2 连续迭代候选 | agent |
| 中环 | 天级 | 一个 batch 收敛出 shortlist → L3 → 锁定 → 开一次 L4 | orchestrator + 人批预算 |
| 外环 | 周到月级 | 通过者进入 L5 观察，决定 paper / 退役 | 人 |

## 5. 两个比 agent 能力更关键的选择

### 5.1 优化目标

loop 的目标不是“找到 Sharpe 最高的策略”，而是：

> 每单位 holdout 预算产出的、通过验证且与现有因子库正交的因子数。

具体做法：

- 奖励与因子库的低相关（应对相关性红海，参考 `FactorMiner`）。
- 惩罚搜索次数：ledger 中的试验数直接抬高 L3 门槛。
- 把“被证伪”记为有效产出，写入 failure memory。

### 5.2 选 verifier 统计功效高的问题

主动管理基本定律 \(IR \approx IC \times \sqrt{breadth}\)：同样的时间跨度下，独立下注次数越多，验证越可信。

| 问题类型 | 每年独立观测量级 | 是否适合 agent loop |
|---|---|---|
| A 股截面日频/周频因子（几千只股票） | 数十万 | 适合，首选 |
| 行业/风格轮动 | 数百 | 谨慎，需更长样本 |
| 月频宏观择时、单标的 CTA | 十几次 | 不适合，几乎无法验证 |

这是 AlphaQuant 先做低频截面因子、而不是择时策略的根本原因。

## 6. 主要风险与对策

| 风险 | 对策 |
|---|---|
| LLM 预训练知识本身是泄漏源（见过历史行情与已发表因子） | blind prompt 隐去 ticker 与日期（参考 `ai-hedge-fund`）；只有 L5 能彻底免疫 |
| 记忆泄漏：holdout 信息经 failure memory 回流 | holdout 打开前冻结记忆；holdout 结果只进 review packet |
| agent 钻 evaluator 空子 | evaluator 不可变且不可写；多候选对照使 gaming 可见；独立 OOS（参考 `Auto-Quant`） |
| ledger 不完整导致 DSR 失真 | 唯一评估入口，ledger 由入口自动写入，不接受人工补录 |
| 过早做 RFT：在噪声奖励上学会过拟合 | 奖励只来自 L1/L2，holdout 永不进入训练信号；ledger 积累足够验证样本后再做（参考 `QuantEvolver`） |
| 期望错配 | 主要产出是被证伪的假设，这说明 verifier 在正常工作 |

## 7. 与 Roadmap 和状态机的对齐

| Verifier 层 | Roadmap 阶段 | 因子状态（`system-contracts.md`） |
|---|---|---|
| L0 | Phase 1 | `validating` → `validated` / `validation_failed` |
| L1 | Phase 1 + Phase 3（agent 主循环） | `evaluating` → `evaluated` / `evaluation_failed` |
| L2 | Phase 2 | `robust_evaluating` → `robust_failed` |
| L3 | Phase 2 | `ledger_gated` → `shortlist_locked` |
| L4 | Phase 2 | `holdout_evaluating` → `holdout_passed` / `holdout_failed` / `holdout_contaminated` → `reviewable` |
| L5 | Phase 4–5 | `shadow_trained` → `fresh_observing` → `fresh_supported` / `fresh_failed` →（人工）`approved_for_paper` |

trial ledger 与 sealed holdout 的划分属于 Phase 0：它们必须在第一次评估之前就存在，事后无法补回（已经无法知道看过多少次数据）。

## 8. 落地顺序

1. **L0 + L1**：固定的 A 股日频 evaluator + ratchet 主循环；同时建 trial ledger，封存 holdout 区间。
2. **L2 + L3**：多窗口检验、default-first 参数治理、按 ledger 折扣的 DSR / BH-FDR。
3. **L4 + L5**：holdout 预算与污染标记、fresh 分池追踪、人工 promotion。
4. **扩展**：按 §3.2 的顺序逐步放开自主 loop 对象（因子组合 → 模型配置 → 组合构建规则）；ledger 中积累足够多经过验证的正负样本后，再考虑 RFT 矿工与多 agent 分工（Lead / Reviewer / Miner）。
