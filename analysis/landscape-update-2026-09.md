# Landscape Update 2026-09

本文件记录 2026-09-26 这一轮开源生态刷新：同步了哪些仓库、新增了什么、刻意没收录什么，以及这些变化对 [agent-quant-os-blueprint.md](agent-quant-os-blueprint.md) 的影响。逐仓细节见 [repo-inventory.md](repo-inventory.md)。

## 1. 同步结果

- 全部既有仓库 `git pull --ff-only` 到上游最新，25 个有新 commit。变化最大的是 `OpenAlice`、`langalpha`、`lumibot`、`mmr`、`QuantDinger`、`AgentQuant`、`Vibe-Trading`、`QuantMind`、`quant-ashare`。
- `Vibe-Trading`、`QuantMind` 原为 depth=1 浅 clone，无本地改动，已直接重置到上游最新（仍为浅 clone）。
- `QuantMind-yj_exp` 是开发仓库 `/data/codebase/stock/QuantMind` 的 worktree，含 15 个本地未推送的 Factor Lab commit，而上游 `origin/yj_exp` 已走向 qm2 路线且不含 `factor_lab/`。为保留本地工作，该目录不跟随上游；上游最新以独立 clone `agent-quant-os/QuantMind-qm2` 形式收录。
- `QuantaAlpha/QuantaAlpha-claw` 在 README 中已宣布，但仓库需要认证、尚未公开，未收录。

## 2. 新增仓库

| 分类目录 | 仓库 | 收录理由 |
|---|---|---|
| `factor-mining/` | `FactorMiner` | 经验记忆（成功模板 + 禁区）+ 四级验证级联，failure memory 最干净的实现 |
| `factor-mining/` | `AlphaEvo` | LLM 只塑造搜索空间，GP/EoH + 四窗口 IC 决定存亡 |
| `factor-mining/` | `QuantEvolver` | 评估器输出转成 RFT/GRPO 奖励，走参数更新路线 |
| `factor-mining/` | `AlphaAgent` | 已重写为 A 股 FactorZoo + 交付门，研究对象层参考价值高 |
| `factor-evaluation/` | `deflated-sharpe` | 可嵌入的 DSR / BH-FDR / 衰减监控 gate |
| `agent-quant-os/` | `QuantMind-qm2` | locked holdout、rolling blind、fresh 验证、default-first 优化治理 |
| `agent-quant-os/` | `deepseek-harness-quant` | A 股低频三层架构、Pitch 人工审批、五池远期验证 |
| `agent-quant-os/` | `kph` | harness 只调用不重算的契约与插件实现 |
| `agent-quant-os/` | `EvoQuant` | 研报 → 文献 → 构思 → 实验 runtime，skills 可外挂 |
| `agent-quant-os/` | `TradingAgents` | 多 agent 决策的事实参考；PIT、SEC as-filed、decision log |
| `agent-quant-os/` | `ai-hedge-fund` | Fund/mandate 一等对象、同一 `run_cycle`、blind backtest |
| `research-harness/`（新分类） | `Auto-Quant` | autoresearch 范式 + oracle-gaming 实证教训 |
| `research-harness/`（新分类） | `autoresearch-trading` | LLM 管结构、优化器管参数的搜索空间治理 |

另外补录了此前遗漏的 `AlphaBench`（Assay point-in-time 回测后端）。

## 3. 刻意未收录

- `Dexter`、`FinRobot`、`ValueCell`、`ContestTrade`：偏基本面投研、事件驱动选股或产品化平台，与低频因子研究 OS 同构度低。
- `dsh-quant`、`everything-claude-trading`、`quant-mcp`、`money-agent`：工具/技能数量堆叠为主，正是蓝图 §6.1 “以 MCP 数量为中心”的反例。
- `7-MASFactorMiner`：教学性质。

## 4. 关键外部证据：honest evaluation

[What survives honest evaluation?](https://arxiv.org/abs/2608.27734)（2026-08，代码与实验清单在 Zenodo，不在 GitHub）对“LLM 发现策略”做了泄漏安全、搜索感知的重新评估：

- agent 只能通过注册过的类型化工具组合策略，特征空间在构造上排除前视。这层不能被统计校正替代：故意植入的泄漏 oracle 跑出 Sharpe 35，完整通过了 DSR 和 PBO。
- 系统把每一次评估写入 trial ledger，DSR 的试验数 N 和 Sharpe 方差 V 直接来自 ledger，而不是作者估计。agent 搜得越多，门槛升得越快。
- 在 453 只 PIT 美股和 39 只 ETF 上，被动基准通过，所有 LLM 发现的策略（两个前沿模型、最多 100 个候选、5 次重复）都被否决。

对蓝图的含义：evidence gate 至少需要两层，一层是数据/工具层的结构性泄漏隔离，另一层是基于完整 trial ledger 的搜索强度折扣。`QuantMind-qm2` 的搜索暴露账本和 `kph` 的 holdout burn budget 是这一思路在工程上的两种实现。

## 5. 勘误：网页描述与代码不符之处

- `AlphaEvo`：README 称 “LLM never sees returns”，但 `alphaevo/llm_utils.py` 的 `compose_window_summary` 会把 5/10/20 日收益摘要注入 prompt。准确说法是 LLM 不直接优化未来收益标签。
- `AlphaAgent`：当前代码中没有 KDD 2025 论文的 AST 原创性、假设-因子对齐、复杂度控制实现；仓库已改为 Tushare + DSL + FactorZoo 框架。
- `QuantEvolver`：论文的 DiCo reward 在代码中没有同名实现，对应 `diversity_reward` + family/残差互补 shaping。
- `deepseek-harness-quant`：README 称 123+ 因子，`factors/factor_engine.py` 的 `FACTOR_FUNCS` 只注册 6 个；九步链引用的 `core.combo_backtest` 不在仓库中。
- `quant-ashare`：执行、组合优化、公司行为等模块已被删除，旧版 inventory 描述的 Level2/冲击成本能力大部分不在当前代码中。

## 6. 趋势判断

1. 新进展集中在评估诚实性、带记忆的挖掘器、code-agent 原生 harness 三块，没有出现能整体取代 `QuantMind-yj_exp` / `QuantDesk` 的完整研究 OS。
2. 越成熟的系统越强调“agent 只提案”：`QuantMind-qm2` 的 Decision/Control/Execution 分离、`kph` 的不重算契约、`deepseek-harness-quant` 的写死引擎，都在印证蓝图 §6.2。
3. 诚实评估下，自主发现可交易 alpha 仍未被证明：`QuantMind-qm2` 多轮 batch 无 survivor，honest evaluation 论文否决全部 LLM 策略。系统价值应落在证据治理，而不是承诺产出。
4. 另一条值得跟踪的路线是 RFT：`QuantEvolver` 把评估器变成奖励信号，适合有 agentic RL 训练资源的团队。
