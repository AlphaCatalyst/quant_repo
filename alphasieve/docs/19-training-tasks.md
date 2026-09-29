# 19 · 投资任务的模型训练定义

状态：设计规范，待实现。起草日期 2026-09-29。

本文定义四个 mandate 的**训练任务**。训练任务的产物是日期 × 标的的样本外分数，组合、执行和最终收益判断仍由策略层完成。本文不打开 holdout 或 fresh，也不改变现有评测、回测、gate、ledger 和 strategy 代码。

## 1. 共用契约

### 1.1 时间、标签和可交易性

- 面板行键为 `(date, code)`。特征只使用 `date` 收盘后已经可获得的信息；财报按首次公告日后的第一个交易日生效；资金流向是当日收盘后可得的信息，与收盘价同属当日；两融余额次日早上才公布，按快照日后的第一个交易日生效；事件按公告日后的第一个交易日对齐。
- 现有价格标签的精确定义为：
  \[
  y_{h,t,i}=\frac{open_{t+1+h,i}}{open_{t+1,i}}-1,
  \]
  其中 `h` 为交易日数，`open_{t+1}` 是 T 日收盘后可以决定的下一交易日开盘价。面板已提供 `label_1d/5d/10d/20d`。
- 若 `tradable_buy(t+1,i)` 为假（停牌、开盘涨停或无成交），标签为缺失；卖出端若停牌或跌停，标签仍保留为理论收益，但组合回测必须按无法卖出处理。退市前最后可交易日之后没有标签。
- 每个标签在样本构造阶段按 `h+1` 个交易日做 embargo；训练窗口末尾不得使用仍在未来持有期内的行。重叠标签允许进入训练，但同一资产的重叠观测必须以 `purge_group=code` 记录，敏感性分析须提供不重叠版本。
- 不在 panel 中覆盖原始标签。模型输入的截面变换只在当日、训练折内拟合：先按 1%/99% 分位 winsorize，再 z-score；常数截面记为缺失。排名目标使用当日有效样本的百分位秩减 0.5。
- 任何特征缺失不填未来值。默认规则是保留行、将该特征置为截面中位数并增加 `missing_<feature>` 指示列；若当日有效特征少于配置的 `min_feature_coverage`，该资产不打分。事件没有发生不等于缺失：`fc_positive` 等事件字段按事件语义填中性值，`fc_age` 仍需保留。
- 当前行业字段是证监会当前快照，非 PIT；A/C/D 的行业中性化和风险约束必须在报告中标记该 warning。官方指数权重缺失时，只能用 PIT 成分 × 流通市值代理，并报告代理与真实指数收益的跟踪误差。

### 1.2 训练、验证和证据边界

- `dev` 固定为 2012-01-01 至 2022-12-31，训练和开发内验证只使用 dev；holdout 固定为 2023-01-01 至 2026-09-25，fresh 为其后窗口。holdout 及 fresh 永远不参与训练、特征选择、超参数搜索、阈值校准或早停。
- 默认采用 walk-forward：每个重训点只读取过去 `train_years` 年（若任务指定 expanding，则从 `dev_start` 起扩展），先 purge/embargo，再在后续窗口打分。重训频率由任务指定；月度重训点在当月第一个交易日，周度任务在周一后的第一个交易日。
- 超参数搜索只能在当前训练窗口内部的 dev 子折进行。推荐 3 折时间序列内层验证（每折都 purge），候选配置最多 12 个。内层选参是预先声明的训练算法的一部分：一次完整的策略回测（固定的任务配置、候选网格、特征版本、标签定义、组合参数）计一个 `strategy` 层 trial，不论内部评估了多少候选。配置里预先声明的多个 seed 属于同一个 trial；为了换一个结果而更换 seed、网格、特征或组合参数重跑，都计新的 trial。按 P-5 分层计算搜索折扣，不能用因子 trial 数替代策略 trial 数。
- 模型指标的选择顺序为：有效覆盖率 → 截面 RankIC 均值和 ICIR → top-minus-bottom（或事件 CAR 差）→ 成本后的策略净 IR。只有策略层完整回测可判断是否上线；holdout 读取须先锁定配置并由 human 批准，每个 mandate 默认一次读取。
- 每次产物必须带 `code_version`、panel `signature`、数据窗口、特征版本、标签版本、训练折、seed、模型参数和 trial id。训练结果写入模型 artifact，不回写因子 ledger。

### 1.3 模型输出契约

每个任务输出 `score(date, asset)`，范围可为任意实数，但方向必须统一为“越大越应增加多头权重”。可选输出 `expected_return`、`risk`、`uncertainty`；组合层不得把缺失分数当作零收益。输出同时记录：有效标的数、截面均值/标准差、分数 RankIC、相邻重训日的 Spearman 相关、分数衰减（1/5/20 个交易日）和预测换手代理 `0.5*sum(abs(rank_t-rank_{t-1}))`。组合层负责校准为权重，不把模型概率直接当资金比例。

## 2. 任务 A：中证 500 指数增强（优先）

### 2.1 目标与标签

主目标是相对中证 500 的可交易超额。训练先预测股票未来收益，再由组合层施加基准、行业、风格和换手约束。

- 主标签：`y20_raw = label_20d`，并行辅助标签 `y5_raw = label_5d`。二者均为上面的次日开盘到未来开盘收益。
- 推荐训练目标为截面行业/规模中性残差：在每个 `(date, h)` 截面上，用行业哑变量、`log(circ_mv)` 及其二次项、beta（过去 60 日相对中证 500）回归 `y_h_raw`，标签取回归残差；样本少于 100 个时退回行业均值残差。原始收益也保留用于报告，不能把残差解释为指数超额的最终收益。
- 训练支持两种任务：`regression_residual`（ridge、LightGBM 回归）和 `rank_residual`（LambdaRank/pairwise，按行业内或全截面分组）。首版以两个 horizon 的标准化预测平均为综合分数；不得把 5 日和 20 日的原始回归系数直接相加。
- 标签先按日期 winsorize，再在训练折内做 z-score；目标分布和残差回归参数只能由训练折估计。不可买样本、ST、停牌样本、开盘涨停样本剔除；训练时可保留开盘跌停样本，但交易验收必须按卖不出处理。

### 2.2 样本构造

- 预测池是月初 PIT `in_zz500` 成分，且满足非 ST、上市至少 60 个交易日、T 日有有效价格；第一版不允许池外股票。训练池默认 CSI 800（`in_csi800`）以增加横截面和历史样本，预测时严格过滤到 CSI 500；训练池选择是配置项，必须在 dev 内比较 `csi500` 与 `csi800` 两个固定候选，不得按结果临时换池。
- 每个交易日生成一行有效样本；特征低频更新时沿用最后一个 PIT 值并记录 age。调仓虽为每 5 日，模型仍可日频打分，组合层只在调仓日消费分数。
- 训练窗口默认 5 年、至少 3 年有效交易日；每次拟合至少 50,000 个样本且每个训练日至少 100 个有效标的。若不满足，跳过该重训点并沿用上次模型，同时在 artifact 记录。
- 对 `h=5` 和 `h=20` 分别 purge `h+1` 个交易日；联合训练时取最长 21 个交易日的 embargo。重叠标签保留为主结果，另跑按资产每 20 日抽样的不重叠敏感性版本。

### 2.3 特征与预处理

首版特征来自 factor library 的固定版本，加上以下面板字段：

| 类别 | 字段/因子 | 处理 |
|---|---|---|
| 价量短期 | `ret_1d`、`overnight_ret`、`intraday_ret`、`vwap_dev`、成交量额、换手、波动率、反转/动量派生因子 | 截面 rank、winsorize；5 日标签重点 |
| 估值规模 | `circ_mv`、`pe_ttm`、`pb_mrq`、`ps_ttm` | `log(circ_mv)`；行业内 rank；20 日标签 |
| 基本面 | `roe_avg`、`np_margin`、`eps_ttm`、`yoy_*`、全部 `ws_*` | PIT 生效日、缺失指示；20 日标签 |
| 事件 | `fc_*`、`ex_*`、`ws_np_surprise_q` | 事件 age/中性填充；与 C 的事件分数可作为独立版本输入 |
| 风险 | 60 日 beta、波动率、流动性、行业暴露 | 仅用于残差化和组合诊断，不能泄漏未来 |

每个日期先做行业内 rank，再做全截面 z-score；缺失覆盖低于 80% 的字段不得进入该版本。特征清单和因子 canonical expression 的哈希组成 `feature_version`；因子库更新必须产生新版本，不能覆盖旧模型。

### 2.4 训练协议、模型和选择

- 月初重训，最近 5 年 rolling；首个可评分日需有 2 年 warm-up。内层按时间 3 折，配置最多 12 个（ridge alpha、LightGBM 深度/叶数/正则、LambdaRank 的 group 参数）。
- 首版模型顺序：ridge 回归作为稳定基线；LightGBM 回归作为主模型；LambdaRank 只有在 pairwise 分组和 top-minus-bottom 稳定优于回归时启用；MLP 延后，不进入第一轮验收。
- 选择先最大化 dev 内 RankICIR，再要求 top-bottom 为正且分数衰减可接受，最后跑现有 B3/B4 dev 策略回测，以成本后净 IR 和跟踪误差筛选。参考 S-6：中证 800、20 日、月度 LightGBM 曾得 IC 0.088、ICIR 0.70、IR 0.55（修正组合约束前的 IR 0.93 不可复用）；ridge IC 0.077、IR 0.37。该结果只用于先验，不是 A 的验收结论。
- 综合分数为 `0.5*z(score_5)+0.5*z(score_20)`；可选按内层 ICIR 加权，但权重必须锁定在 dev 内。不同 seed 的 LightGBM 最多 3 个，按截面 z-score 后等权；seed 列表在配置中预先声明，写入 manifest。
- 算力：月度重训 × 内层 3 折 × 12 个候选，dev 上约 100 个重训点、3,600 次拟合。为此把选参频率降为每年一次（每年初在内层折上选一次配置，月度只按选定配置重拟合），拟合次数降到约 400 + 100 次；重训点之间相互独立，按重训点拆成 Ray 任务并行，在两个集群上分发。

### 2.5 与组合层的接口

输出 5 日、20 日及综合 score；组合层按中证 500 代理基准优化，约束行业偏离 ±2%、单名主动权重 ±1%、主动市值暴露 ±0.2 标准差、beta 0.95–1.05、单次单边换手 ≤15%。模型不输出目标权重，也不自行扣成本；组合层用分数衰减和换手代理诊断是否应降低调仓频率。

## 3. 任务 C：业绩超预期漂移

### 3.1 事件标签和样本

事件单位为 `(code, announcement_date, event_type)`，事件类型为 westock 定期报告、业绩快报和 BaoStock 业绩预告。公告没有时刻，统一在公告日后的第一个交易日开盘尝试入场。

- 事件窗口收益：`R_i(τ1,τ2)=open_{entry+τ2}/open_{entry+τ1}-1`，其中 `entry` 为次日开盘；`τ∈{0,1,5,20,40,60}` 以交易日计。相对收益为减去同日中证 500（或配置基准）收益，CAR 为相对收益在窗口内的累加（报告同时给复合收益）。
- 主标签是 `CAR_1_20`、`CAR_1_40`、`CAR_1_60` 的连续值；训练时可用最高/最低超预期组的 pairwise 排序标签。公告日后第一个交易日开盘涨停、停牌、退市或没有有效基准收益的事件剔除；公告前已知的重复修订只保留首次可交易公告并记录版本。
- 超预期特征包括 `ws_np_surprise_q`、预告增幅中值、快报相对上年同期、`fc_positive`、`ex_eps_chg`、历史季节性 surprise z-score。每种事件类型单独标准化，再合并；没有事件的普通股票日不作为事件样本。
- 样本按事件发生日排序。purge 作用在训练集与验证/测试集之间：训练集中标签窗口（进场日到进场后 60 个交易日）与验证起点重叠的事件全部剔除。同一股票相邻两次定期报告相隔约 60 个交易日，窗口部分重叠是常态，保留两者；只有同一报告期的重复或修订公告才去重（保留首次可交易公告）。最低要求为每种事件类型 500 个事件、每个年份至少 50 个事件，否则只报告描述性 CAR。

### 3.2 模型与评价

首版使用 ridge/LightGBM 回归预测 `CAR_1_20`，同时训练 LambdaRank 预测十组排序；MLP 延后。按事件类型和公告年份做 expanding walk-forward，季度重训；内层最多 8 个配置。选择指标为 CAR 高低组差的 t 值、十组单调性、RankIC，再看事件日历组合的成本后净超额、换手和容量。模型输出 `event_score` 及预计持有 20/40/60 日收益，组合层按事件进入/退出，不把未发生事件填成持仓信号。

## 4. 任务 B：行业 ETF 轮动

### 4.1 标签和样本

标的为上市满一年、日均成交额超过配置阈值的行业/主题 ETF；上市前用对应行业指数的区间只作回测替代，并在样本中标记 `proxy_asset=true`。每个 `(date, etf)` 的标签为相对 ETF 等权基准的未来收益：

\[
y_{h,t,e}=\left(\frac{open_{t+1+h,e}}{open_{t+1,e}}-1\right)-\left(\sum_j w_j^{eq}(r_{t+1:t+h,j})\right),\quad h\in\{5,20\}.
\]

若 ETF 开盘不可交易或成交额为零则标签缺失；ETF 无印花税但仍计佣金和滑点。截面不足 10 只时不训练、不更新排名。标签按日期 winsorize/z-score，行业指数替代段与真实 ETF 段分开统计。

### 4.2 特征、训练和输出

特征包括 ETF 20/60/120 日动量与反转、波动率、成交额/换手、折溢价（若有）、对应行业成分股聚合的盈利修正、SUE、估值分位和资金流。行业内算子关闭；只做 ETF 截面 rank 和时间序列标准化。周度重训、最近 3 年 rolling、5 日与 20 日双标签；最多 6 个配置，优先 ridge，再 LightGBM 回归，LambdaRank 仅在横截面至少 15 只时启用。输出每只 ETF 的 `score_5`、`score_20` 和综合分数，组合层持有前 3–5 只，每 5 或 10 日调仓。选择指标以长时间序列的 RankIC、top-minus-bottom、相对等权组合净超额和最大回撤为主，单日截面 IC 只作辅助。

## 5. 任务 D：股指期货对冲的市场中性

### 5.1 目标和样本

D 不重新发现多头 alpha，首版复用 A 的 CSI 500 score 和多头组合；训练任务只估计多头组合相对中证 500 的残差收益与 beta，并预测 IC 合约基差/滚动收益。

- 股票标签为 A 的 `y20_residual`，即相对中证 500、行业和规模中性后的 20 日收益；训练样本仍来自 CSI 800，预测过滤 CSI 500。
- 对冲标签为合约可得后定义的 `basis_return_{20d}`：主力/次月合约总回报减去中证 500 指数收益，并扣除换月价差；若没有连续合约映射则不训练该头，不能用现货价格伪造期货基差。
- 期货数据缺失期间只输出股票 alpha，不宣称市场中性。合约到期、换月、涨跌停、停牌和保证金不足的日期从相应标签剔除。

### 5.2 模型和接口

按月 rolling 5 年、20 日标签、21 日 purge；ridge 估计 beta 和残差，LightGBM 回归预测 residual score，最多 8 个配置。选择先看残差 RankIC/ICIR 和 beta 稳定性，再看加入 IC 对冲、保证金 12%–15%、现金缓冲 20%–25%后的净收益、波动、最大回撤和夏普。输出 `long_score`、`beta_hat`、`basis_expected_return`、`hedge_notional` 建议值；组合/执行层最终按实际合约乘数和换月规则确定空头名义本金。

## 6. `TrainingTask` 配置规范

配置文件建议位于 `configs/training_tasks/<task_id>.yaml`，不复用 `search_space.yaml` 的因子搜索字段。以下字段为必填或默认值：

| 字段 | 类型/默认 | 规则 |
|---|---|---|
| `task_id` | string，必填 | `[a-z0-9_-]+`，全局唯一 |
| `mandate` | enum `A/C/B/D`，必填 | 决定标签、资产类型和输出契约 |
| `universe_train` / `universe_predict` | string，必填 | 训练池可宽于预测池；必须是已注册 PIT universe |
| `label` | mapping，必填 | `kind`、`horizons`、`formula_version`、`mask`、`winsorize`、`standardize` |
| `sample` | mapping，必填 | `frequency`、`membership_asof`、`min_history`、`overlap`、`purge_days`、`embargo_days` |
| `features` | mapping，必填 | `factor_refs`、`panel_fields`、`preprocess`、`missing_policy`、`feature_version` |
| `split` | mapping，默认 dev-only | `tier=dev`、`window=rolling/expanding`、`train_years`、`retrain`、`inner_folds` |
| `search` | mapping | `max_configs` 默认 12、`selection_metrics`、`trial_layer=strategy`、`seed_count` |
| `models` | list，必填 | `family`、`loss`、`params`；首版只允许 ridge/lgbm/lambdarank |
| `ensemble` | mapping | `horizon_weights`、`seed_aggregation`、`calibration` |
| `output` | mapping，必填 | `score_field`、可选预测字段、`decay_lags` |
| `portfolio_link` | mapping，必填 | 消费字段、调仓频率、成本/组合回测入口 |
| `platform` | mapping | `cluster`、`remote_root`、`runlab_entity/project`、`artifact_subdir` |

验证规则：`tier` 只能为 `dev`；任何 `holdout`/`fresh` 字符串、路径或日期都拒绝；所有 horizon 必须为正整数；`purge_days >= max(horizons)+1`；预测池必须是训练池子集或有明确过滤器；`max_configs <= 12`、`seed_count <= 3`；事件任务必须声明事件去重；`feature_version`、`panel_signature` 和 `code_version` 缺一不可；D 若没有期货数据映射只能标记 `basis_head=disabled`。

### 6.1 A 示例

```yaml
task_id: a_csi500_residual_v1
mandate: A
universe_train: csi800
universe_predict: csi500
label:
  kind: regression_residual
  horizons: [5, 20]
  formula_version: open_t1_to_open_t1_plus_h_v1
  neutralize: [industry, log_circ_mv, beta_60d]
  mask: [not_suspended, buyable_entry, not_st]
  winsorize: [0.01, 0.99]
  standardize: cross_sectional_zscore
sample: {frequency: daily, membership_asof: month_start, min_history: 3y, overlap: keep_with_purge, purge_days: 21, embargo_days: 21}
features: {factor_refs: library@locked, panel_fields: [circ_mv, pe_ttm, ws_np_surprise_q, fc_chg_mid], preprocess: industry_rank_then_zscore, missing_policy: median_plus_indicator, feature_version: factors-v1}
split: {tier: dev, window: rolling, train_years: 5, retrain: monthly, inner_folds: 3}
search: {max_configs: 12, selection_metrics: [rank_icir, top_bottom, net_ir], trial_layer: strategy, seed_count: 3}
models: [{family: ridge, loss: mse}, {family: lgbm, loss: regression}]
ensemble: {horizon_weights: {5: 0.5, 20: 0.5}, seed_aggregation: mean_zscore, calibration: cross_sectional_zscore}
output: {score_field: score_a, predicted_fields: [expected_return_5d, expected_return_20d], decay_lags: [1, 5, 20]}
portfolio_link: {rebalance_every: 5, benchmark: sh.000905, backtest: strategy_layer_b3}
platform: {cluster: http://28.83.35.117:8081, remote_root: /taijifs_zw35/r2/felixjjiang/alphasieve, runlab_entity: felixjjiang, runlab_project: alphasieve, artifact_subdir: models/a_csi500_residual_v1}
```

### 6.2 C、B、D 示例

```yaml
# C
task_id: c_pead_event_v1
mandate: C
universe_train: ashare_all
universe_predict: ashare_all
label: {kind: event_car, horizons: [20, 40, 60], formula_version: next_open_relative_car_v1, event_types: [ws_report, express, forecast], mask: [buyable_entry, dedupe_same_period]}
sample: {frequency: event, membership_asof: event_date, min_history: 500_events_per_type, overlap: keep_with_purge, purge_days: 61, embargo_days: 61}
features: {factor_refs: [], panel_fields: [ws_np_surprise_q, fc_chg_mid, fc_positive, ex_eps_chg, ex_roe, ex_gr_yoy], preprocess: event_type_zscore, missing_policy: semantic_neutral, feature_version: events-v1}
split: {tier: dev, window: expanding, retrain: quarterly, train_years: 3, inner_folds: 3}
search: {max_configs: 8, selection_metrics: [car_spread_t, monotonicity, net_excess], trial_layer: strategy, seed_count: 1}
models: [{family: ridge, loss: mse}, {family: lgbm, loss: regression}, {family: lambdarank, loss: pairwise}]
ensemble: {horizon_weights: {20: 0.5, 40: 0.3, 60: 0.2}, seed_aggregation: mean_zscore, calibration: none}
output: {score_field: event_score_c, predicted_fields: [car_20d, car_40d, car_60d], decay_lags: [1, 5, 20]}
portfolio_link: {holding_days: [20, 40, 60], entry: next_open, backtest: event_calendar_b3}
platform: {cluster: http://28.83.35.117:8081, remote_root: /taijifs_zw35/r2/felixjjiang/alphasieve, runlab_entity: felixjjiang, runlab_project: alphasieve, artifact_subdir: models/c_pead_event_v1}

# B
task_id: b_etf_relative_v1
mandate: B
universe_train: etf_industry_dev
universe_predict: etf_industry_dev
label: {kind: etf_relative_return, horizons: [5, 20], formula_version: open_t1_relative_equal_weight_v1, mask: [listed_1y, tradable_entry, amount_threshold]}
sample: {frequency: daily, membership_asof: date, min_history: 3y, overlap: keep_with_purge, purge_days: 21, embargo_days: 21}
features: {factor_refs: [], panel_fields: [ret_1d, amount, turnover_rate, industry_aggregate_sue, industry_value_percentile], preprocess: cross_sectional_rank_zscore, missing_policy: median_plus_indicator, feature_version: etf-v1}
split: {tier: dev, window: rolling, train_years: 3, retrain: weekly, inner_folds: 3}
search: {max_configs: 6, selection_metrics: [rank_ic, top_bottom, net_sharpe], trial_layer: strategy, seed_count: 1}
models: [{family: ridge, loss: mse}, {family: lgbm, loss: regression}]
ensemble: {horizon_weights: {5: 0.5, 20: 0.5}, seed_aggregation: mean_zscore, calibration: cross_sectional_zscore}
output: {score_field: score_b, predicted_fields: [relative_return_5d, relative_return_20d], decay_lags: [1, 5, 20]}
portfolio_link: {rebalance_every: 5, top_k: 5, benchmark: etf_equal_weight, backtest: strategy_layer_b3}
platform: {cluster: http://21.234.200.155:8081, remote_root: /taijifs_zw35/r2/felixjjiang/alphasieve, runlab_entity: felixjjiang, runlab_project: alphasieve, artifact_subdir: models/b_etf_relative_v1}

# D
task_id: d_ic_neutral_v1
mandate: D
universe_train: csi800
universe_predict: csi500
label: {kind: residual_plus_basis, horizons: [20], formula_version: residual_open_v1, mask: [not_suspended, buyable_entry, futures_mapping_present]}
sample: {frequency: daily, membership_asof: month_start, min_history: 5y, overlap: keep_with_purge, purge_days: 21, embargo_days: 21}
features: {factor_refs: [a_csi500_residual_v1], panel_fields: [beta_60d, volatility_60d], preprocess: residual_zscore, missing_policy: no_basis_no_neutral_claim, feature_version: d-v1}
split: {tier: dev, window: rolling, train_years: 5, retrain: monthly, inner_folds: 3}
search: {max_configs: 8, selection_metrics: [residual_icir, beta_stability, net_sharpe], trial_layer: strategy, seed_count: 1}
models: [{family: ridge, loss: mse}, {family: lgbm, loss: regression}]
ensemble: {horizon_weights: {20: 1.0}, seed_aggregation: mean_zscore, calibration: beta_target_1}
output: {score_field: score_d, predicted_fields: [beta_hat, basis_expected_return, hedge_notional], decay_lags: [1, 5, 20]}
portfolio_link: {hedge: IC, rebalance_every: 5, margin: 0.15, cash_buffer: 0.25, backtest: futures_hedged_b3}
platform: {cluster: http://21.234.200.155:8081, remote_root: /taijifs_zw35/r2/felixjjiang/alphasieve, runlab_entity: felixjjiang, runlab_project: alphasieve, artifact_subdir: models/d_ic_neutral_v1}
```

## 7. 对现有代码的实现顺序

以下是后续工程变更的依赖顺序；本次不实施：

1. `src/alphasieve/configs/` 新增 `training_tasks/` loader、schema 和校验，锁定 dev-only、label/purge/search 规则；同步 `src/alphasieve/config.py` 暴露配置版本。
2. `src/alphasieve/data/panel.py` 与 `src/alphasieve/data/access.py` 增加 PIT universe、事件样本、ETF/期货字段、标签版本和按任务的 mask；保持现有 `label_*` 兼容。
3. `src/alphasieve/strategy/model.py` 拆出 `TrainingTask` 驱动的 sample builder、残差标签、截面预处理、LambdaRank 分组、内层 purged CV、多 horizon/seed ensemble；保留现有 ridge/LightGBM 入口作为兼容适配层。
4. 新增 `src/alphasieve/strategy/tasks.py`（任务契约与输出）、`src/alphasieve/strategy/events.py`（C 的事件窗口）和 `src/alphasieve/strategy/futures.py`（D 的合约/基差）；只依赖 data 与 model，不调用 ledger。
5. `src/alphasieve/strategy/run.py` 增加 `--training-task`，将任务配置、panel signature、code version、seed 和模型 artifact 写入 strategy trial 请求；禁止从命令行绕过 schema。
6. 组合层和执行层随后增加 A/B/C/D 的消费适配、ETF/事件/期货回测入口及模型分数衰减诊断；这些变更必须单独评审，不能在训练任务实现中偷偷改变成本或 gate。
7. ledger/trial 接口最后增加 `layer=strategy`、`max_configs` 和 search discount 聚合；holdout request 只由策略层在配置锁定后产生。RunLab adapter 记录同一 manifest，平台 submit 只传 dev 数据。
8. 在实现完成后才运行 dev 的 focused tests、普通路径策略回测和两集群 smoke；本设计阶段不提交 Ray job、不读 holdout/fresh。

## 8. Ray、存储和复现

训练可提交到 `http://28.83.35.117:8081` 或 `http://21.234.200.155:8081`，按任务配置选择；两集群都挂载 `/taijifs_zw35/r2`，节点无互联网且不能访问 westock，因此 westock/期货原始同步必须在本机完成并只上传截断到 dev 的面板或 artifact。平台上只允许存在 dev panel，`deploy/ray/submit.sh` 的 holdout/fresh 检查保持为硬拒绝。

模型和分数存于 `/taijifs_zw35/r2/felixjjiang/alphasieve/models/<task_id>/<run_id>/`，至少包含 `model.bin`、`scores.parquet`、`manifest.json`、`feature_stats.json` 和 `metrics.json`。RunLab 使用 entity `felixjjiang`、project `alphasieve`；API key 只从远端 `secrets/runlab.env` 读取，不能出现在任务参数或日志。manifest 必须记录 git/code version、panel signature、配置 hash、训练日期、特征版本、依赖锁、随机种子、集群地址和输入 artifact hash。任务失败或跳过重训也要写状态和原因，不能用空分数代替。

## 9. 风险和待确认问题

| 问题 | 影响 | 当前处理 |
|---|---|---|
| 重叠 horizon 标签泄漏或有效样本被高估 | IC/ICIR 偏乐观 | 主结果保留但 purge；必须提供不重叠敏感性 |
| 行业是当前快照，不是 PIT | 中性化和风险暴露有轻微前视 | warning；后续接入 PIT 行业前不能宣称完全无前视 |
| 官方 CSI 500 权重不可得 | 基准代理误差 | 成分 × 流通市值代理并报告跟踪误差 |
| C 事件没有公告时刻、事件可能重复修订 | 入场时间和 CAR 偏差 | 统一次日开盘、同一报告期只保留首次可交易公告 |
| B 截面仅 20–30 只且 ETF 历史短 | 截面 IC 噪声和替代段偏差 | 小网格、长时间序列、proxy 标记 |
| D 缺少 IC 历史连续合约/换月数据 | 无法验证基差和保证金 | 数据补齐前只训练股票残差，不发市场中性结论 |
| westock 年报约三分之一存在追溯调整 | dev 基本面可能偏乐观 | 保留 panel warning；特征版本和来源写入 manifest |
| 平台节点不能访问 westock | 任务无法现场补数据 | 仅使用本机生成且截断到 dev 的面板 |
| 训练/组合参数试验过多 | 搜索折扣和上线判断失真 | 每配置计 strategy trial，最多 12，holdout 每 mandate 默认一次 |
| A 的训练池是否 CSI 800 | 统计功效与分布迁移的权衡 | dev 内预注册 csi500/csi800 两候选，由任务配置锁定 |
| A 第一版是否允许 20% 非成分股 | 约束与容量定义变化 | 本规范第一版禁止，需用户另行确认后新增版本 |

