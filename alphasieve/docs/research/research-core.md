# 04 · 研究内核

研究内核回答“一个因子候选怎么被表达、怎么被评估、怎么被判定、结果怎么记账”。对象字段的设计定义见 [design/system-contracts.md](../../../design/system-contracts.md)，本文件补充实现细节与默认参数。

## 1. 研究对象

design 中已定义：`ResearchQuestion`、`DataContract`、`FactorSpec`、`StrategySpec`、`ExperimentRun`、`TrialLedgerEntry`、`ReviewPacket`、`PromotionRecord`。AlphaSieve 实现时新增：

| 对象 | 作用 | 关键字段 |
|---|---|---|
| `Campaign` | 一次有预算的研究活动 | `campaign_id`、`question_id`、`universe`、`horizon`、`budgets`（trial、turn、LLM 费用、时长）、`stop_conditions`、`status` |
| `Turn` | agent 的一次会话调用 | `turn_id`、`campaign_id`、`agent_type`、`model`、`prompt_version`、`started_at`、`ended_at`、`transcript_path`、`candidates_submitted`、`cost` |
| `Directive` | 人对 agent 的结构化指令 | `directive_id`、`campaign_id`、`kind`（prioritize / forbid / hint / answer）、`content`、`created_by`、`consumed_in_turn` |
| `Shortlist` | 锁定的候选批次 | `shortlist_id`、`campaign_id`、`factor_versions`、`locked_at`、`memory_frozen_at` |
| `HoldoutRequest` | 打开 holdout 的申请 | `request_id`、`shortlist_id`、`reads_requested`、`status`、`approved_by`、`reason` |
| `FreshCohort` | 一批进入前瞻观察的对象 | `cohort_id`、`members`、`source_pool`、`started_at`、`min_days`、`status` |
| `AgentRequest` | agent 向人提出的请求 | `request_id`、`campaign_id`、`kind`（data / question / scope）、`content`、`status`、`response` |

所有对象用 pydantic 定义，放在 `contracts/`，并导出 JSON Schema 给前端。

标识规则：

- `factor_id` 是逻辑因子，`version` 递增；`candidate_hash` 是规范化表达式 + 参数的哈希，用于去重。
- 同一 `candidate_hash` 重复提交不产生新因子，但仍记一条 trial（重复评估也消耗预算）。

## 2. 因子 DSL

第一阶段只允许表达式型因子（E1）与系统维护的模板（E2）；受限程序（E3）在 M4 之后引入。搜索空间的完整定义（表达能力分级、派生变量库、覆盖坐标）见 [factor-search-space.md](factor-search-space.md)。

- 语法：函数式表达式，例如 `cs_rank(ts_mean(ret_1d, 5) / ts_std(ret_1d, 20))`。
- 终端变量：panel 中的白名单字段（价格、量额、估值、基本面）以及派生字段 `ret_1d`、`vwap` 等。
- 算子分类（首批从 FactorMiner 的算子库移植并裁剪）：

| 类别 | 示例 |
|---|---|
| 时序 | `ts_mean`、`ts_std`、`ts_sum`、`ts_max`、`ts_min`、`ts_rank`、`ts_delta`、`ts_delay`、`ts_corr`、`ts_cov`、`ts_decay_linear`、`ts_zscore` |
| 截面 | `cs_rank`、`cs_zscore`、`cs_demean`、`cs_winsorize`、`cs_neutralize(x, industry, size)` |
| 元素 | `abs`、`log`、`sign`、`signed_power`、`add`、`sub`、`mul`、`div`、`max2`、`min2`、`where` |
| 分组 | `group_rank(x, sw_l1)`、`group_demean(x, sw_l1)` |

- 类型检查：区分时序与截面语义；窗口参数必须是正整数常量且不超过上限（默认 252）。
- 前视在构造上不可表达：没有负延迟算子；终端变量都是当日收盘后可得的值；标签字段不在白名单中。
- 规范化：交换律排序、常量折叠、代数等价化简（参考 FactorMiner `canonicalizer.py`），得到 `candidate_hash`。
- 复杂度：节点数、深度、终端变量种类数，超过上限（默认节点 ≤ 30、深度 ≤ 8）直接 L0 失败。

## 3. 评估指标

所有指标都附带 `evidence_tier`（dev / holdout / fresh）、区间、股票池、标签。

| 指标 | 定义 |
|---|---|
| 覆盖率 | 股票池中因子值非缺失的比例，按日平均 |
| RankIC | 每日因子值与标签的截面 Spearman 相关 |
| RankIC 均值 / ICIR | 日度 RankIC 的均值；ICIR = 均值 / 标准差 |
| RankIC 正比例 | 日度 RankIC > 0 的天数占比（按因子方向调整符号） |
| 分组收益 | 按因子值分 5 组，各组等权标签收益；多空 = 第 5 组 − 第 1 组 |
| 多头超额 | 第 5 组相对股票池等权基准的超额收益（贴近指数增强口径） |
| 换手代理 | 1 − 相邻两日因子排名的截面相关 |
| 与因子库相关性 | 与库内每个因子的日度截面 Spearman 相关的时间均值，取绝对值最大者 |
| 边际贡献 | 在参考模型组上，“基线特征集 + 候选”相对“基线特征集”的 RankIC 提升（walk-forward） |
| 中性化后 RankIC | 对行业与市值做截面回归取残差后再算 RankIC |
| 成本后多头超额 | 按换手与单边成本（默认 0.15%）扣减后的多头超额 |

参考模型组（M6 起用于边际贡献，M4 起先用 ridge）：ridge、默认参数 LightGBM、LightGBM LambdaRank。基线特征集默认为因子库当前成员；因子库为空时用一组固定的基础量价因子。

## 4. Gate 与默认阈值

阈值是初始占位值，写在版本化的 `src/alphasieve/configs/gate_policy.yaml`（L0 的窗口、复杂度与终端变量限制在 `search_space.yaml`），M2 结束前用基础因子集（例如 Alpha158 在同一区间上的分布）校准。gate policy 的修改是工程变更，需要人工 review；每条 trial 记录其使用的 policy 版本。

| 层 | 检查 | 默认阈值 | 失败后状态 |
|---|---|---|---|
| L0 结构约束 | 语法、类型、白名单、窗口上限、复杂度、PIT 字段声明 | 见 §2 | `validation_failed` |
| L1 开发窗口 | 覆盖率 | ≥ 0.80 | `evaluation_failed` |
| | RankIC 均值（按声明方向调整后） | ≥ 0.02；方向与假设相反的候选直接失败 | |
| | ICIR | ≥ 0.25 | |
| | 与因子库最大相关 | ≤ 0.60（超过但 ICIR 高出 30% 以上时标记为“替换候选”） | |
| L2 样本内稳健 | 4 个不重叠子窗口中 RankIC 同号的个数 | ≥ 3 | `robust_failed` |
| | 中性化后 RankIC / 原 RankIC | ≥ 0.5 | |
| | 成本后多头超额 | 只记录，不拦截（gate_policy v2，D-24） | |
| | 边际贡献（参考模型组中位数） | > 0 | |
| | 参数来源 | default-first：默认参数，或单参数邻域救援 ≤ 7 次 | |
| L3 搜索折扣 | DSR（试验数与方差取自本 campaign 的 ledger） | p < 0.05 | `ledger_failed` |
| | 同批候选 BH-FDR | q < 0.10 | |
| L4 锁定留出 | holdout 上 RankIC 同号且 ICIR ≥ dev ICIR 的 50% | — | `holdout_failed` |
| | 读取预算 | 每个 shortlist 1 次；每个 campaign 默认 2 次 | 超额 `holdout_contaminated` |

## 5. Trial Ledger

- 唯一入口：`evaluation.evaluate(spec, tier, context)`。CLI 的 `factor eval` 与 orchestrator 的批次评估都调用它；函数内部先写 ledger（状态 `started`），计算完成后追加一条结果记录（状态 `completed` / `failed`）。
- 记录内容：`trial_id`、`campaign_id`、`factor_id`、`version`、`candidate_hash`、`evidence_tier`、`data_window`、`gate_policy_version`、`metrics`、`gate_results`、`created_by`（agent / human / system）、`artifact_id`、`prev_hash`、`hash`。
- 计数口径：DSR 使用本 campaign 内 `evidence_tier=dev` 的全部 completed trial，包括重复提交与失败候选。
- 不可删除、不可修改；需要作废时追加一条 `void` 记录并说明原因。

## 6. 状态机

状态定义见 design 文档。实现规则：

- 状态转移只能由 `gates/state_machine.py` 执行，每次转移写一条事件，包含触发者与依据的 trial / decision。
- 触发者：

| 转移 | 触发者 |
|---|---|
| draft → … → `robust_failed` / `robust_passed` | 评估入口（agent 或 human 调用 `factor eval`） |
| `robust_passed` → `ledger_gated` / `ledger_failed` | orchestrator 批次 gate（L3 需要整批 trial 计数，因此不在单次评估中执行） |
| `ledger_gated` → `shortlist_locked` | orchestrator（campaign 停止时）或 human |
| `shortlist_locked` → `holdout_evaluating` | human 批准 HoldoutRequest 后由 system 执行 |
| `holdout_passed` → `reviewable` | system 生成 Review Packet 后 |
| `reviewable` → `rejected` / `needs_repair` / `approved_for_shadow` | human（Approver） |
| `approved_for_shadow` → … → `fresh_observing` | system 流水线 |
| `fresh_observing` → `fresh_supported` / `fresh_failed` | system（满足最短观察期后） |
| `fresh_supported` → `approved_for_paper` | human（Approver） |

- `holdout_contaminated`、`rejected`、`retired` 为终态；`needs_repair` 只能产生新版本，不能在同一段 holdout 上重测。

## 7. 回测与执行模拟

回测分 B1–B5 五级：B1 因子快速评估（L1）、B2 因子可交易评估（L2）、B3 组合日频模拟、B4 滚动样本外、B5 paper。成交规则、成本模型、组合构建、敏感性检查与引擎选择见 [backtest.md](backtest.md)。

## 8. 模型

- 固定配置模型：LightGBM 回归或 LambdaRank，参数写死在 `config/models.yaml`。
- 滚动重训：每月末用最近 N 年（默认 5 年）数据重训，特征集为因子库当前成员；由 orchestrator 定时触发，属于流水线任务。
- 模型设计的自主循环（目标、损失、集成方式）在第一阶段之后引入，规则见 [design/agent-loop-verification.md](../../../design/agent-loop-verification.md) §3.3。
