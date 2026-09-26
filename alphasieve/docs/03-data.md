# 03 · 数据

数据是整个系统的真值来源。本文件定义第一阶段用哪些数据、怎么构建 panel、PIT 与可交易性规则，以及开发 / 留出 / 前瞻三个区间如何划分与隔离。

## 1. 数据源

第一阶段以 Tushare Pro 为主数据源，接口层抽象为 `Provider`，后续可替换或补充自有数据库。

| 数据 | Tushare 接口 | 用途 |
|---|---|---|
| 股票列表、上市/退市日期 | `stock_basic` | 股票池、上市天数过滤、幸存者偏差处理 |
| 名称变更（ST 标记） | `namechange` | ST / *ST 过滤 |
| 日线行情 | `daily` | 开高低收、成交量额 |
| 复权因子 | `adj_factor` | 后复权价格计算 |
| 每日指标 | `daily_basic` | 换手率、市值、估值 |
| 涨跌停价 | `stk_limit` | 可交易性（涨停买不进、跌停卖不出） |
| 停复牌 | `suspend_d` | 可交易性 |
| 指数成分与权重 | `index_weight` | 沪深 300 / 中证 500 / 中证 1000 的历史成分（PIT） |
| 指数日线 | `index_daily` | 基准收益 |
| 行业分类 | `index_classify` + `index_member`（申万） | 行业中性化、行业暴露 |
| 财务指标与报表 | `fina_indicator`、`income`、`balancesheet`、`cashflow` | 基本面因子（按公告日 PIT） |
| 业绩预告 / 快报 | `forecast`、`express` | 后续事件驱动（第一阶段只落库） |

每个数据集的拉取记录（接口、参数、拉取时间、行数、哈希）写入 `data_snapshots` 表，作为 provenance。

## 2. Panel

### 2.1 主 panel

- 粒度：交易日 × 股票，Parquet 按年份分区。
- 主键：`trade_date`、`ts_code`。
- 字段分组：

| 组 | 字段示例 |
|---|---|
| 价格 | `open`、`high`、`low`、`close`、`vwap`（后复权）、`adj_factor` |
| 量额 | `volume`、`amount`、`turnover_rate`、`turnover_rate_f` |
| 规模估值 | `total_mv`、`circ_mv`、`pe_ttm`、`pb`、`ps_ttm` |
| 可交易性 | `is_suspended`、`is_limit_up_open`、`is_limit_down_open`、`is_st`、`days_listed`、`tradable_buy`、`tradable_sell` |
| 分类 | `sw_l1`、`sw_l2` |
| 成分 | `in_hs300`、`in_zz500`、`in_zz1000`（当日有效成分） |
| 基本面（PIT 对齐后） | `roe_ttm`、`gross_margin`、`revenue_yoy` 等 |

### 2.2 标签

- 默认标签：`ret_{h}d_open_to_open` = T+1 开盘买入、T+1+h 开盘卖出的收益，h ∈ {1, 5, 10, 20}。
- 买入日若 `tradable_buy = false`（停牌、开盘涨停），该样本标签记为缺失，不参与评估。
- 标签的截面变换（排名、标准化）在评估层完成，panel 只存原始收益。

### 2.3 股票池

- 每个交易日的股票池 = 当日有效指数成分 ∩ 非 ST ∩ 上市满 60 个交易日 ∩ 当日未停牌。
- 已退市股票保留在历史 panel 中，避免幸存者偏差。

## 3. PIT 与可交易性规则

| 规则 | 实现 |
|---|---|
| 财务数据 | 以公告日 `ann_date` 为可得日；可得日为交易日且公告在收盘后时，从下一个交易日起可用。第一阶段统一按“公告日次一交易日起可用”保守处理 |
| 财务重述 | 使用首次公告值；重述值以其自身公告日生效 |
| 指数成分 | 使用 `index_weight` 当期快照，不用最新成分回填历史 |
| 行业分类 | 使用分类的生效起止日期 |
| 复权 | 后复权价格用于收益计算；因子若使用价格水平（非比率），必须在 DSL 中显式声明 |
| 全样本统计 | 禁止；标准化、去极值只能在截面内或用过去窗口完成（L0 检查） |

## 4. 数据区间与隔离

| 区间 | 默认范围 | 可访问角色 | 用途 |
|---|---|---|---|
| dev | 2012-01-01 至 2022-12-31 | agent、human、system | L1、L2、L3 的全部计算；agent 循环只在这里 |
| holdout | 2023-01-01 至项目启动日 | system（评估）；human 只看评估结果 | L4 锁定留出，按批次开启，读取计预算 |
| fresh | 项目启动日之后 | system（评估）；human 只看评估结果 | L5 前瞻验证，不回填 |

- 区间边界写在 `config/splits.yaml`，项目启动时由 human 确认并锁定；锁定后修改需要人工审批并记录，且会让已有的 holdout 证据全部标记为 `contaminated`。
- dev、holdout、fresh 分别物化到不同目录（见 [02-architecture.md](02-architecture.md) §4）；data 层的读取函数带角色参数，agent 角色只能拿到 dev。
- 标签跨区间：dev 末尾若干天的标签需要用到 holdout 首段价格，这些样本在 dev 评估中剔除（embargo = 最大标签周期 + 1 天）。
- 已知局限：LLM 预训练语料可能覆盖 holdout 期间的市场信息；只有 fresh 区间对此免疫。这也是 L5 不可省略的原因。

## 5. 数据质量检查

每次更新 panel 后运行，结果写入 `data_quality_reports`，前端数据页展示：

- 覆盖率：每日股票池中有行情、有标签、有基本面的比例。
- 异常：价格跳变与复权因子不一致、成交量为零但未标停牌、涨跌停价缺失。
- 时效：最新交易日与数据最新日期的差距。
- 一致性：指数成分数量与官方口径偏差。

检查失败的日期在 panel 中打标记，评估时按 `missing_policy` 处理。

## 6. 更新节奏

- 每个交易日收盘后（默认 18:00）增量拉取，更新 fresh 区间 panel。
- fresh cohort 的前瞻指标在数据更新后由 orchestrator 自动计算。
- 历史回补（例如新增字段）作为一次性任务，完成后重新生成 dev / holdout 快照签名；已有 artifact 通过快照签名判断是否受影响。
