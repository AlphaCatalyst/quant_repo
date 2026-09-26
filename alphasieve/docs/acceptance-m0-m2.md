# 验收记录：M0–M2（2026-09-27）

本记录对应 [09-milestones.md](09-milestones.md) 的 M0、M1、M2。所有数字来自本机实际运行；日志在 `/data/alphasieve/logs/`，artifact 在 Ceph `/mnt/private_felixjjiang/alphasieve/artifacts/`。

## 1. 环境

| 项 | 值 |
|---|---|
| 本地热存储 | `/data/alphasieve`（SQLite 状态库、panel、因子缓存） |
| Ceph 存储 | `/mnt/private_felixjjiang/alphasieve`（原始数据镜像、artifact） |
| 数据源 | BaoStock（D-18），westock-data 对账 |
| 股票池 | 中证 800（沪深 300 ∪ 中证 500），月度 PIT 成分 |
| 区间 | dev 2012-01-01 至 2022-12-31；holdout 2023-01-01 至 2026-09-25 |
| 配置版本 | splits v1、gate_policy v0、costs v1、search_space `ss-ashare-daily-v1@1`、派生变量 dv1 |

## 2. 数据（M1）

| 数据集 | 结果 |
|---|---|
| 成分快照 | 354 个月度快照（2012-01 至 2026-09，两个指数），涉及 1,780 只股票 |
| 个股日线 + 复权因子 | 1,780 只，5,731,851 行，0 个失败；约 440 MB，已镜像到 Ceph |
| 指数日线 | 沪深 300、中证 500、中证 800 |
| 季度财务 | profit / growth 共 3,560 个文件，0 个失败 |
| dev panel | 4,203,232 行，1,741 只股票，签名 `2fad39c1d5d21cf1…`，质量检查全部通过 |
| holdout panel | 5,720,385 行，1,780 只股票，签名 `598549cf0b2b0ab4…`，目录权限 0700，质量检查全部通过 |

dev 质量检查：股票池规模最小 473（2015 年 7 月股灾停牌期间），收盘价覆盖率 100%，`label_5d` 覆盖率中位数 99.9%，异常率 0，成分快照缺失月份 0。股票池内 2013 年起财务字段覆盖率 100%。

同步耗时：核心数据约 66 分钟（BaoStock 对并发有限速，单进程每只股票约 12 秒），财务数据约 2 小时 25 分钟；panel 构建约 3.5 分钟，峰值内存约 7.3 GB。

## 3. 测试

| 集合 | 结果 | 覆盖 |
|---|---|---|
| 合成数据（`uv run pytest`） | 69 通过 | T0 静态检查（ruff）、T1 算子与指标对照、T2 不变量、T4 CLI 全链路、T6 植入信号与零假设 |
| 真实数据（`ALPHASIEVE_RUN_REALDATA=1 ALPHASIEVE_RUN_NETWORK=1`） | 21 通过 | T3 已知事件与跨源对账、T5 金标对照、财务 PIT、holdout 权限 |

关键不变量（T2）：截面置换标签后 IC 降到 0 附近；8 种表达式截断后历史值不变；在窗口外注入极端标签不影响指标；同一输入 artifact 哈希相同；ledger 篡改可检出；重复提交计入 trial 数。

已知事件（T3）：2015-07-08 中证 800 停牌比例超过 20%；2020-02-03 开盘跌停比例超过 30%；国庆休市；中国平安 2013–2022 年初均在沪深 300；沪深 300 成分变化 70% 以上集中在 1/7 月快照；推算涨停价与实际涨停收盘价的一致率超过 95%；茅台除息日复权收益连续。westock-data 对账：10 只股票的日价格变动（按比例缩放后）与成交量一致。

金标对照（T5）：10 个经典因子的 dev RankIC 均值与独立实现（按股票分组滚动 + scipy spearman）一致，误差小于 1e-6。

## 4. 端到端（M2）

种子库：18 个经典量价因子（dev，`label_5d`）。

| 因子 | IC 均值 | ICIR |
|---|---|---|
| 量价背离 20 日 | 0.0375 | 0.322 |
| 最大日收益 20 日 | 0.0406 | 0.280 |
| 收盘偏离 vwap 5 日 | 0.0362 | 0.278 |
| 5 日反转 | 0.0372 | 0.236 |
| 20 日换手 | 0.0422 | 0.232 |
| 20 日波动 | 0.0354 | 0.189 |
| 动量（120 日跳过 20 日） | −0.0013 | −0.008 |
| 流通市值 | 0.0044 | 0.025 |

（完整列表见 `alphasieve library list`。）结果与 A 股已知规律一致：短期反转、低波动、低换手、彩票效应、量价背离有效，动量无效，中证 800 内规模效应弱。

正式评估（agent 角色，`factor eval`）：

| 候选 | 结果 | 未通过的检查 |
|---|---|---|
| `reversal_excess_3d`（3 日超额收益反转） | evaluation_failed | ICIR 0.231 < 0.25；与 5 日反转种子相关 0.713 > 0.60 |
| `turnover_adjusted_reversal` | evaluation_failed | ICIR 0.177；与 5 日反转种子相关 0.805 |
| `reversal_excess_3d`（重建 panel 后复评，campaign `acceptance-m0-m2`） | evaluation_failed | 同上，指标与首次完全一致 |

ledger：6 条记录、3 个 trial（2 个不同候选），`ledger verify` 通过，无未完成 trial。

L2 路径演示（临时状态库、空因子库，不写入正式 ledger）：量价背离 20 日通过 L0、L1（ICIR 0.322），L2 结果为 `robust_failed`——四个子窗口 IC 同号、中性化后保留 78% 的 IC、ridge 边际贡献 +0.0042，但每周调仓持有前 20% 的组合扣除成本后年化超额为 −1.5%。

## 5. Gate 校准

`alphasieve gate calibrate --random 200`（dev，`label_5d`，gate_policy v0），artifact `cda47e717ca799c9574cd077`；耗时约 101 分钟，峰值内存约 4.6 GB。

| 项 | 结果 |
|---|---|
| 零假设模拟：200 个随机表达式的 L1 通过率 | 1%（2 个） |
| 随机表达式 ICIR 分位数 | 中位数 −0.018；90% 0.203；95% 0.234；99% 0.278 |
| 主要失败原因 | ICIR 不足 193 次、方向不符 172 次、与库高相关 55 次、覆盖率不足 24 次 |
| 植入信号检出率（每档 5 次） | IC 0.01：0%；0.02：0%；0.03：100%；0.05：100% |
| 种子因子 ICIR 分位数 | 25% 0.114；中位数 0.177；75% 0.231 |
| 工具给出的 `min_icir` 建议值 | 0.186（种子中位数，仅供参考） |

解读：

- 随机表达式由真实字段拼成，不等于纯噪声；通过 L1 的两个（`neg(ts_max(vwap_dev,10))`、`sub(ts_rank(np_margin,60),ts_zscore(close,5))`）与已知效应相关。因此 1% 应理解为“盲目搜索命中 L1 的比例”，而不是严格的误报率。
- 当前 `min_icir = 0.25` 高于约 80% 的种子因子，对真实因子偏严；但若降到 0.186，随机表达式中 ICIR 超过该阈值的比例将超过 10%（0.186 低于随机分布的 90% 分位 0.203），实际通过 L1 的比例还取决于方向、覆盖率和库相关性检查。降低 L1 阈值会把更多筛选压力交给 L3 的搜索折扣。
- 是否发布 `gate_policy.yaml` v1、选多少阈值，由人决定（见 [10-decisions.md](10-decisions.md) Q-3）。
- 校准耗时主要花在每个候选与 18 个库因子逐一计算相关性上，后续可对库因子的截面排名做缓存。

## 6. 已知局限与后续

- 股票池只有中证 800；行业分类非 PIT；涨跌停价为推算值（D-18）。
- gate_policy 仍为 v0 占位阈值；v1 需要人工根据校准报告决定。
- 未实现：E2 模板、Alpha158 / GTJA191 种子库、fresh 区间与每日增量调度、SQLite 定时备份（随 M3 orchestrator）。
- 发现并修复的问题：BaoStock `get_data()` 与新版 pandas 不兼容（改为逐行读取）；gate 校准时共享求值上下文导致内存持续增长（改为每个表达式独立上下文）。

## 7. 复现

```bash
cd alphasieve
uv run alphasieve data sync --dataset core --workers 6 --json
uv run alphasieve data sync --dataset financials --json
uv run alphasieve data build-panel --json
uv run alphasieve library seed --json
ALPHASIEVE_ROLE=agent uv run alphasieve factor eval examples/reversal_excess_3d.yaml --json
uv run alphasieve ledger verify --json
uv run alphasieve gate calibrate --random 200 --json
uv run pytest && ALPHASIEVE_RUN_REALDATA=1 ALPHASIEVE_RUN_NETWORK=1 uv run pytest tests/test_real_data.py
```
