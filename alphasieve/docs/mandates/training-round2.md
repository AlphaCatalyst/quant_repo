# 20 · 训练任务第二轮设计

状态：已实现（2026-09-30，D-32），实现状态、偏差和 dev 结果见 §8。设计基于 2026-09-29 dev 验收和 D-31。

## 1. 本轮范围与当前基线

`docs/acceptance/acceptance-training.md` 的结果全部来自 dev（2012–2022），扣成本、5 亿规模、全收益基准。A v4 已改用线性规划组合，但年化超额 5.3%、IR 1.09、TE 4.9%、7 年 6 年为正、超额最大回撤 -8.1%，搜索折扣后 IR 0.63，仍未达到 A 的全部验收门槛。C v1 的 CAR60 最高/最低十分位价差为 6.0%、t=20，但日历组合 IR 只有 0.11；C v2 年化超额为 -1.4%。B v1/v2 的截面 IC 约 0–0.03，只有 ETF 自身价量，暂不能证明行业轮动。D 之前使用全收益成员代理，`basis_included=false`，结果不能作为期货对冲验收。

本轮四项工作是：

1. 冻结 C 的 walk-forward 事件分数，把它作为 A v5 的稀疏特征；
2. 在不把覆盖不足的数据伪装成 2012 年历史的前提下，扩充因子库；
3. 用当前 ETF 持仓建立行业/股票集合映射，重做 B，并量化映射前视；
4. 使用已实现的新浪 IC 数据和 `futures_IC.parquet` 重做 D，明确基差方向，并只在有真实期货覆盖的区间验收。

本轮 A 的新增策略层 trial 预算最多 **4 个**（A 累计最多 `N=9`，含已有 5 个）。4 个配置必须在开始前锁定；改变事件滞后、因子清单、训练池、模型网格、组合参数或基准口径都算新的配置。因子层的单因子筛选不计为策略 trial，但必须有独立筛选报告和固定入选清单。B 最多 3 个策略 trial，D 最多 2 个；C 不再开独立组合 trial，只冻结一个已完成的 C walk-forward 版本供 A 使用。

## 2. C 事件分数接入 A（A v5）

### 2.1 输入的时间契约

事件字段的实际来源和当前 panel 字段为：`ws_stat_date`、`ws_np_surprise_q`、`ws_np_growth_q_yoy`、`ws_rev_growth_yoy`、`fc_age`、`fc_chg_mid`、`fc_positive`、`ex_eps_chg`、`ex_roe`、`ex_gr_yoy`。`training/events.py::detect_events` 把新报告期、`fc_age == 0` 和快报字段变化识别为事件。panel 中的事件值在公告日之后的第一个交易日生效；因此 A 的事件特征再向后滞后一个交易日：事件公告日为 `a`，panel 生效日为 `t1`，最早允许出现在 A 样本日的是 `t2`。

A 在 T 收盘后生成特征、次日开盘交易。为避免“公告后第一天收盘才看到、却在同一天形成交易”的边界争议，事件特征统一使用 `effective_date + 1` 的值；事件在 `t2` 才可被 A 读取。`event_available_date`、原始公告日、panel 生效日必须写入特征 artifact。任何事件字段的 forward fill 都不得跨过公告生效日。

C 分数不是用 C 全区间一次拟合后回填。对每个 A 样本日 `T`，取最近一次 C walk-forward 模型在 `T-1` 收盘前已经产生的 `event_score`；该 C 模型的训练集末端仍须早于其预测点并满足 60 日事件 purge。若 C 的下一次重训点在 T 之后，则继续使用上一版 C 模型。C 分数 manifest 必须包含 `c_task_id`、`c_config_hash`、C 模型的训练截止日、C panel signature 和 `score_available_date`。A 训练、内层选参和打分都只 join `score_available_date <= T-1` 的记录。

### 2.2 变成 A 特征的字段

对每只股票每日生成以下特征；这些是事件信息的派生列，不是把未来 CAR 当标签：

| 特征 | 定义 | 无事件值 | 预期方向 |
|---|---|---|---|
| `event_age_any` | T 到最近一次可用事件的交易日数，超过 60 日截断为 61 | 61，并增加 `event_missing_any` | 越近影响越强 |
| `event_decay_any` | 最近事件分数 `s` 乘 `exp(-ln(2)*age/20)` | 0 | C 分数正向 |
| `event_decay_5/20/60` | 分别用半衰期 5、20、60 日的衰减分数 | 0 | 由 dev 内锁定 |
| `event_score_last` | 最近可用 C walk-forward 分数，按事件类型 z-score 后合成 | 0，另有 missing | 正向 |
| `event_score_ws/forecast/express` | 最近同类型分数及其衰减 | 0 | 由类型筛选 |
| `event_surprise_last` | 最近事件的 `ws_np_surprise_q` 或 `ex_eps_chg`，按类型截面 rank | 0 | 通常正向 |
| `event_surprise_signed` | 预告中值/快报 EPS 变化保留符号的 winsorized z-score | 0 | 正向 |
| `event_type_onehot` | 最近事件类型 one-hot；没有事件全为 0 | 0 | 交由模型学习 |
| `event_count_60` | 过去 60 个交易日的去重事件数 | 0 | 需防止拥挤 |
| `event_conflict_60` | 过去 60 日正向与负向 surprise 的数量差/总数 | 0 | 正向 |
| `event_score_rank_age` | `event_decay_any` 在 A 预测池的当日 rank | 当日中性 0 | 正向 |

同一交易日同一股票有多个类型事件时，不把事件数重复计算：各类型先取最新事件；`event_score_last` 取类型分数的等权平均，`event_surprise_last` 取公告时间未知时最保守的最小值，并记录 `event_multi_type=true`。事件特征必须经过 A 的既有行业内 rank/z-score；不把稀疏原值直接与价量特征相加。

无事件股票保留在 A 的横截面中，`age=61`、衰减分数为 0、类型 one-hot 为 0，并增加 missing 指示。缺失与“明确中性事件”不能混淆；若事件字段本身不可用则整组特征按 `min_feature_coverage` 丢弃并写明原因。C 分数缺失不能用训练期均值补成正值。

### 2.3 实现改动、配置与 trial

需要新增或修改的文件和函数：

1. `src/alphasieve/training/events.py`：新增 `event_feature_panel()`、`available_event_scores()`，实现事件 age、衰减、类型聚合和 `effective_date + 1` 检查；扩展返回表以包含公告日和可用日。
2. `src/alphasieve/training/samples.py::feature_grids()`：支持带 `score_available_date` 的 as-of join，并拒绝未来 C score。
3. `src/alphasieve/training/run.py`：在构造 A 样本前冻结并校验 C artifact manifest；记录 C 版本和 join 覆盖率。
4. `src/alphasieve/training/task.py`：增加 `event_sources`、`event_lag_days`、`event_half_lives`、`frozen_score_task` 等配置校验；要求 A v5 的事件滞后至少 1 个交易日。
5. `configs/training_tasks/a_csi500_residual_v5.yaml`：只新增配置，不在本轮直接修改旧 v4。

配置示例：

```yaml
task_id: a_csi500_residual_v5
mandate: A
universe_train: csi800
universe_predict: csi500
label: {kind: regression_residual, horizons: [5, 20], formula_version: open_t1_to_open_t1_plus_h_v1,
        neutralize: [industry, log_circ_mv, beta_60d], mask: [not_suspended, buyable_entry, not_st}
sample: {frequency: daily, membership_asof: month_start, min_history_years: 3, min_names_per_date: 100,
         min_train_rows: 50000, overlap: keep_with_purge, train_stride: 1, purge_days: 21, embargo_days: 21}
features:
  factor_refs: a_locked_18_plus_screened
  panel_fields: [circ_mv, pe_ttm, ws_np_surprise_q, fc_chg_mid]
  event_sources: [c_pead_event_v1]
  event_lag_days: 1
  event_half_lives: [5, 20, 60]
  frozen_score_task: c_pead_event_v1
  preprocess: industry_rank_then_zscore
  missing_policy: median_plus_indicator
  min_feature_coverage: 0.8
split: {tier: dev, window: rolling, train_years: 5, warmup_years: 2, retrain: monthly, select_every: yearly, inner_folds: 3}
models:
  - {family: ridge, grid: {alpha: [3.0, 10.0, 30.0]}}
  - {family: lgbm, grid: {num_leaves: [15, 31], min_child_samples: [200]}}
search: {max_configs: 6, selection_metric: rank_icir, seeds: [0, 7]}
ensemble: {horizon_weights: {5: 0.5, 20: 0.5}, seed_aggregation: mean_zscore}
output: {score_field: score_a_v5, decay_lags: [1, 5, 20]}
portfolio: {kind: index_enhancement, benchmark: zz500, construction: lp, rebalance_every: 10,
            industry_dev: 0.02, name_cap: 0.01, size_limit: 0.2, beta_range: [0.95, 1.05],
            turnover_cap: 0.15, aum: 500000000, max_participation: 0.10}
```

A v5 4 个 trial 的顺序固定为：`event_only`、`factor_family_only`、`event_plus_factor`、`event_plus_factor_sensitivity`。前三个分别回答事件边际贡献、因子边际贡献和合并效果；第四个只改变预先登记的事件半衰期集合或训练池，不能同时换模型和组合。C 分数本身只做一次冻结 artifact 复现，不计新的 C 策略 trial。

验收标准：A 原有全部门槛同时满足（年化净超额 ≥6%、IR ≥1.0、TE 4%–6%、超额最大回撤不低于 -8%、7 年至少 5 年为正、5 亿规模下降不超过 1.5 个百分点）；此外事件特征的增量 dev RankICIR 必须为正，去掉事件组后 IR 下降至少 0.05，事件 score 的可用率和 join 未来行数分别报告，未来行数必须为 0。若 A 只因事件特征提高而通过，但事件分数的时间戳审计失败，整个 trial 作废。

## 3. 扩充因子库

### 3.1 现有字段核对和覆盖原则

当前 dev panel 的实际字段包括价格量额、估值、可交易性、当前行业、BaoStock 财务、事件、日内聚合、westock 报表（`ws_*`）、资金流向（`mf_*`）、两融（`mg_*`）和标签。`src/alphasieve/data/fundamentals.py` 实际派生字段为：

- 报表：`ws_roe_ttm`、`ws_roa_ttm`、`ws_gross_margin_ttm`、`ws_op_margin_ttm`、`ws_cfoa_ttm`、`ws_accruals_ttm`、`ws_debt_to_assets`、`ws_ibd_to_equity`、`ws_goodwill_to_equity`、`ws_cash_to_assets`、`ws_rd_to_rev_q`、`ws_asset_growth_yoy`、`ws_rev_growth_yoy`、`ws_np_growth_q_yoy`、`ws_np_surprise_q`、`ws_np_ttm`、`ws_rev_ttm`、`ws_ocf_ttm`、`ws_equity`。
- 资金流：`mf_main_net_ratio`、`mf_jumbo_net_ratio`、`mf_block_net_ratio`、`mf_mid_net_ratio`、`mf_small_net_ratio`，覆盖从 2020 年开始。
- 两融：`mg_fin_to_mv`、`mg_fin_chg_4w`、`mg_fin_buy_share`、`mg_short_to_fin`，历史覆盖从 2018/2019 年开始，按快照后首个交易日可用。

当前 panel schema 没有可用的研报/一致预期字段，也没有历史研报发布日。任何 `analyst_*`、目标价、盈利预测上调字段必须先新增 provider、PIT 对齐和 panel 字段；在此之前不能把它们放入 A 2012–2022 的训练配置。资金流和两融不足 2012 的年份不能回填 0：A 主任务要么排除这类特征，要么单独建立 2020+ overlay，并将覆盖区间写入验收。

### 3.2 候选因子清单

以下 32 个候选按家族预注册。公式中的 `rank_cs` 是当日截面 rank，`lag1` 是字段生效后的首个交易日再滞后一个交易日；滚动统计只使用 T 及以前数据。方向是对未来相对收益的初始假设，不是验收结论。

| 家族/因子 | 公式 | 字段 | PIT 滞后 | 最早覆盖 | 预期方向 |
|---|---|---|---|---|---|
| 价量 `mom_20` | `close/close[-20]-1` | `close` | 当日收盘 | 2012 | 正 |
| 价量 `mom_60` | `close/close[-60]-1` | `close` | 当日收盘 | 2012 | 正 |
| 价量 `mom_120_20` | `close[-20]/close[-120]-1` | `close` | 当日收盘 | 2012 | 正 |
| 价量 `rev_5` | `-(close/close[-5]-1)` | `close` | 当日收盘 | 2012 | 正 |
| 价量 `rev_20` | `-(close/close[-20]-1)` | `close` | 当日收盘 | 2012 | 正 |
| 价量 `overnight_20` | `mean(open/close[-1]-1,20)` | `open`,`close` | 当日收盘 | 2012 | 负/待定 |
| 价量 `intraday_20` | `mean(close/open-1,20)` | `open`,`close` | 当日收盘 | 2012 | 负/待定 |
| 价量 `vol_20` | `std(ret_1d,20)` | `ret_1d` | 当日收盘 | 2012 | 负 |
| 价量 `amihud_20` | `mean(abs(ret_1d)/amount,20)` | `ret_1d`,`amount` | 当日收盘 | 2012 | 负 |
| 价量 `turn_accel` | `mean(turnover,5)/mean(turnover,60)-1` | `turnover_rate` | 当日收盘 | 2012 | 待定 |
| 估值 `ep` | `1/pe_ttm` | `pe_ttm` | 当日收盘 | 2012 | 正 |
| 估值 `bp` | `1/pb_mrq` | `pb_mrq` | 当日收盘 | 2012 | 正 |
| 估值 `sp` | `1/ps_ttm` | `ps_ttm` | 当日收盘 | 2012 | 正 |
| 估值 `value_mix` | `mean(rank(ep),rank(bp),rank(sp))` | `pe_ttm`,`pb_mrq`,`ps_ttm` | 当日收盘 | 2012 | 正 |
| 报表 `ws_roe` | `ws_roe_ttm` | `ws_roe_ttm` | 公告后首日+1 | 2012 | 正 |
| 报表 `ws_roa` | `ws_roa_ttm` | `ws_roa_ttm` | 公告后首日+1 | 2012 | 正 |
| 报表 `gross_margin` | `ws_gross_margin_ttm` | `ws_gross_margin_ttm` | 公告后首日+1 | 2012 | 正 |
| 报表 `op_margin` | `ws_op_margin_ttm` | `ws_op_margin_ttm` | 公告后首日+1 | 2012 | 正 |
| 报表 `cfoa` | `ws_cfoa_ttm` | `ws_cfoa_ttm` | 公告后首日+1 | 2012 | 正 |
| 报表 `accruals` | `-ws_accruals_ttm` | `ws_accruals_ttm` | 公告后首日+1 | 2012 | 正 |
| 报表 `leverage` | `-ws_debt_to_assets` | `ws_debt_to_assets` | 公告后首日+1 | 2012 | 正 |
| 报表 `cash_quality` | `ws_cash_to_assets-ws_debt_to_assets` | `ws_cash_to_assets`,`ws_debt_to_assets` | 公告后首日+1 | 2012 | 正 |
| 报表 `asset_growth` | `ws_asset_growth_yoy` | `ws_asset_growth_yoy` | 公告后首日+1 | 2012 | 负/待定 |
| 报表 `rev_growth` | `ws_rev_growth_yoy` | `ws_rev_growth_yoy` | 公告后首日+1 | 2012 | 正 |
| 报表 `np_growth` | `ws_np_growth_q_yoy` | `ws_np_growth_q_yoy` | 公告后首日+1 | 2012 | 正 |
| 报表 `np_surprise` | `ws_np_surprise_q` | `ws_np_surprise_q` | 公告后首日+1 | 2012 | 正 |
| 报表 `rd_intensity` | `ws_rd_to_rev_q` | `ws_rd_to_rev_q` | 公告后首日+1 | 2012 | 待定 |
| 资金流 `main_flow_5` | `mean(mf_main_net_ratio,5)` | `mf_main_net_ratio` | 当日收盘后+1 | 2020 | 正/待定 |
| 资金流 `block_flow_20` | `mean(mf_block_net_ratio,20)` | `mf_block_net_ratio` | 当日收盘后+1 | 2020 | 待定 |
| 资金流 `flow_reversal` | `-mean(mf_main_net_ratio,5)` | `mf_main_net_ratio` | 当日收盘后+1 | 2020 | 负/待定 |
| 两融 `fin_to_mv` | `-mg_fin_to_mv` | `mg_fin_to_mv` | 快照后首日+1 | 2018/19 | 负 |
| 两融 `fin_change` | `-mg_fin_chg_4w` | `mg_fin_chg_4w` | 快照后首日+1 | 2018/19 | 待定 |
| 两融 `fin_buy_share` | `-mg_fin_buy_share` | `mg_fin_buy_share` | 快照后首日+1 | 2018/19 | 待定 |
| 两融 `short_fin` | `-mg_short_to_fin` | `mg_short_to_fin` | 快照后首日+1 | 2018/19 | 待定 |

日内四字段 `rv_intraday`、`tail30_vol_share`、`open30_ret`、`updown_vol_share` 覆盖从 2020 年开始，另设 `hs300_2020` 任务，不并入 A 的 2012–2022 主样本。当前 panel 没有研报/一致预期字段；未来若新增，候选必须至少包括“盈利预测修正幅度”“预测分歧”“目标价相对收盘价”“报告发布后首日收益反转”，但需先给出原始发布日、首次发布版本和覆盖年份，不能按当前快照回填。

### 3.3 分家族筛选和进入 A 的规则

筛选按以下顺序进行，所有阈值在开始前固定：

1. **数据门槛**：在 A 的 CSI 800 训练池内，按日期统计覆盖率；主 A 因子要求 2012–2022 至少 80% 的有效股票日、每年不少于 150 个交易日。2020+ 的资金流/两融不得为了满足门槛而填充早年；它们只能进入 2020+ overlay 或单独任务。
2. **单因子 dev walk-forward**：以 5 日和 20 日适配的 horizon 分开计算截面 RankIC、ICIR、正 IC 比例、top-bottom、1/5/20 日 IC 衰减和换手代理；残差化方法必须和 A 相同。
3. **稳健性**：分 2012–2015、2016–2019、2020–2022 三段，至少两段同号；剔除不可交易样本后覆盖率不低于 80%。不根据结果翻转方向，方向相反则记录为反向候选并另计一次筛选。
4. **相关性和家族配额**：与 18 个种子及已选候选做截面时间序列相关；相关性 >0.70 的只保留 ICIR/覆盖更高者。每个家族最多进入 3 个，至少保留一个未重复家族；建议从 32 个候选中选 8–12 个进入 A v5 的冻结清单。
5. **边际进入模型**：先固定 A v4 的模型和 LP 组合，依次按家族加入；只有在内层 RankICIR 非负、组合 dev 净 IR 边际提升至少 0.02 且不使换手增加超过 20% 的候选组才进入 A v5。任何“看到最终 IR 后删特征”的操作都算新 trial。

本轮因子单因子筛选建议 32 个候选、每家族一次预注册筛选报告；不为每个候选开策略 trial。最终进入 A 的因子清单只允许在 A v5 四个配置中使用。

需要改动或新增：`src/alphasieve/factors/library.py` 增加派生因子注册；`src/alphasieve/factors/derived.py`（新增）实现滚动公式和字段依赖；`src/alphasieve/factors/pit.py`（新增）统一公告后首日+1 与覆盖起始日；`src/alphasieve/training/samples.py::feature_grids()` 增加家族、覆盖、相关性报告；`src/alphasieve/configs/search_space.yaml` 只在评审后增加字段；`src/alphasieve/configs/training_tasks/a_csi500_residual_v5.yaml` 引用冻结版本。资金流和两融字段需要在特征计算时标记 `coverage_start`，不可伪造 2012 历史。

## 4. 重做 B：行业 ETF 轮动

### 4.1 映射对象和可用性

已确认 westock `etf holdings --date` 不提供历史持仓，`index constituent` 也没有历史版本；panel 的 `industry` 是当前证监会快照。因而本任务不把当前持仓当作历史 PIT。新增静态映射 artifact `etf_mapping_current_v1`，每行包含 `etf_code`、`holding_code`、`holding_weight`、`industry_current`、`mapping_asof`、来源和快照 hash。

映射分两层：

- **行业级主版本**：按当前持仓权重聚合到当前行业，保留权重最高行业直至累计权重 80%，得到 `etf_industry_weight`。每日行业特征由股票 panel 在该行业全体内计算，再按映射行业权重汇总；不使用单只历史持仓权重。这个版本只声称“行业暴露轮动”。
- **股票篮子敏感性版本**：用当前持仓代码和权重直接对股票特征加权，缺失股票权重重新归一化并记录有效权重比例。这个版本更接近 ETF，但前视更强，只作敏感性和披露，不作为首个上线候选。

当前行业映射和股票篮子都在 `mapping_asof` 之后固定；不能根据每日 ETF 涨跌反推历史成分。若 ETF 对应指数有公开历史行业分类，新增第三个历史映射版本，但必须单独标记来源和可用年份。

### 4.2 行业层特征

对每个 ETF、日期、行业权重计算：

- 行业动量：行业个股流通市值加权的 20/60/120 日收益、短期反转和距离 250 日高点；
- 行业内资金：`mf_main_net_ratio`、`mf_block_net_ratio` 的 5/20 日均值及横截面分位；2020 年前为缺失，不填 0；
- 盈利修正：`ws_np_surprise_q`、`ws_np_growth_q_yoy`、`fc_chg_mid`、`ex_eps_chg` 的行业中位数、上升比例和离散度；
- 估值：行业内 `1/pe_ttm`、`1/pb_mrq`、`1/ps_ttm` 的流通市值加权分位；
- 拥挤度：行业成交额/流通市值、换手率、个股收益横截面离散度、涨停比例；
- ETF 自身：20/60/120 日动量、5/20 日反转、波动率、回撤、成交额比率、折溢价（若有）。

行业特征先在股票行业截面内 rank/z-score，再按 ETF 映射权重聚合；ETF 截面再 rank/z-score。某字段在 ETF 的有效映射权重低于 70% 时，该日期该特征为缺失并加 indicator。行业当前快照前视必须在每个 artifact 中显示。

### 4.3 标签、组合和偏差量化

标签继续使用真实 ETF 的次日开盘收益相对 ETF 等权组合：

\[
y_{h,t,e}=r^{ETF}_{t+1:t+1+h,e}-\frac{1}{|E_t|}\sum_{j\in E_t}r^{ETF}_{t+1:t+1+h,j},\quad h\in\{5,20\}.
\]

上市前的行业指数替代只用于扩展描述，不与真实 ETF 段合并为一个 headline；结果分 `proxy` 和 `real_etf` 两段。截面少于 10 只不训练，少于 15 只不启用 LambdaRank。组合首版持有 Top 3/5，5 或 10 个交易日调仓，佣金和滑点按 ETF 规则、印花税为 0，基准为当日可交易 ETF 等权。

前视偏差必须做三组对照：

1. `industry_map`：当前持仓仅映射行业，全体行业股票计算特征，作为主结果；
2. `basket_map`：当前持仓股票篮子直接聚合，作为偏差上界敏感性；
3. `all_industry`：不按 ETF 当前持仓，只按全行业等权/市值加权，作为映射依赖的下界。

每个日期报告三种特征的截面相关、加权绝对差、有效权重、RankIC 差、组合净超额差和换手差。`basket_map - industry_map` 的年化净 IR 差超过 0.20 或 RankIC 差超过 0.02 时，B 只能作为研究结果，不能进入上线候选。静态持仓造成的偏差不从收益中扣除后再宣称修正，必须在标题、manifest 和表格中写 `mapping_asof`。

需要改动或新增：`src/alphasieve/data/providers/westock.py` 增加 ETF 当前持仓读取和快照 hash；新增 `src/alphasieve/data/etf_mapping.py` 的 `build_current_mapping()`、`validate_mapping()`；新增 `src/alphasieve/training/etf_industry.py` 的 `aggregate_industry_features()`、`mapping_bias_report()`；`src/alphasieve/training/etf.py::build_etf_table()` 接受行业特征和三种 mapping mode；`src/alphasieve/training/run.py` 写 mapping artifact 和分段结果。

配置示例：

```yaml
task_id: b_etf_industry_v1
mandate: B
universe_train: etf_sector
universe_predict: etf_sector
label: {kind: etf_relative_return, horizons: [5, 20], formula_version: etf_open_relative_equal_weight_v2,
        mask: [listed_1y, tradable_entry, amount_threshold]}
sample: {frequency: weekly, membership_asof: date, min_history_years: 3, min_names_per_date: 10,
         min_train_rows: 1000, overlap: keep_with_purge, train_stride: 1, purge_days: 21, embargo_days: 21}
features: {factor_refs: [], panel_fields: [industry_momentum, industry_flow, industry_surprise, industry_value,
          industry_crowding, mom_20, mom_60, vol_20, amount_ratio_5_60], preprocess: rank_zscore,
          missing_policy: median_plus_indicator, min_feature_coverage: 0.7}
mapping: {artifact: etf_mapping_current_v1, mode: industry_map, mapping_asof: locked_snapshot,
          comparison_modes: [basket_map, all_industry]}
split: {tier: dev, window: rolling, train_years: 3, warmup_years: 1, retrain: weekly, select_every: yearly, inner_folds: 3}
models: [{family: ridge, grid: {alpha: [3.0, 10.0]}}, {family: lgbm, grid: {num_leaves: [15, 31]}}]
search: {max_configs: 4, selection_metric: rank_icir, seeds: [0]}
ensemble: {horizon_weights: {5: 0.5, 20: 0.5}, seed_aggregation: mean_zscore}
output: {score_field: score_b_industry, decay_lags: [1, 5, 20]}
portfolio: {kind: etf_rotation, benchmark: etf_equal_weight, rebalance_every: 5, top_k: 5, aum: 500000000}
```

B trial 预算最多 3 个：行业映射主版本、股票篮子敏感性、只用 ETF 自身价量的对照。验收要求真实 ETF 段 RankIC 均值 ≥0.03、相对等权年化净超额 ≥5%、Sharpe ≥0.8、最大回撤不高于等权基准；proxy 段只作附录。三种 mapping 的偏差报告必须完整，否则不算通过。

## 5. D：真实期货、基差标签与可行性重评估

### 5.1 已实现的数据边界

本节不重复实现数据同步。`src/alphasieve/data/providers/sina.py` 通过 Referer `https://finance.sina.com.cn` 读取字段 `d/o/h/l/c/v/p`；`src/alphasieve/data/futures.py` 已有 `sync_futures`、`hedge_leg`、`build_futures_tiers`、`read_hedge_leg`，dev 面板旁已有 `futures_IC.parquet`。IC0 从 2017-01-17 开始；单合约完整数据从 2019-04-19 的精确段开始。`training/run.py` 已读取 hedge leg，`mandates.futures_hedged()` 已按真实期货腿扣每次 2bp 换月成本，并把验收限制在期货覆盖日期。

当前对冲规则是确定的：持有当月合约到交割日，交割日收盘开下一个合约。2017-01-17 至 2019-04-18 只有 IC0，`hedge_leg` 用到期日基差收敛至 0.2% 以内推断换月；在 2019-04 至 2022 与精确法对照，44/44 个换月日一致，日收益平均偏差 2.9bp，累计偏差约 -1%/年。该段只能报告为 `source=continuous_inferred_roll`，不能与精确段混为同一验收证据。

### 5.2 基差方向的更正

令 `basis_t = futures_close_t / spot_close_t - 1`。对“多股票、空 IC”的组合，空头每日收益近似 `-fut_ret_t`，基差变化收益近似 `-(basis_{t+1}-basis_t)`，另减换月成本。因此：

- 期货贴水（basis < 0）不是空头收益，而是潜在成本；贴水向 0 收敛时空头亏损；
- 升水向 0 收敛时空头获得收益；
- docs/mandates/mandate-specs §6 “贴水时为正、升水时为负”的表述方向相反，本文件以此处为准，后续应在 docs/mandates/mandate-specs 勘误但本轮不修改它。

已有逐年对冲腿相对价格指数损耗也支持这一更正：2017 -10.5%、2018 -3.4%、2019 -13.0%、2020 -10.1%、2021 -4.8%、2022 -5.4%。因此 D 不能把基差收敛当作天然 alpha；默认固定 beta 对冲可能不可行。

### 5.3 合约判定、收益和标签

当前生产规则保持“持有到交割日”的确定性规则。新增的主力/次主力诊断采用持仓量 `p`：在每个交易日从未到期合约中选持仓量最大者为主力、第二大者为次主力；只有次主力持仓量连续 2 个交易日超过主力的 90%，且距当前合约交割日不少于 5 个交易日，才把候选换月日标成 `oi_roll_candidate`。该诊断不得覆盖已实现的交割日换月，除非单独开新的策略 trial。

对冲腿的日收益规则：

- 同一持仓合约的 `fut_ret_t = close_t/close_{t-1}-1`；空头贡献为 `-hedge_notional_t*fut_ret_t`；
- 换月日收盘平旧合约、下一合约收盘开新合约，换月日不把两个合约价格拼成一个跳空收益；单独扣 2bp × 对冲名义比例；
- 交割日、缺报价、涨跌停或保证金不足时标记不可交易，不用相邻价格插值；
- `basis_t` 使用当日持有合约收盘价与中证 500 现货/成员代理收盘价，保存合约代码、到期日、`days_to_expiry`、source 和 roll 标记。

`basis_head` 不预测“贴水水平”本身，而预测未来 20 个交易日年化基差变化：

\[
label^{basis}_{20,t}=252/20\times(basis_{t+20}-basis_t),
\]

其有经济意义的空头收益方向为 `-label_basis`，并应同时报告绝对基差和基差变化。只有持有合约和现货均可得、未来 20 日没有换月缺口、且属于精确单合约段时才生成标签。IC0 段只报告基差水平和推断换月误差，不开启 `basis_head` 训练。

### 5.4 对冲比例、保证金和可行性决策

资本分配保持 `long_share = 1/(1+margin+cash_buffer)`；首版 `margin=0.15`、`cash_buffer=0.25`，即多头占 71.43% 资本，15% 保证金和 25% 现金缓冲按总资本预留。beta 对冲名义为多头组合过去 60 日 beta 乘以多头净敞口，再按合约乘数取整；取整残差写入现金/未对冲敞口。每天检查保证金占用，若保证金加缓冲不足，停止新增对冲并标记 forced_deleveraging。

D 需要同时报告三种可行性版本：

1. 固定 beta 全额对冲：主基线，含真实 futures leg 和换月成本；
2. 部分对冲：对冲比例 0.5、0.75、1.0，但这三个比例只能作为预先登记的 3 个组合配置，不在结果后挑选；
3. `basis_head`：只在精确段开启，若预测未来 20 日贴水恶化（`label_basis` 更负或空头收益为负）则把对冲比例下调至 0.5，预测改善才恢复至 1.0；阈值由训练折内固定为历史分位，不用全 dev 最优阈值。

如果固定 beta 和部分对冲在精确段都无法达到目标，则 D 降级为“风险管理对冲模块”，不再作为独立收益 mandate。IF/IM 替代只列为后续数据任务，不能在 IC 结果上推断可行。

需要改动或新增：

1. `src/alphasieve/data/futures.py::hedge_leg()` 增加 `oi_rank`、`oi_roll_candidate`、`exact_segment`、`basis_change_20d` 列；不改变当前交割换月主规则。
2. `src/alphasieve/training/samples.py::labels()` 增加 futures-covered mask 和基差变化标签，拒绝 IC0 段的 basis head。
3. `src/alphasieve/training/mandates.py::futures_hedged()` 把 `short_return`、`basis_level`、`basis_change_pnl`、`roll_cost` 分开归因，分别返回 `exact`、`inferred`、`all` 三段统计；基差收益符号按本节定义。
4. `src/alphasieve/training/run.py` 把 `futures_IC.parquet` 的 signature、覆盖起止日、source 占比和 segment acceptance 写入 artifact。
5. `src/alphasieve/training/task.py` 增加 `futures_source`、`segment_policy`、`basis_label`、`hedge_ratio_grid` 校验；`basis_head: enabled` 只允许 `segment_policy: exact_only`。
6. 新增 `src/alphasieve/data/futures_quality.py`，检查合约日期连续性、重复日期、到期日、p/o/c 非正值、换月日一致性和 2bp 成本是否只扣一次。

配置示例：

```yaml
task_id: d_ic_neutral_v2
mandate: D
universe_train: csi800
universe_predict: csi500
label: {kind: residual_plus_basis, horizons: [20], formula_version: residual_open_plus_basis_change_v1,
        mask: [not_suspended, buyable_entry, futures_mapping_present]}
sample: {frequency: daily, membership_asof: month_start, min_history_years: 5, min_names_per_date: 100,
         min_train_rows: 50000, overlap: keep_with_purge, train_stride: 1, purge_days: 21, embargo_days: 21}
features: {factor_refs: [a_csi500_residual_v4], panel_fields: [beta_60d, volatility_60d, basis, days_to_expiry,
          oi_rank], preprocess: residual_zscore, missing_policy: drop, min_feature_coverage: 0.8}
futures: {product: IC, source: sina, segment_policy: exact_only, roll_rule: delivery_close_to_next,
           oi_diagnostic: true, roll_cost: 0.0002, basis_label: annualized_change_20d}
split: {tier: dev, window: rolling, train_years: 5, warmup_years: 2, retrain: monthly, select_every: yearly, inner_folds: 3}
models: [{family: ridge, grid: {alpha: [3.0, 10.0]}}, {family: lgbm, grid: {num_leaves: [15, 31]}}]
search: {max_configs: 4, selection_metric: residual_icir, seeds: [0]}
ensemble: {horizon_weights: {20: 1.0}, seed_aggregation: mean_zscore}
output: {score_field: score_d_v2, decay_lags: [1, 5, 20]}
portfolio: {kind: futures_hedged, hedge: IC, margin: 0.15, cash_buffer: 0.25,
            hedge_ratio_grid: [0.5, 0.75, 1.0], basis_head: enabled, exact_segment_required: true,
            aum: 500000000}
```

D 最多 2 个策略 trial：固定 beta/部分对冲基线，以及精确段的 `basis_head` trial。IC0 段只做数据质量和收益归因，不增加验收样本。D 的验收分别在 2019-04-19 至 2022-12-30 精确段、2017-01-17 至 2019-04-18 推断段和全覆盖报告；只有精确段可判定通过。精确段要求年化净收益 ≥5%、年化波动 ≤6%、最大回撤不低于 -5%、Sharpe ≥1.0，`basis_included=true`，并报告空头腿、基差变化、换月成本、保证金和现金拖累。若精确段不足一个完整年份或期货覆盖率低于 95%，状态为 blocked 而不是失败。

## 6. 防止 dev 过拟合和搜索记账

- A v5 只允许 4 个预注册配置，累计 A 的 strategy trial 不超过 9；每个配置内部最多 6 个候选、3 折、每年一次选参，月度只重拟合获选配置。
- 事件半衰期、因子家族配额、B 的三种 mapping、D 的对冲比例和 basis 阈值都必须在配置中预先列出；不能看完整 dev 曲线后选择。
- 单因子筛选按家族先做相关性和跨时期稳定性，入选因子锁定后才进入 A；不得用 A 最终 IR 反向删除特征。
- 结果必须同时给整体、三段年份、逐年收益、换手、成本、容量、score decay、mapping/source 覆盖和 placebo/敏感性；任何只改善 2016 年的配置不作为胜出理由。
- C score 的 artifact hash、可用日和训练截止日逐行审计；A 发现未来 join 行、C 模型训练截止日晚于 A 样本日或事件生效日错位时，该 trial 标记无效，不以修复后重跑结果替代原记录。
- D 只在精确期货段判定；把 IC0 推断段和无期货段混入总体 Sharpe 会掩盖基差损耗，禁止作为 headline。
- holdout 仍保持每个 mandate 一次人工批准读取；本轮任何 dev 结果不能触发 holdout 自动读取。

## 7. 实现顺序

1. 先完成 C score artifact 的 as-of schema、事件滞后审计和 A v5 事件特征 join；跑 A v5 的 4 个预注册配置。
2. 冻结因子库 32 个候选的字段依赖、覆盖年份和单因子筛选报告，选出 8–12 个候选组；把冻结清单接入 A v5 的因子组合，不新增超预算策略 trial。
3. 完成 ETF 当前持仓快照、行业聚合、三种 mapping 对照和偏差报告；跑 B 的 3 个配置并分 real/proxy 段验收。
4. 在既有 futures 数据链路上增加合约质量、OI 诊断、精确段 basis label、收益归因和 segment report；先跑固定对冲，再在预算内跑 basis head。
5. 汇总四项 dev artifact，更新 acceptance 报告和 D-31 后续决策；只有配置哈希、特征版本、C score hash、mapping snapshot 和 futures signature 全部冻结后，才申请各 mandate 的单次 holdout 读取。

本文件涉及的全部实现仍受现有 holdout/fresh 隔离、策略层 trial ledger、RunLab manifest 和人工 holdout 审批规则约束。

## 8. 实现状态与结果（2026-09-30）

四项都已实现并在 dev 上跑完预算内的 trial，没有一项达到验收门槛。holdout 没有读取；期货和 ETF 行业特征都只构建了 dev 层，holdout 层由人工在批准读取前在本机构建（`data build-futures --tiers holdout`、`data build-etf-industry --asof 2026-09-30 --tiers holdout`）。数字见 [acceptance-training.md](../acceptance/acceptance-training.md) §2a。

### 8.1 实现位置

| 项 | 代码 | 配置 |
|---|---|---|
| C 事件分数接入 A | `training/event_features.py`；`Features.event_source` 在 bundle 里冻结 C 产物的 sha256，运行时核对 | `a_csi500_residual_v5_{event,factor,combined,sensitivity}` |
| 因子库扩充 | `training/derived.py`（24 个候选，7 个家族）；`train screen-derived <task>` 输出筛选报告 | 同上，`derived_fields` 为筛选保留的 13 个 |
| B 行业映射 | `data/etf.py::sync_etf_holdings`；`training/etf_industry.py`；`data build-etf-industry`；`Features.etf_mapping` 在 bundle 里冻结持仓快照的 sha256 | `b_etf_industry_v1`、`b_etf_basket_v1`、`b_etf_relative_v3` |
| D 真实期货 | `data/providers/sina.py`、`data/futures.py`；`data sync --dataset futures`、`data build-futures`；`mandates.futures_hedged` 按段报告 | `d_ic_neutral_v1`（重跑）、`d_ic_neutral_v2` |

### 8.2 与设计的偏差

- **A v5 四个配置只在特征上与 v4 不同**（模型网格、组合、基准都同 v4），这样差异可以归因到特征。事件滞后固定为 1 个交易日；原始分数按所在月之前 250 个交易日的 C 分数转成分位，避免 C 每年重拟合造成的尺度漂移。逐行审计未来行数为 0。§2.2 列出的 `mg_*` 字段在面板中不存在，没有构建。
- **因子筛选只用覆盖率和相关性**：dev 窗口覆盖 ≥ 0.8，且与现有特征及已保留候选的平均截面秩相关绝对值 ≤ 0.7；RankIC 只报告、不参与选择。保留 13 个（§7 预期 8–12 个，规则固定后没有再按数量截断）：mom_60、mom_250_20、dist_high_250、ret_skew_60、turnover_cv_20、limit_up_20、log_price、sp、goodwill_neg、roe_change_250、gm_change_250、yoy_equity、rev_growth_accel_60。资金流和研发强度因覆盖不足剔除，另有 8 个因相关性剔除。
- **B 没有做 `all_industry` 对照**，改为“只用 ETF 自身价量”的对照（与 §4.3 的预算描述一致）。三个 B 配置都以 v2 为底、只在特征上不同；headline 和验收只看真实 ETF 段（真实 ETF 数 ≥ 10 的日期，2018-01-19 起），跟踪指数替代段作为附录。B 的验收新增真实段 RankIC ≥ 0.03。映射特征覆盖低于 0.7 的被剔除（两种映射下的资金流，basket 映射下还有 ep 和 ep_hist_z）。
- **B 的特征层偏差报告在构建时计算**（不用收益），写入 `etf_industry_meta.json` 的 `agreement_on_real_etf_rows`：两种映射同名特征在真实 ETF 行上的平均截面秩相关为 0.69–0.91，basket 映射在真实行上的覆盖为 0.70–0.84。
- **B trial 使用的行业特征文件**是修正前的构建（industry_map sha256 `cb2f75c7…`）。之后修正了行业间排名的浮点打平问题（排名前取整到 1e-10），重建后 `0d197244…` 只有 `ind_limit_up_20` 变化（12.5% 的格子，新旧相关 0.9975），没有重跑。
- **D 的 `basis_head` 没有实现**，配置校验会拒绝 `basis_head: enabled`。D v2 显示部分对冲（0.5）虽然收益转正，但波动 7.8% 超过 6% 上限，基差头没有可行的落点，所以 D 的第二个 trial 用于换 A v4 的多头，而不是基差头。D 降级为 A 的风险管理模块（D-32）。
- `docs/mandates/mandate-specs` §6 的基差方向写反了：对“多股票、空期货”的组合，贴水是成本，已在 18 中更正。

### 8.3 结论

- **A**：v5 的 RankIC 略有提高（5 日 0.056 → 0.058–0.059），但组合 IR 全部低于 v4（1.09 → 事件 0.96、因子 0.84、两者合并 0.57、csi500 训练池 0.81），主要差在 2021–2022 年。7 年样本下 IR 的标准误约 0.38，单独一类特征的下降在噪声内，合并版下降接近显著。A 保持 v4。A 的 N = 9，零假设下最优 IR 期望 0.585，v4 折扣后 0.50。
- **B**：真实 ETF 段上行业映射版 RankIC 最高（0.061，对照 0.033，basket 0.026），组合 IR 反而是对照最高（0.78，行业映射 0.61，basket 0.51）；三者都未通过 Sharpe ≥ 0.8 和回撤不差于等权。basket 映射在 RankIC 和 IR 上都低于行业映射，前视没有造成虚高；“basket − industry” 的 IR 差 −0.10、RankIC 差 −0.035，没有触发“只能作为研究结果”的条件。
- **D**：精确段（2019-04-19 起）对冲后年化 −2.6%，基差是主要拖累（年化约 −6.3%，多头超额 +6.1%）。
