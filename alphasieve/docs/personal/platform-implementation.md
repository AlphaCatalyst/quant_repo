# 28 · 广义平台的实现评估：快速可做项与重构判断

状态：R-1、R-2、Q1–Q6 已实现（2026-10-03），实现状态与偏差见 §9；§5、§6 未实现。评估日期 2026-10-03。依据为 [broad-quant-platform.md](broad-quant-platform.md) 的能力清单与当前代码（`src/alphasieve/`，约 1.7 万行）。只交付本文，不改代码、配置或预算。工作量是单人估算，不含 review 与真实数据回补。

## 1. 结论

- **不需要大重构，以新增模块为主。** CLI 注册与角色检查、SQLite 迁移、只追加表加哈希链、artifact、只读 Web API、agent 执行器这些基础设施都可以直接扩展。
- **先做两处小的抽取，再开始新增：**
  1. 把哈希链的追加与校验抽成公共函数，供新账本使用；
  2. 把 agent 执行器里写死的工具白名单和联网开关改为可配置的 profile。
- **不动的部分：** `evaluation/`、`gates/`、`ledger/ledger.py` 的 trial 哈希格式、`backtest/`、`training/`、`fresh/`。论点研究与它们并列，不经过因子 gate，也不改变已有记录的哈希。
- **快速可做（约 2–3 周）：** 预测账本与结算、论点对象与情景计算、决策日志、持仓导入与体检 v0、单次运行的 Researcher / Reviewer agent、对应的只读页面。

## 2. 现有代码的可复用度

| 模块 | 现状 | 对新能力的意义 |
|---|---|---|
| `cli/registry.py` | `@command(name, roles=...)` 装饰器注册，统一 JSON 信封与角色检查 | 新命令直接加 `cli/commands_*.py`，不需要改框架 |
| `state/db.py` | `MIGRATIONS` 列表顺序追加；已有大量只追加表，用触发器禁止 UPDATE / DELETE | 新表按同样方式追加迁移 |
| `ledger/ledger.py` | trial 哈希链，`HASHED_COLUMNS` 按因子 trial 字段写死（factor_id、candidate_hash、gate_results 等） | **不适合**直接存预测或决策；也不应改动，否则旧记录的哈希校验失效 |
| `fresh/service.py::append_ledger` 与 `forward_ledger` 表 | 通用结构：`record_kind`、`actor`、`payload_json` 加哈希链 | 结构可借鉴，但语义属于前瞻验证；且目前**没有对应的校验函数** |
| `fresh/paper.py` | paper 日的 `row_hash` 又是一套独立的链 | 与上面两套合计三种哈希链实现 |
| `decisions` 表 | 记录人工对因子、holdout 等对象的审批决定 | 名字容易与"交易决策日志"混淆，新表应另起名 |
| `approvals.py` | SSH 签名审批、证据快照 | 以后若要对大额论点仓位做签名确认，可以复用 |
| `agents/executors.py` | Codex / Claude 子进程执行器；`CLAUDE_ALLOWED`、`CLAUDE_DENIED` 模块级常量写死，禁止 WebSearch / WebFetch；Codex 的 `network_access=false` 写死 | 执行器本身可复用，工具白名单和联网开关需要参数化 |
| `agents/orchestrator.py`、`agents/workspace.py` | 多 turn 循环；brief 由因子库与漏斗生成，按 `robust_passed` 统计进展 | 与因子挖掘强耦合；论点研究先不用这套循环，改为单次运行 |
| `agents/integrity.py` | 扫描 agent 命令与读取路径，检测越权 | 新 agent 角色需要各自的规则 |
| `data/`（westock、baostock、新浪、东方财富、交易所、中证、通达信 provider） | 行情、财报、研报文本、两融、期货日线 | 持仓体检、财报排雷、卖方观点审计都能直接用；新浪 `daily_bars(symbol)` 是通用接口，生猪等商品合约需要实测 |
| `data/reports.py`、`data/report_features.py` | 研报正文解析出 EPS 预测与评级 | 卖方观点审计的现成起点 |
| `web/app.py` 与 `frontend/src/pages/` | FastAPI 只读接口、9 个 React 页面 | 按同样模式加页面；保持不新增网页写接口 |

## 3. 建议的两处前置重构

### R-1 哈希链公共函数（约 0.5–1 天）

新增 `ledger/chain.py`，提供 `append_chained(conn, table, row, hashed_cols)` 与 `verify_chain(conn, table, hashed_cols)`：在同一写事务里读取上一行哈希、计算并插入。

- 新账本（预测、决策日志）只用它，不再复制第四、第五套实现。
- 顺带给 `forward_ledger` 补上校验函数，计算方式与 `fresh/service.py` 现有写法一致，不改已有记录。
- trial 账本保持原样，不迁移。

### R-2 agent profile 参数化（约 1–2 天）

把执行器里写死的部分改为 `AgentProfile`：允许的工具、可写目录、是否联网、prompt 模板。现有因子挖掘的 Miner profile 与当前常量逐项一致，用测试锁定命令行参数不变。

- 新增 Researcher profile：允许联网检索，只能写 `theses/` 下的草稿，仍不能写状态库。
- 新增 Reviewer profile：只读论点目录，允许联网，只写复核意见。
- `orchestrator` 的多 turn 循环暂不泛化，论点研究先用单次运行。等 Monitor 这类需要定时运行的角色出现后，再考虑抽象。

是否允许联网检索由 human 决定（[broad-quant-platform.md](broad-quant-platform.md) §10 第 4 项）。未决定前，Researcher 只能使用本地数据和 westock 研报。

## 4. 快速可做项

按依赖顺序排列。每项都有合成数据测试，不读 holdout/fresh。

| 序号 | 能力 | 实现要点 | 估算 | 依赖 |
|---|---|---|---|---|
| Q1 | 预测账本与结算 | 新表 `forecasts`（只追加）；命令 `forecast add / list / settle / score`；结算数据源限定为已有的行情、指数、期货 panel，登记时写明数据源与口径；计算 Brier 分数与分档校准表 | 2 天 | R-1 |
| Q2 | 论点对象与情景计算 | 论点用 YAML 存在仓库的 `theses/` 目录（与 `campaigns/` 同样入 git）；schema 校验每条证据带出处与 A/B/C/D 等级，以及证伪条件、仓位上限；估值公式用受限的算术表达式（只允许四则运算和参数名），后端计算情景矩阵和单参数敏感度；命令 `thesis validate / scenarios / show` | 2–3 天 | — |
| Q3 | 决策日志 | 新表 `journal_entries`（只追加）；命令 `journal add / list`；关联论点与预测；记录当时的持仓快照 | 1 天 | R-1、Q4 |
| Q4 | 持仓导入与体检 v0 | 从券商导出的 CSV 导入持仓（字段映射可配置）；用现有 panel 计算行业集中度（申万当前快照，注明非 PIT）、市值分布、60 日 beta、持仓相关性、简单压力情景 | 2–3 天 | — |
| Q5 | Researcher / Reviewer 单次运行 | 命令 `thesis draft --topic ...`、`thesis review <id>`；产物落到 `theses/drafts/`，由人审阅后才进入正式目录；transcript 与费用照常记录 | 2 天 | R-2，以及联网决定 |
| Q6 | 只读页面 | 论点列表与详情（证据表、情景矩阵、关联预测）、预测校准、持仓体检 | 2–3 天 | Q1–Q4 |

样例验收：把猪周期报告的核心参数录成第一个论点（牧原、东瑞），登记 2–3 个可结算预测，例如生猪期货某合约的结算价区间、牧原月度销售简报的出栏量区间；情景矩阵要能复现报告中"广发参数代入得 41.99 元"这一类关键数字。

## 5. 中期项（各约 1–2 周）

| 能力 | 依赖与要点 |
|---|---|
| 财报排雷 | westock 全报表已在 panel；规则指标（应收与存货异常、利润与现金流背离、商誉占比、审计意见）由后端计算，agent 写解读 |
| 卖方观点审计 | 基于 `data/reports.py` 的 EPS 预测解析，按分析师与券商统计预测误差和修正方向；可派生 `wr_*` 因子进入因子 campaign |
| 公告与纪要文本管线 | 需要新增公告来源 provider（巨潮或交易所），原文落盘并记录 provenance；结构化抽取的结果带原文定位 |
| 周期行业监控 | 需要行业数据（生猪价格、能繁母猪存栏、商品期货曲线等）；Monitor 定时运行，对照论点的证伪条件告警，这一步需要泛化 orchestrator 的调度 |
| 风险模型 v1 | 按 [risk-model.md](../mandates/risk-model.md) 实现后，替换持仓体检 v0 的简化暴露 |

## 6. 长期项

个人账户 mandate 与新资产类型（可转债、跨资产 ETF、商品期货 CTA，见 [personal-account.md](personal-account.md) §5）、特殊情况扫描、宏观配置、期权辅助、港股通、执行接入。这些需要新的数据资产类型或回测适配，按 mandate 单独记预算。

## 7. 命名与边界约定

- 新表不复用 `decisions`、`trials`、`forward_ledger`；分别命名为 `forecasts`、`journal_entries`、`theses_index`（若需要索引）。
- 新代码放在新包里：`thesis/`（论点、情景、预测）、`portfolio_book/`（持仓与体检）、`journal/`，避免与现有的 `strategy/portfolio.py`（策略回测的组合构建）混淆。
- 论点研究的结论不写入因子库，也不触发 holdout、review 或 paper 状态变化；需要进入系统化研究时，按现有流程另开 campaign 或 TrainingTask。
- 持仓与成交属于个人隐私数据：只存本机状态库，不进入 git，不上传到平台集群。

## 8. 待人工决定的问题

1. 是否先做 R-1、R-2 两处前置重构，再做 Q1–Q6。
2. Researcher 是否允许联网检索（决定 Q5 的范围）。
3. 持仓导入使用哪家券商的导出格式。
4. 第一个论点样例是否用猪周期，以及首批预测的结算数据源。

## 9. 实现状态（2026-10-04）

按"所有操作都允许"的授权，§8 的四项按默认值执行：先做 R-1、R-2；Researcher 与 Reviewer 允许联网；持仓导入用通用中英文表头映射；第一个论点用猪周期。

| 项 | 代码 | 命令 |
|---|---|---|
| R-1 | `ledger/chain.py`（`append_chained`、`verify_chain`）；`fresh/service.py::verify_forward_ledger` 按原格式校验 `forward_ledger` | — |
| R-2 | `agents/executors.py::AgentProfile`、`MINER` / `RESEARCHER` / `REVIEWER`；Miner 命令行与重构前逐字节一致（测试锁定）；`agents/integrity.py` 拦截 agent 访问持仓与日志 | — |
| Q1 | `forecasts/`，表 `forecast_ledger`（registered / settled / voided 事件，哈希链） | `forecast add / settle / void / list / show / score / verify` |
| Q2 | `thesis/`（`model.py`、受限公式 `formula.py`、`scenarios.py`）；样例 `theses/hog-cycle-muyuan.yaml` | `thesis validate / scenarios / implied / show / list` |
| Q3 | `journal/`，表 `journal_entries` | `journal add / list / show / verify` |
| Q4 | `portfolio_book/`（导入、行情、体检、日度持仓与净值、仓位/行业/论点归因、持仓约束提示），追加表 `holdings_snapshots`、`book_cashflows`；映射与限制在 `configs/book/` | `book import / list / show / check / history / attribution / rebalance / cashflow`（仅 human、system） |
| Q5 | `thesis/agent_run.py`：单次运行的临时 git 工作区 | `thesis draft / review / runs / run-show`（draft、review 仅 human） |
| Q6 | `web/app.py`：`/api/theses`、`/api/theses/{id}`、`/api/forecasts`、`/api/book` 及只读的 `/api/book/history`、`/api/book/attribution`、`/api/book/rebalance`；前端页面 `Theses`、`Forecasts`、`Book` | — |

持仓跟踪的口径：`book history [--start --end --account --benchmark]` 以相邻券商快照之间数量不变、收盘价估值，数量差记在后一个快照日为**推断交易**；导出现金存在时沿用该金额。`book cashflow add --file flows.csv`（列 `account,as_of,amount,note`，流入为正）或单笔 `--account --as-of --amount` 登记外部现金流，日收益扣除当日登记流入后连乘为时间加权净值；未登记流入无法与交易区分。基准优先用本地沪深 300 全收益指数，缺失回退价格指数，并在报告注明来源。`book attribution --start --end --by position|industry|thesis` 的个股贡献按前一日仓位的价格变动计算；行业用持仓日可得的申万一级历史，基准权重取区间起点前最近的中证官方权重，缺失时可回退 DoltHub；成分股收盘价优先读取日更的 `raw/baostock/daily`，缺文件回退 `baostock_all/daily`，缺少行业收益数据时效应留空；论点按 YAML 证券关联及明确指向证券的日志关联，未关联列为 core。归因的 `risk_model_hook` 提供当前简化风格暴露，**不是 rm1 历史因子收益归因**。`book rebalance [--snapshot id]` 只根据已登记的论点仓位上限和 `configs/book/limits.yaml` 限额、显式目标权重给出差额提示；无目标权重不推断加仓。所有新报告保存在 `<hot_root>/book/reports/`，网页仅显示已保存报告；`/api/book` 系列接口与看板其他页面使用同一认证开关（`ALPHASIEVE_WEB_AUTH`；当前部署为免登录，2026-10-07），系统不生成订单。

`book check [--announcement-lookback-days 30]` 在保存的 JSON 中增加财务红旗（股票琥珀/红色、触发规则及证据期间、占总组合权重）、近期高/中重要性公告（类型、标题、日期、原文 URL；可转债同时关联转债代码及正股代码）和当前风格暴露。可转债下修、强赎、回售从本地公告元数据补录，即使共用分类器将回售评为低重要性。风格只用 book 行情与当前报价：股票原始对数市值、配对 beta、年化波动、60 至 20 日价格动量、当前 PB 倒数对数；基准可比的只有 beta=1 与指数波动。缺失项记空，未用研究 panel，未取得 rm1 所需的全市场截面、252 日动量、成交额与历史 PB，故标记 `book_simplified_not_rm1`，不可用作 rm1 风险约束。

与前文设计不同或补充的地方：

- **包名**：预测放在 `forecasts/`，没有并入 `thesis/`，因为预测也可以不挂论点。
- **持仓行情与研究隔离**：持仓体检不读研究 panel；股票和基准日线以 BaoStock 为主，ETF、可转债日线用 westock-data，报价快照按日缓存在 `<hot_root>/book/market/`。导入市值仍是组合权重口径，报价只作参考。行业优先用最新申万一级快照，缺失时回退 BaoStock 证监会分类，标注 "current snapshot, not PIT"。股票按流通市值（缺失回退总市值）分为 <50亿、50–200亿、200–1000亿、≥1000亿，缺值单列未知；ETF/转债不混入股票市值档。转债标记阈值：转股溢价率 >50%、剩余规模 <3 亿元、剩余期限 <1 年、评级低于 AA-；缺失值不推断。
- **预测结算**：结算结果由后端按条件计算，调用方只能提供观测值与来源（`manual`），或由 `sina_futures_close`、`stock_close` 自动取数；结算日前、重复结算、作废后结算一律拒绝。
- **写入范围的强制**：Codex 沙盒只能限制整个工作区，不能限制子目录。论点 agent 运行结束后提交工作区，用 git diff 检查改动路径，越出 `drafts/` 或 `reviews/` 即判为 rejected；草稿不会自动复制进 `theses/`。
- **猪周期样例的口径**：广发 2027 参数（猪价 14、成本 11.2、出栏 0.78 亿头、均重 120、归母折算 0.925、PE 10）得 41.99 元，与报告一致；42.59 元市价隐含猪价 14.04 元/kg。原文参数在 0.925 归母折算下为 115.36 元，原文"约 120 元"未做折算；报告给出的隐含猪价 14.34 元用的是成本 11.5 元。两处口径差已写入样例的 `revisions`。
- **预测草案**：样例中的 `proposed_forecasts` 只是草案，结算日需按数据源日历复核后再由人登记。

验证：2026-10-04 全量 `uv run pytest -q` 为 404 passed、22 skipped；`npm run build` 通过。持仓跟踪用临时热根导入 2026-09-28 至 09-30 三份 `mixed.csv` 快照并读取真实收盘价：期初/期末市值 6,815.50/6,874.50 元，时间加权收益 0.866%，沪深 300 全收益 0.411%，推断 9 月 30 日浦发银行增加 10 股。基准行业权重取 2026-08-31 官方快照；本地成分股日线只到 2026-09-24，该区间配置/选择效应为空（报告标记覆盖不足）。此前在临时目录中还走通论点校验与反推、预测登记与提前结算拒绝、持仓导入与真实 BaoStock 行情体检、agent 角色拒绝、决策日志与哈希链校验。尚未用真实模型运行 `thesis draft / review`。
