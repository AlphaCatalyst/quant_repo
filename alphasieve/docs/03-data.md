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

### 1.1 数据源分工

| 来源 | 角色 | 说明 |
|---|---|---|
| Tushare Pro | 主源（结构化行情、财务、成分、行业） | 部分接口（如 `stk_limit`、`index_weight`、财务报表）需要相应积分权限，接入前确认账号权限与调用频率上限 |
| westock-data（腾讯自选股数据，本机已有 skill） | 交叉校验源；后续事件数据源 | `kline` 支持按日期范围拉取，可抽样对账价格与成交；研报、公告、新闻用于后续事件驱动。批量拉取能力与历史指数成分的 PIT 口径需实测 |
| Qlib 社区 A 股数据 | 快速启动与交叉校验 | 可用于在 Tushare 接入完成前先跑通 M2；财务 PIT 与可交易性字段不完整，不作为权威源 |
| 商业数据（Wind、聚源、米筐等） | 可选升级 | 若 Tushare 权限或质量不足再评估 |

原则：同一字段只有一个权威源；其他来源只做对账（见 [12-testing.md](12-testing.md) T3）。

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

## 7. 数据量级

按 2012-01 至 2026-09 估算：约 3,580 个交易日；历史上出现过的股票约 6,200 只（含退市），每日平均在市约 3,900 只，合计约 1,400 万个股票日。

| 数据 | 规模估算 | 压缩后（Parquet + zstd） |
|---|---|---|
| 日线 + 复权 + 每日指标 + 涨跌停 + 停牌（约 40 列） | 1,400 万行，未压缩约 4.5 GB | 约 1–1.5 GB |
| 财务报表与指标（约 6,000 只 × 60 期 × 3 张表） | 约 36 万份报告，未压缩约 1.3 GB | 约 0.2–0.4 GB |
| 指数成分与权重、行业分类 | 数十万行 | < 50 MB |
| 合并后的主 panel（全市场，约 80 列 float32） | 内存约 4.5 GB | 约 2 GB |
| 主 panel（沪深 300 / 中证 500 / 中证 1000 并集，约 1,800 只） | 约 640 万行，内存约 2 GB | 约 0.8 GB |
| 单个因子值（全市场 float32） | 约 56 MB | 约 30–40 MB |
| 因子值缓存（库成员 + `robust_passed`，按 500 个计） | — | 约 15–20 GB |
| trial ledger（10 万条） | — | 约 200 MB |
| 评估 artifact（10 万条 × 50–200 KB） | — | 约 5–20 GB |
| agent transcript（1 万个 turn × 1–5 MB） | — | 约 10–50 GB |

结论：

- 第一阶段总量在 100 GB 量级以内，单机即可承载；62 GB 内存足以把股票池 panel 常驻内存，32 核可以并行评估。
- 单次因子评估（640 万行、含滚动算子）在秒级；500 次 trial 在分钟到十分钟级，计算不是瓶颈，LLM 调用才是。
- 候选的因子值不全部落盘：只缓存因子库成员与 `robust_passed` 候选，其余需要时按表达式重算（确定性保证结果一致）。

后续扩展的量级（不在第一阶段）：分钟线约 5,000 只 × 240 根 × 243 日 × 10 年 ≈ 29 亿行，压缩后约 100 GB；Level2 在 TB 级；公告、研报、新闻文本在数十 GB 级。这些需要放在 Ceph 并配合分区读取。

## 8. 存储分层

沿用 scicomp-foundry 的分层做法（`/data/codebase/scicomputing/scicomp-foundry/docs/32-storage-layout.md`）：

| 层 | 内容 | 位置 | 理由 |
|---|---|---|---|
| git | 代码、文档、配置、gate policy、模板、金标快照 | 仓库 | 可审阅、可回滚 |
| 本地热存储 | 工作 panel（dev / holdout / fresh 分目录）、因子值缓存、SQLite 状态库、campaign workspace、临时文件 | `/data/alphasieve/`（本地盘，当前剩余约 580 GB） | 随机读写频繁；SQLite 需要可靠的文件锁；holdout 权限隔离依赖本地文件权限 |
| Ceph | 原始数据权威副本（Tushare raw 快照）、artifact、agent transcript、SQLite 定时备份、日报与批次报告 | `/mnt/private_felixjjiang/alphasieve/` | 容量大、可跨机器访问、适合一次写入多次读取 |
| taijifs | 发布归档：因子库版本快照、批次报告合集 | `/taijifs_zw35/r2/felixjjiang/alphasieve/` | 长期保存 |

要点：

- **SQLite 不放 Ceph**：ceph-fuse 上的文件锁与 WAL 不可靠。状态库放本地，每小时用 SQLite 在线备份写一份到 Ceph。
- **热 panel 不直接从 Ceph 读**：从 Ceph 的原始副本构建，物化到本地盘；Ceph 挂载异常时评估仍可继续。
- **挂载保护**：写 Ceph 的目录采用 bind mount，并在挂载点上设置不可写保护（与 scicomp-foundry 相同），Ceph 不在时写入直接失败，避免悄悄写回本地盘造成数据分叉。
- **holdout / fresh 的备份**：在 Ceph 上以 system 用户可读的权限单独存放；agent 用户所在的环境不挂载该目录。
- 存储根目录由环境变量配置：`ALPHASIEVE_HOT_ROOT`、`ALPHASIEVE_STORE_ROOT`、`ALPHASIEVE_ARCHIVE_ROOT`；开发时默认指向仓库内的 `data/`、`artifacts/` 等目录。
