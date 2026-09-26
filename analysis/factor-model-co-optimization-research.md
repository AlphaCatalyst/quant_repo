# Factor–Model Co-Optimization: Research Survey (2026-09)

本文整理“因子与预测模型如何联合优化”的最新研究与实践经验，来源包括开源项目、arXiv 预印本和期刊/会议论文。设计结论落在 [../design/agent-loop-verification.md](../design/agent-loop-verification.md) §3.3。

说明：多数数字来自论文摘要或正文中的主表，没有在本地复现；带 arXiv 编号但未注明会议的均为预印本。

## 1. 问题为什么难

- 因子的价值依赖模型：同一因子在线性模型里无效、在树模型里可能有效；边际贡献还依赖库里已有因子。
- 模型的最优配置依赖因子集：因子集一变，原最优配置可能失效。
- 联合搜索让试验数成倍增长，多重检验门槛随之抬高。
- 在同一段数据上既选因子又调模型，会叠加选择偏差。下文第 5 节显示，这一点在已发表的 agent 系统中确实发生过。

## 2. Agent 框架：怎么安排“先动因子还是先动模型”

| 工作 | 来源 | 联合优化机制 | 要点 |
|---|---|---|---|
| R&D-Agent(Q) | NeurIPS 2025（Datasets & Benchmarks），arXiv 2505.15155，开源于 `microsoft/RD-Agent` | 每轮只选一个方向：因子精炼或模型优化；用两臂 contextual 线性 Thompson sampling 调度，context 是 8 维绩效向量；限制同一方向连续探索的最大轮数 | CSI300（测试期 2017–2020）上联合优化配置报告 IC 0.0532、ARR 14.21%、IR 1.74；消融中 bandit 调度优于随机和 LLM 调度 |
| AutoScientist-Quant | arXiv 2608.28632 | 把因子发现、因子库子集选择、模型搜索（Linear / LightGBM / XGBoost / CatBoost 及其超参）统一成一个带全局预算的树搜索；控制器每轮在 IMPROVE / COMBINE / PIVOT / STOP 中选择，并决定生成多少候选 | 反馈窗口与留出测试窗口在设计上不重叠，留出窗口只使用一次；修复了前人评估代码中的两处前视问题（见第 5 节） |
| AlphaCrafter | arXiv 2605.05580 | Miner 持续扩充因子池；Screener 按市场状态从因子库组装加权集成，不重训模型；Trader 在参考策略上做超参搜索并执行 | 把“组合”从静态模型改成按 regime 重配；但 Trader 按回测目标选配置，存在选择偏差风险 |
| FactorEngine | arXiv 2603.16365 | 因子写成程序；宏观层 LLM 改逻辑，微观层贝叶斯优化调参数；精选因子并入 Alpha158 训练 LightGBM，以组合级回测结果反馈 | 结构与参数分离；对比实验中为 AlphaAgent、RD-Agent(Q) 重新切分挖掘窗口以避免泄漏 |

共同趋势：

- 不同时搜索两边，而是交替或分阶段，由调度器决定方向。
- 调度器越来越显式地感知预算，并且包含 STOP。
- 结构由 LLM 决定、连续参数交给确定性优化器（FactorEngine 的贝叶斯优化，与仓库中 `autoresearch-trading` 的思路一致）。

## 3. 非 agent 的“挖掘 + 组合”联合方法

| 工作 | 来源 | 机制 | 对设计的启示 |
|---|---|---|---|
| AlphaGen | KDD 2023，代码见 GitHub `AlexrandAI/alphagen`（README 标注为论文代码） | RL 生成因子，奖励是加入该因子后因子池线性组合的 IC，即按对组合模型的边际贡献评价因子 | 因子评估应以“对下游组合的边际贡献”为准，而不是单因子 IC |
| AlphaForge | AAAI 2025，arXiv 2406.18394 | 两阶段：生成-预测网络挖掘多样因子；组合模型每天按近期 IC/ICIR 重选 top-N 并重新拟合线性权重 | 组合层可以是确定性的动态重选与重加权，不需要 agent 参与 |
| FactorMiner | arXiv 2602.14670，仓库 `factor-mining/FactorMiner` | 选择层对比 Lasso、stepwise、XGBoost；与 PatchTST、Chronos-2、LightGBM 端到端预测对比 | 报告显示公式因子库在 IC/ICIR 上优于这些端到端基线（CSI500 上 LightGBM 5.53%/0.51，FactorMiner 8.25%/0.77），并在 2026Q1 仍有效 |

## 4. 模型侧证据：该优化什么

### 4.1 预测目标与损失函数比模型结构更关键

- Cakici & Zaremba, *Getting the Target Right in Return Prediction*（SSRN 6615698，2026）：35 个市场、1994–2024。把目标从原始收益改为标准化或排名收益，预测准确度提高近 3 倍、组合收益翻倍；特征变换是次要因素。排名目标在收益离散度或偏度高时（尤其微盘股）表现较差，最优变换随市场和时间变化。
- LambdaRankIC（arXiv 2605.00501）：推导 Rank IC 的 lambda 梯度，作为 XGBoost 自定义目标直接优化 Rank IC；在低信噪比、重尾噪声下优于回归损失和 NDCG 类排序目标。NDCG 偏重头部，与截面全排序的需求不一致。

### 4.2 “复杂度红利”存在争议

- Kelly, Malamud & Zhou, *The Virtue of Complexity in Return Prediction*（Journal of Finance, 2024）：理论与实证主张在适当收缩下，参数多于样本的模型样本外表现更好。
- Nagel, *Seemingly Virtuous Complexity in Return Prediction*（NBER w34104, 2025）：短训练窗口下 RFF 预测退化为近期样本收益的加权平均，本质是波动率择时的动量策略；换成反转数据时同样的方法表现很差。
- Buncic（SSRN 5239006, 2025）：结论依赖零截距约束和特定的聚合方式；加截距后，带温和 ridge 收缩的 15 变量线性模型显著优于复杂模型。
- arXiv 2608.23761：应以有效自由度衡量复杂度，强收缩下的大模型实际上接近只有截距的模型。

启示：不要把“换更复杂的模型”作为 agent 在模型侧的主要动作；复杂度收益很容易来自数据特性而非真实的学习能力。

### 4.3 时间序列基础模型尚未超过工程化的 GBDT

- Kronos（AAAI 2026）：在 45 个交易所、120 亿条 K 线上预训练，零样本价格预测 RankIC 显著优于其他 TSFM，但论文没有和工程化 LightGBM + 因子做截面 alpha 对比。
- *Revisiting Time Series Models in Finance*（arXiv 2511.18578）：零样本 Chronos/TimesFM 表现差，微调也难以弥补经济意义上的差距；用金融数据预训练后差距缩小，但整体仍落后于 CatBoost 等集成模型。
- arXiv 2606.27100：TSFM 在排名上占优，但相对随机游走的改进小且稀疏，只有少数任务通过 Diebold–Mariano 检验。

### 4.4 让模型跟上最新数据：滚动重训与增量学习

- DDG-DA（AAAI 2022，已并入 Qlib `examples/benchmarks_dynamic/DDG-DA`）：预测下一期数据分布，据此重加权历史样本后再滚动重训。
- DoubleAdapt（KDD 2023，开源 `SJTU-DMTai/DoubleAdapt`）：数据适配器 + 模型适配器的元学习增量学习。官方建议每月从头重训，月内每 2–3 个交易日做一次增量更新；并提醒其特征适配层在 Alpha158 这类几百维因子上会过参数化。
- arXiv 2401.03865：在增量学习中同时利用最新数据与历史数据，兼顾可预测和不可预测的漂移。

启示：“模型随最新因子更新”主要是确定性的重训与增量学习问题，可以作为流水线组件，而不是 agent 的搜索对象。

### 4.5 部署期的 regime 失效

- *When Alpha Breaks*（arXiv 2603.13252）：LightGBM 截面排序模型在开发期很强，但 2024 年留出期遇到 AI 主题行情与板块轮动，60/90 日 RankIC 转负，20 日 RankIC 从 0.072 降到 0.010。作者提出策略级的 regime 信任门：信任度低时不交易，再叠加个股级不确定性截断。

启示：L5 前瞻验证之外，上线后还需要“是否交易”的策略级门控。

## 5. 评估卫生：已发表系统中的泄漏

- AutoScientist-Quant 指出，AlphaAgent、QuantaAlpha、R&D-Agent(Q) 共用的评估代码有两处前视问题：信息类指标按全样本而非声明的测试段计算，使选择反馈包含了未来评估期；一个失效的分段过滤器让模型在自己的训练区间上被打分。修复后所有方法重新评估。它还因为“R&D-Agent(Q) 的循环在其报告的窗口上选择因子”而将其排除出对比基线。
- FactorEngine 在对比时为 AlphaAgent 和 R&D-Agent(Q) 重新切分挖掘阶段的训练、验证、回测窗口，挖掘完成后再用原始切分训练多因子模型并回测。
- 这与 honest evaluation 论文（arXiv 2608.27734）的结论一致：LLM 研究系统报告的指标，需要在“反馈窗口 ≠ 报告窗口”并修正前视后重新审视。

## 6. 对本仓库设计的启示

1. **因子评估对模型稳健**：以对下游组合的边际贡献评价因子（AlphaGen），并用一组参考模型（ridge、默认 GBDT、排序模型）交叉检验，而不是只用单一模型。
2. **交替优化 + 感知预算的调度器**：每轮只动一边；调度器参考 R&D-Agent(Q) 的 contextual bandit，加上连续探索上限，以及 AutoScientist-Quant 的 STOP 选项和预算条件。
3. **结构与参数分离**：agent 改结构，连续超参交给贝叶斯优化等确定性优化器，并且在 walk-forward 的每个训练段内部完成。
4. **模型侧动作的优先级**：预测目标与损失函数（排名目标、Rank IC 目标、中性化目标）> 集成方式 > 模型结构；复杂模型和 TSFM 只作为候选，不作为默认方向。
5. **模型随因子更新是流水线职责**：固定配置的滚动重训、动态组合重加权（AlphaForge），以及可选的 DDG-DA / DoubleAdapt，属于确定性组件。
6. **反馈窗口与报告窗口必须分离**：给 evaluator 加不变量测试（指标只在声明的区间计算），参考 `mmr` 的 `tests/invariants`。
7. **部署期 regime 门控**：在 L5 之后加策略级是否交易的判断。

## 7. 值得跟踪的开源实现

- `microsoft/RD-Agent`：`rdagent fin_quant` 的因子/模型交替循环与 bandit 调度（仓库已收录，`factor-mining/RD-Agent`）。
- `AlexrandAI/alphagen`：组合感知的因子奖励（`calc_pool_IC_ret` 等接口）。
- `SJTU-DMTai/DoubleAdapt`、Qlib `examples/benchmarks_dynamic/`：增量学习与滚动重训基线。
- AutoScientist-Quant、AlphaCrafter、FactorEngine 截至本次调研未确认开源代码。
