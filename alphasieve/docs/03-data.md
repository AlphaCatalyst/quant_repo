# 03 · 数据

数据是整个系统的真值来源。本文件定义第一阶段用哪些数据、怎么构建 panel、PIT 与可交易性规则，以及开发 / 留出 / 前瞻三个区间如何划分与隔离。

## 1. 数据源

第一阶段使用免费数据源 BaoStock（无需 token），实现在 `src/alphasieve/data/providers/baostock.py`，同步逻辑在 `src/alphasieve/data/sync.py`。接口层按 provider 抽象，后续可替换为 Tushare 或自有数据库（见 [10-decisions.md](10-decisions.md) D-18）。

| 数据 | BaoStock 接口 | 用途 |
|---|---|---|
| 交易日历 | `query_trade_dates` | 标签、embargo、上市天数 |
| 股票列表、上市/退市日期 | `query_stock_basic` | 上市天数过滤、幸存者偏差处理 |
| 日线行情（不复权） | `query_history_k_data_plus`（`adjustflag=3`） | 开高低收、成交量额、换手率、PE/PB/PS、`tradestatus`（停牌）、`isST` |
| 复权因子 | `query_adjust_factor` | 后复权价格 = 不复权价格 × `backAdjustFactor` |
| 沪深 300 / 中证 500 成分 | `query_hs300_stocks(date)`、`query_zz500_stocks(date)` | 按月初快照的历史成分（PIT） |
| 指数日线 | `query_history_k_data_plus`（sh.000300 / sh.000905 / sh.000906） | 基准收益 |
| 行业分类 | `query_stock_industry` | 证监会行业，**只有当前快照，非 PIT** |
| 季度财务 | `query_profit_data`、`query_growth_data` | ROE、净利率、EPS TTM、净利润同比等，带 `pubDate`（PIT） |

每次拉取写入 `data_snapshots` 表（数据集、参数、行数、内容哈希）作为 provenance；原始数据先落本地，再镜像到 Ceph。个股日线从 2011-01-01 起拉取，为 2012 年开始的 dev 窗口提供滚动窗口预热。

### 1.1 数据源分工

| 来源 | 角色 | 说明 |
|---|---|---|
| BaoStock | 主源 | 免费；单只股票 15 年日线一次查询约 4.5 秒，同步使用 6 个进程、支持断点续传 |
| westock-data（腾讯自选股数据） | 行情的交叉校验源；全 A 三大报表、资金流向、融资融券快照的主源（D-30） | 实测 `kline` 默认返回前复权价格，现金分红按减法调整、送转股按比例缩放，指定不复权会报服务错误；因此对账比较“按比例缩放后的日价格变动”与成交量（单位为手），见 `tests/test_real_data.py`。报表与资金流向见 §1.3 |
| Tushare Pro / 商业数据 | 可选升级 | 需要中证 1000 历史成分、申万 PIT 行业、交易所涨跌停价、2020 年以前的分钟线与两融时再评估；渠道与价格见 [17-data-vendors.md](17-data-vendors.md) |

原则：同一字段只有一个权威源；其他来源只做对账（见 [12-testing.md](12-testing.md) T3）。

### 1.3 westock 报表与资金流向（D-30）

实现在 `data/providers/westock.py`（CLI 封装）和 `data/fundamentals.py`（派生字段）。原始数据放在 `data/raw/westock/`，各股票池共用。

| 数据 | 命令 | 覆盖 | panel 字段 |
|---|---|---|---|
| 三大报表 | `alphasieve data sync --dataset ws_financials --universe ashare_all` | 全 A，2000 年起 | `ws_*`：ROE、ROA、毛利率、营业利润率、经营现金流/资产、应计、资产负债率、有息负债/权益、商誉/权益、现金/资产、研发/营收（单季）、资产同比、营收 TTM 同比、单季净利同比、单季净利变化/资产，以及净利润 TTM、营收 TTM、经营现金流 TTM、归母权益四个金额 |
| 资金流向 | `--dataset fund_flow` | 全 A，2020 年起 | `mf_main_net_ratio` 等：主力（超大单加大单）、超大单、大单、中单、小单的净流入除以当日成交额 |
| 融资融券 | `--dataset margin`（当日全 A 快照）；`--dataset margin_history --universe hs300_2020 --start 2019-10-08`（周频回补） | 沪深 300 成分 2019-10 起周频；全 A 从首次运行起逐日积累 | `mg_fin_to_mv`（融资余额/流通市值）、`mg_fin_chg_4w`（融资余额 4 周变化）、`mg_fin_buy_share`（融资买入/(买入+偿还)）、`mg_short_to_fin`（融券余额/融资余额）；次日可用，最多沿用 10 个交易日 |

- 报表字段的生效日：三张表中最晚的公告日之后的第一个交易日；同比用上年同期报表计算。
- 已知局限：约三分之一的年报资产负债表是追溯调整后的数值（D-30），写入 panel `warnings`。
- 资金流向在收盘后可知，与收盘价同属当日信息；标签从次日开盘起算。
- `data daily-update` 每天增量更新资金流向和融资融券快照，周六全量刷新 westock 报表。

### 1.2 第一版的已知局限

写入 panel 的 `meta.json` 的 `warnings`：

- 股票池为中证 800（沪深 300 ∪ 中证 500）：免费源只有这两个指数的历史成分；中证 1000 待找到可靠的历史成分来源再加。
- 行业分类为证监会口径的当前快照，不是 PIT；中性化与行业暴露可能有轻微前视。
- 涨跌停价由板块规则推算，不是交易所公布值（规则见 §3）。
- 流通市值由 `收盘价 × 成交量 / 换手率` 反推，停牌日沿用最近值。

## 2. Panel

### 2.1 主 panel

- 粒度：交易日 × 股票，长表存为 `panel/<tier>/panel.parquet`，附 `meta.json`（窗口、字段、签名、warnings）与 `benchmark.parquet`；构建逻辑在 `src/alphasieve/data/panel.py`。
- 主键：`date`、`code`（BaoStock 代码格式，如 `sh.600000`）。
- 字段分组：

| 组 | 字段 |
|---|---|
| 价格（后复权） | `open`、`high`、`low`、`close`、`vwap`、`adj_factor`；不复权原值 `*_raw`、`preclose` |
| 量额 | `volume`、`amount`、`turnover_rate` |
| 规模估值 | `circ_mv`、`float_shares`、`pe_ttm`、`pb_mrq`、`ps_ttm` |
| 可交易性 | `is_suspended`、`is_st`、`days_listed`、`limit_up`、`limit_down`、`is_limit_up_open`、`is_limit_down_open`、`tradable_buy`、`tradable_sell` |
| 分类 | `industry`（证监会，非 PIT） |
| 成分 | `in_hs300`、`in_zz500`、`in_csi800`、`has_member_snapshot`、`in_universe` |
| 基本面（PIT 对齐后） | `roe_avg`、`np_margin`、`eps_ttm`、`yoy_ni`、`yoy_equity`、`yoy_asset` 及对应报告期 |
| 收益与标签 | `ret_1d`、`label_1d`、`label_5d`、`label_10d`、`label_20d` |

### 2.2 标签

- `label_{h}d` = T+1 开盘买入、T+1+h 开盘卖出的收益（后复权），h ∈ {1, 5, 10, 20}。
- 买入日若 `tradable_buy = false`（停牌、开盘涨停），该样本标签记为缺失，不参与评估。
- 标签先在全量数据上计算，再按区间末尾做 embargo（见 §4），dev 标签不会用到 holdout 价格。
- 标签的截面变换（排名、标准化）在评估层完成，panel 只存原始收益。

### 2.3 股票池

- `in_universe` = 当月中证 800 成分 ∩ 非 ST ∩ 上市满 60 个交易日 ∩ 当日未停牌。
- 已退市股票保留在历史 panel 中，避免幸存者偏差。

## 3. PIT 与可交易性规则

| 规则 | 实现 |
|---|---|
| 财务数据 | 以 `pubDate` 为公告日，从公告日之后的第一个交易日起可用 |
| 财务重述 | 每个报告期只用首次公告值；晚于更新报告期公告的旧报告期数据被忽略，数据不会“倒退” |
| 指数成分 | 每月初查询一次成分快照，适用于当月所有交易日；不用最新成分回填历史 |
| 行业分类 | 第一版只有当前快照（非 PIT，见 §1.2） |
| 涨跌停价 | 按前收盘价推算：主板 10%、ST 5%、创业板 2020-08-24 起 20%、科创板 20%、北交所 30%，四舍五入到分；上市前 5 个交易日不设限 |
| 停牌 | `tradestatus = 0` 或成交量为 0 |
| 复权 | 后复权价格用于收益计算；因子若使用价格水平（非比率），必须在 DSL 中显式声明 |
| 全样本统计 | 禁止；标准化、去极值只能在截面内或用过去窗口完成（L0 检查） |

## 4. 数据区间与隔离

| 区间 | 默认范围 | 可访问角色 | 用途 |
|---|---|---|---|
| dev | 2012-01-01 至 2022-12-31 | agent、human、system | L1、L2、L3 的全部计算；agent 循环只在这里 |
| holdout | 2023-01-01 至 2026-09-25（配置值；该区间最后一个交易日为 2026-09-24） | system（评估）；human 只看评估结果 | L4 锁定留出，按批次开启，读取计预算 |
| fresh | 项目启动日之后 | system（评估）；human 只看评估结果 | L5 前瞻验证，不回填 |

- 区间边界写在 `src/alphasieve/configs/splits.yaml`（可用 `ALPHASIEVE_CONFIG_DIR` 覆盖），项目启动时由 human 确认并锁定；锁定后修改需要人工审批并记录，且会让已有的 holdout 证据全部标记为 `contaminated`。
- dev、holdout、fresh 分别物化到不同目录（见 [02-architecture.md](02-architecture.md) §4），holdout 目录权限为 0700；`data/access.py` 的 `load_panel` 按角色检查：dev 对 agent / human / system 开放，holdout / fresh 只允许 system 读取。
- 每个区间的 panel 都包含从 2011-01-01 起的预热数据，指标只在区间窗口内计算。
- 标签跨区间：窗口末尾 `1 + h` 个交易日的 `label_{h}d` 置为缺失（embargo 按周期分别计算），dev 标签不使用 holdout 价格。
- 已知局限：LLM 预训练语料可能覆盖 holdout 期间的市场信息；只有 fresh 区间对此免疫。这也是 L5 不可省略的原因。

## 5. 数据质量检查

每次构建 panel 后运行（`data/quality.py`），结果写入 `data/quality/<tier>.json`，`alphasieve data status` 展示摘要：

- 股票池规模下限（每日 ≥ 100）。
- 覆盖率：股票池内收盘价覆盖率（≥ 99%）、`label_5d` 覆盖率中位数（≥ 90%）。
- 异常率：非正价格、单日收益超过 45%、流通市值缺失（≤ 0.1%）。
- 成分快照缺失的月份数（= 0）。
- 时效：最新数据日期与窗口最后一个交易日的差距。

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
