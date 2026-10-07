# 24 · 从属于 mandate 的因子 campaign

状态：设计，未实现、未运行。日期 2026-10-01。只交付本文，不改代码、配置、测试或索引，不运行 trial、Ray 或训练，不读取 holdout/fresh。本文落实 [mandate-specs.md](mandate-specs.md) §1；任务分层沿用 [task-layers.md](../research/task-layers.md)，预算纪律沿用 [a-portfolio.md](a-portfolio.md) §6。

## 1. 目标与非目标

把 campaign 绑定到一个 mandate 的冻结模型版本。候选的股票池、主周期、标签和 L2 对照由该版本决定。agent 研究的问题变为“这个因子是否补充该模型”，入选仍只代表 dev 上值得进入策略试验。

首期只支持 A。B 的小截面、C 的事件样本需要各自适配器，不能复用 A 的日频 L2；D 已降级为 A 的风险模块，不新开独立 campaign。旧的无 mandate campaign 保留原口径，不追溯重评。

不让 agent 搜索模型、组合或 gate 参数。不因因子通过而自动更新 library、A 特征或模型。不增加策略预算，不把代理 IC、成本代理或 dev 结果当作验收。holdout 批准、review 决定和 paper 晋升始终由人执行。

## 2. 当前状态与准确缺口

下表路径除文档和 `campaigns/` 外，均相对 `src/alphasieve/`。

| 已有路径 / 函数 | 当前行为与缺口 |
|---|---|
| `contracts/models.py::Campaign`；`campaigns/service.py::create_campaign` | 没有 mandate 字段；universe 默认 csi800，horizon 从重点领域推导。`ensure_can_evaluate` 只检查 campaign/turn 配额 |
| `evaluation/core.py::EvalInputs/l2_metrics` | 取 `label_{h}d` 原始收益和 `in_universe`；L2 中性化只含行业、规模，不是 A 的残差标签 |
| `evaluation/evaluate.py::build_job/compute_job/evaluate_spec` | 唯一入口本机写 started/终态；job 带实时 library，不带 mandate、训练 task 或 bundle |
| `evaluation/marginal.py::marginal_contribution/_walk_forward` | alpha=10 的 ridge，前 2 年后逐年 expanding，embargo=h+1；库为空则用 4 个基础特征。缓存键含 panel、horizon、基线名称，不含完整特征表达式和 mandate |
| `evaluation/service.py::run_worker/run_bridge` | 常驻 dev panel，worker 纯计算，bridge 转发，本机收尾写 ledger；可复用，不另建训练服务 |
| `gates/policy.py::gate_l1/gate_l2`；`configs/gate_policy.yaml` v2 | L1 独立 IC/ICIR、覆盖和库相关；L2 边际 IC>0，成本后超额仅记录。算法换口径须新 policy，不能冒充 v2 |
| `campaigns/lifecycle.py::l3_screen/conclude` | 对 campaign 结果做 DSR/BH、锁 shortlist、冻结记忆；有读取预算就创建因子 holdout 请求。没有 A 入模批次；还依赖全局 `factor_specs.state` |
| `factors/library.py::library_members/frames_from_members` | 库是全局成员集合，不是某个 A 版本的实际特征集；因子值缓存按表达式 hash、panel signature 存储 |
| `training/task.py::Features`；`training/run.py::resolve_features/make_bundle/_frames` | `factor_refs: library` 在打包时解析；bundle 冻结表达式、方向、panel fields 和 feature_version。新一次解析 library 可能改变 A 输入 |
| `training/derived.py::screen` | 24 个预注册候选，仅按覆盖≥0.8、相关绝对值≤0.7筛选，收益只记录；保留 13 个进入 v5。不是 mandate 边际筛选，也不是新的 campaign 入模入口 |

`campaigns/ws-fundamental-002.yaml` 沿用领域推导周期；`fund-flow-001.yaml` 显式指定 ashare_2020/5 日；`prog-evolve-001.yaml` 是独立的 400 次程序搜索。都没有 mandate 关联。并行 lane、模板变体和程序搜索继续按 [scaling.md](../data/scaling.md) §2.3–§4 计数，不能因吞吐增加而降低门槛。

A 参考是 `a_csi500_residual_v4`、`S-1f2df27729ff`。配置实际在 `src/alphasieve/configs/training_tasks/`：CSI 800 训练、CSI 500 预测；5 日/20 日残差标签；library 加 15 个 panel fields；行业内 rank 后 z-score；ridge/LGBM/LambdaRank，月度滚动、每年内层选参。综合 walk-forward 分数已冻结；实际调仓为 10 个交易日，不能沿用 docs/mandates/mandate-specs 早期的 5 日描述。

[training-round2.md](training-round2.md) §3–§6 的入模意图与实际 §8 不同：实际筛选未用模型边际贡献。v5 的 5 日 RankIC 从 0.056 升到 0.058–0.059，组合 IR 却从 v4 的 1.09 降为事件 0.96、因子 0.84、合并 0.57、敏感性 0.81，主要差在 2021–2022。IC 增益不是成本后 IR 增益，也不能把下降全部归因于交易成本。

截至本次核对，A v4 仍是未通过验收的 dev 参考；没有 mandate 通过 dev 验收。代码预算 A/B/C/D 为 13/5/4/4；docs/mandates/a-portfolio 的 A 名额已用完。docs/mandates/a-cost-aware 的两个成本试验另有批准，但代码仍为 13；这些名额不得挪给因子入模。

## 3. 设计

### 3.1 对象、绑定与数据流

`Campaign` 新增 `mandate: Literal["A","B","C","D"] | None=None`、`baseline_id: str | None=None`。二者同时有值或同时为空。绑定 campaign 的 `universe` 表示预测池，实际 panel 由 baseline 的训练池解析。新绑定记录固定为 `universe=csi500`、`horizon=20`；评估服务加载 dev csi800 panel，在其中应用两个 PIT 成分 mask。

创建时用 `model_fields_set` 区分省略值与显式值：省略 universe/horizon 则继承；显式值不符则拒绝，不能静默覆盖。A 的主周期固定为 20 日，L2 同时评估 [5,20]，权重 [0.5,0.5]。候选仍是一个表达式，FactorSpec 的 universe/horizon 必须为 csi500/20；不能在同一 campaign 按领域或结果换周期。标签定义、样本 mask、中性化和预处理只在冻结 context 中保存，agent 不能覆盖。

job 新增服务端解析的 `panel_universe=csi800`；`DirQueue.claim/run_worker/compute_job` 的接单、panel 缓存和一致性检查用这个字段，不能再用候选的逻辑 universe=csi500 找物理 panel。mask 从 context 解析，agent 不能传 panel 路径。无绑定 job 缺省仍取 spec.universe。

新增对象和 SQLite 表；大数组仍写内容寻址 artifact，不写 SQLite：

| 对象 / 表 | 必需字段与约束 |
|---|---|
| `MandateBaseline` / `mandate_baselines` | `baseline_id` 为身份 manifest 的完整 SHA-256（不含自身 id 和派生 artifact_id）；mandate、源 task/trial、源 bundle/manifest/score digest、panel signature、实际特征顺序及表达式、标签/样本/split/ensemble、dev 窗口、算法版本、policy digest、成本 digest、artifact_id、创建人/时间；插入后不更新 |
| `CampaignContext` / campaigns 新列 | `mandate`、`baseline_id` 外键；有效口径也写入 spec_json。与 baseline 同时冻结，运行后禁止更换；旧行两列 NULL |
| `MandateFactorEval` / `mandate_factor_evals` | trial_id 唯一，started_seq 外键指向 trials.seq（trial_id 在 ledger 多行，不能直接作唯一外键）；campaign_id、baseline_id、factor_id/version、candidate_hash、L0–L3 结果、artifact_id；从 ledger 派生的上下文状态，不是新 trial |
| `FactorBatch` / `mandate_factor_batches` | batch_id、mandate、baseline_id、按序候选 trial/ref/hash 清单、manifest digest、状态 `locked/planned/tested/rejected`、人工决定及 strategy_trial_id；锁定后成员不变，每批最多一次 started |
| `StrategyFeatureSource` / TrainingTask 新字段 | 可选 `factor_batch_id`；带源 bundle digest、batch digest 和固定特征清单；默认省略保持旧 config hash，非默认进入 hash |

流程：人工冻结 A 版本 → 人工创建 campaign → agent 提交表达式 → 唯一入口 L0/L1/L2、factor ledger → 结题 L3/锁记忆 → 系统生成 dev 候选批次 → 人工预注册新 A task 与预算 → 完整 A strategy trial → 人工决定后续研究或评审。

baseline 创建只做确定性解析、缓存和审计，不选模型、不运行 A 组合，也不新增 strategy trial。必须找到源 trial 的唯一原始 bundle 和实际保留特征清单；不能用当前 library 重建旧 bundle。特征/标签变换须匹配源代码版本，版本漂移且没有兼容适配器就拒绝。冻结 A score 的字节和 OOS provenance，复用 `training/score_source.py::freeze_source` 的身份规则，并增加 panel signature 严格相等检查。源缺失、重复或不一致就停止，不重训替代。

### 3.2 因子级与 mandate 级的边界

L0 仍检查 DSL、复杂度、PIT 可表达性、领域、候选方向和数据覆盖起点；新增绑定一致性检查。L1 仍是单因子检查，但标签继承 A 的 20 日残差，报告在 CSI 500 预测池计算，另报 CSI 800 训练覆盖。原始 5 日/20 日 IC 作为诊断并列，不能替代主标签。L1 的比较成员及其同口径 ICIR 在 baseline 创建时冻结，不与原始收益口径的 library ICIR 混用。

L2 的分期、方向稳定、邻域预算仍是因子稳健性；边际贡献变成 mandate-specific。A 主标签已残差化，原 `neutral_ratio` 的分母改口径会失真：新 policy 对绑定 A 将它改为记录项，另报候选在原始收益上的行业/规模中性化保留率；残差标签的四个控制项由后端样本契约保证，不再重复作同一 gate。

拟议 policy v3 增加 `mandate_profiles.A`，无绑定路径逐项保持 v2。A 初始 L1 仍为覆盖≥0.8、有效日≥250、IC≥0.02、ICIR≥0.25、相关≤0.6，替代比率 1.3；L2 保留四段至少三段正 IC、邻域≤7，主边际 IC>0。这些阈值还未在残差标签上校准，工程上线前须人工 review 合成零假设/植入信号报告；不能承诺通过率，不按真实 campaign 结果调阈值。

mandate 结果归属于 `(campaign, baseline, factor version)`，不用全局 `factor_specs.state` 表示“对 A 有效”。同因子对两个模型可有不同结论。绑定路径的 L3/批次选择读上下文 trial，不能因另一 campaign 已改变全局状态而跳过；旧的因子 holdout/review 状态机保持原用途。

### 3.3 A 的廉价 L2：选择对齐特征的 ridge

当前服务一次到 L2 约 23 秒，D-25 记录 worker 计算约 22–32 秒。下表是同一常驻 worker、dev csi800、缓存热时的工程估算，不是本次测量；(c) 按固定 15 叶、100 轮的双周期浅树估算。共同的 DSL/L1、排队和传输成本另列，不能把内核耗时当端到端耗时。

| 选项 | 每候选 L2 内核估算 | 贴近 A 的程度与缺口 |
|---|---|---|
| (a) A 特征/标签的月度 ridge 增量 | 15–40 秒；端到端目标中位数≤35 秒 | 对齐输入、目标、训练/预测池和时间折；线性代理，不复现树交互、排序模型或每年选参 |
| (b) 对冻结 A score 正交后的候选 RankIC | 2–8 秒 | 直接参照真实 ensemble；只测已输出分数遗漏的线性秩信息。现有 score 只覆盖预测池，不能伪造 CSI 800 训练分数；偏相关也不等于加入后 IC 增量 |
| (c) 固定小 LGBM 月度 refit | 1–10 分钟 | 可捕捉交互，但仍不是完整 ensemble；两个特征集×两个周期×约 84 月，不含调参，仍明显高于当前因子筛选成本 |

选 (a)。固定 `mandate_ridge_v1`、alpha=10、带不惩罚截距，无网格、seed 搜索或年度重新选参。不是“完整 A 重训的边际贡献”；指标必须标记 `surrogate=true`。不同时把 (b)/(c) 当第二条获胜通道。若性能超目标，先优化缓存和算子，不能静默改年度折或标签。

1. baseline 从源 bundle 生成 A 的实际入模矩阵 X0，包括 15 个 panel fields、实际保留因子及缺失指示列。复用 `training/samples.py::universe_mask/labels/feature_grids/build_cross_sectional` 的变换；保存实际列名、mask 和 dropped 报告，不能仅凭 YAML 的 library 字符串推断。beta 按 A 的锁定基准和 60 日算法计算。
2. 残差目标为次日开盘至 t+1+h 开盘收益，控制 industry、log_circ_mv、其二次项、beta_60d，1%/99% winsorize 后 z-score。复用 A 训练池残差估计，在预测池上取值；不重新按候选缺失或 CSI 500 收益拟合另一套残差。标签成熟终点不得超过 dev 末日。
3. 使用 A 的 5 年 rolling、4 年 warm-up、每月首交易日重训、train_stride=5、min_train_rows=50,000；保持 `engine._train_rows` 的 `t < p-21`，并验证 `label_end < p`。训练池 CSI 800，日频预测池 CSI 500。基线无效折和行集合先冻结；候选不能改变它们。
4. 候选按同一预处理和缺失规则成为一列，必要时带一个缺失指示列。覆盖不足直接失败，不以缺失删掉低收益行；不改变 X0。各月/周期拟合 X0 与 X0+候选的 paired ridge；无候选预测预先缓存。相同测试日期、股票、标签比较两个版本，日期有效标的至少 100 只。
5. 每个 h 报 `IC_with[h]`、`IC_without[h]`、每日差、折数和配对覆盖。主指标 `marginal_ic=0.5*mean(ΔIC_5)+0.5*mean(ΔIC_20)`，阈值严格 >0；另报按 0.5/0.5 z-score 合成的分数 IC、2016–2019/2020–2022 和逐年差。两个周期共同有效日才进入主均值，缺折/NaN 不补零。

缓存采用两层。不可变 artifact 保存 label、mask、X0 的 float32 分块、每日 n/sx/sy/X'X/X'y、月度基线 Cholesky/系数和 OOS score；统计累加为 float64。worker 按字节上限维护 baseline LRU，不复制整套基线到每个 job。每候选只计算交叉项及最多两列的 Schur 补，复杂度由重复拟合全矩阵降为遍历候选与 X0 的交叉项。候选缺失指示的处理不能改变基线统计的行集合。

缓存键是完整 baseline manifest digest，不是特征名称列表：包含源 task/config/feature/bundle/score digest、panel signature、日期/股票顺序、标签/中性化/预处理/样本/折/alpha/算法/依赖版本、policy 和成本 digest。候选结果键再加表达式 hash、direction、FactorSpec digest。原子写入并验 digest；同 campaign 的所有 job 固定一个 baseline_id。改任一项产生新 baseline、新 campaign，旧结果不混排；cache hit 只省计算，不豁免新评估的记账。

### 3.4 v5 教训与组合相关诊断

主 L2 仍用预测增益 gate。另将两个 ridge 综合分数及冻结 A score 经 `training/mandates.py::neutral_score` 的行业/规模中性化后，报与真实 A score 的相关、5/20 日衰减和 10 日调仓的换手/成本代理。前者说明线性代理与 A 的距离，不能声称已测 A ensemble 加因子的真实效果。

成本代理固定为 CSI 500 有效分数最高 10% 等权目标，平分打平时按 code 排序；每 10 日更新，`turnover=0.5*sum(abs(w_t-w_prev_target))`。在 T 收盘用截至 T 的 20 日 ADV/波动率和固定 b3 费率估算目标交易费用：买卖线性费用，加 `sum(k*σ_i*abs(Δw_i)*sqrt(5e8*abs(Δw_i)/ADV_i))`；不按未来收益校准。首次建仓另列，不混入稳态均值。

保存 `delta_target_turnover`（百分点/次）、`delta_cost_proxy`（% NAV/年，按实际 dev 交易日/252 年化）、超 10% ADV 的目标交易比例及缺流动性比例；基线/候选用同一日期和股票集合，缺成本输入标为不可估计，不填零。冻结 A score 的同规则代理作参考。所有项 `informational=true`，不进入失败原因、L3 排名或批次选取。

它没有 A 的 LP 风险约束、漂移持仓、涨跌停阻塞、实际成交或容量反馈，不能命名为净 IR，也不另跑 LP/B3。沿用 D-24 的成本记录纪律。只有完整入模 strategy trial 能回答 v5 型问题；必须并列净 IR、逐年/固定子段、TE、目标/实际换手、费用和容量，IC 提升而净 IR 下降就保留失败，不删除特征“救回”配置。

### 3.5 固定批次进入 A 与 CLI 权限

首期一 campaign 最多 200 个 factor started、holdout_reads=0。结题后从 L3 shortlist 中，按首次 started seq 取最早 5 个尚未被批次消费的不同表达式；一次只锁一批，每批恰好 5 个，不足 5 个则等待，不开小批、不按最大 ΔIC 挑组、不试子集。剩余候选只排队，下一批需要人工另行决定与策略预算；同 baseline 下追加候选仍记录来源 campaign 和搜索次数。

锁批不加入全局 library。人工 `batch plan` 才生成一个新 A task：从源 bundle 复制实际固定因子 refs 加这 5 个 refs，panel fields、label/sample/split/models/search/ensemble/portfolio 原样保持。不得再解析 `factor_refs: library`；bundle 绑定原始表达式和两个 manifest digest，feature_version 改变。只允许这一个全批次配置运行一次完整 A 训练及组合评估，计一个 A strategy trial。固定内层网格和 seed 属于该 trial，改组、删特征、换 seed 或重跑都是新 trial。

下列 baseline/batch 命令为新增设计，尚不可运行；原 campaign/factor/train 命令也须先接入 context 校验。所有命令支持 `--json`，例中身份由既有 CLI 角色机制验证，不凭命令参数自报审批身份。

```bash
alphasieve campaign baseline freeze --mandate A --task a_csi500_residual_v4 --trial S-1f2df27729ff --json
alphasieve campaign create campaigns/a-mining-001.yaml --json
alphasieve factor eval candidates/example.yaml --campaign a-mining-001 --json
alphasieve campaign batch lock --campaign a-mining-001 --json
alphasieve campaign batch plan --batch BATCH_ID --task-id a_csi500_factor_batch_001 --decision-id DECISION_ID --json
alphasieve train run --task a_csi500_factor_batch_001 --json
```

完整 campaign 示例；baseline_id 的占位值需替换为冻结命令返回的完整 digest，新增字段实现前不能加载：

```yaml
campaign_id: a-mining-001
title: A v4 的基本面增量研究
question: 基本面因子能否补充冻结 A v4 的残差预测？
mandate: A
baseline_id: REPLACE_WITH_BASELINE_SHA256
domains: [fin_quality, fin_growth]
cells: [{domain: fin_quality, form: level, scale: quarterly}]
budgets: {trials: 200, turns: 60, turn_minutes: 30, max_hours: 72, holdout_reads: 0}
stop: {no_improvement_turns: 20, max_consecutive_failed_turns: 3}
agents: [{harness: codex, model: gpt-6-sol}]
```

| 角色 | 可运行范围 |
|---|---|
| human | freeze/create；锁定任务与预算、batch plan；启动真实策略 trial；后续 holdout 申请/批准、review/paper 决定 |
| agent | 已运行 campaign 的 factor validate/eval/expand、dev status/show、研究请求；不能 freeze、plan、train 或审批 |
| system | 调度 dev 评估、结题、按固定规则 batch lock；执行已人工预注册的策略任务并回收结果，不能选择任务、增加预算或作审批 |

## 4. 防泄漏、ledger 与预算

- baseline、A score、标签、缓存和返回指标全部为 dev 2012–2022；主 L2 只在源 OOS 支持的 2016–2022 上比较。先检查 tier/窗口再打开文件；跨 2022-12-31 的标签不构造、不读取。agent 只收到 dev 指标、公共口径和 digest，不给原始 score、label、基线矩阵或服务器路径。
- worker/bridge 只接收 dev job；job 的 baseline_id、panel signature、policy digest 必须与 campaign 完全一致，收尾再次校验返回身份。不能从 latest、holdout/fresh score 或未来 panel 补缺。远端只部署 dev baseline。现有进程隔离有残余风险（D-21/D-22）；须扩展 `agents/integrity.py` 与 CLI 白名单，不能声称已有文件系统强隔离。
- 绑定 campaign 强制 holdout_reads=0；`conclude` 锁 dev shortlist/记忆后结束，不创建因子 holdout 请求，不把因子 L3 通过改成 review 批准。未来策略 holdout 仍走 `training/holdout.py`：每 mandate 一次、人工申请并批准，用锁定配置重新训练/评分，绝不复用 dev score 充当 holdout。review、paper 状态只接受 human，agent/system 不自批。
- factor trial 仍唯一经 `evaluate_spec`，先 started 再纯计算及终态；新记录在已参与哈希的 metrics/manifest 中写 mandate、baseline_id、label_version、factor搜索来源。不要把 factor 的 scope 当已被哈希保护的字段；不改旧哈希链。并发配额检查与 started 预留要在同一写事务，失败、无效和进行中均计数；重复拒绝在 started 前，不补录或删行。
- factor L3 仍对单因子主标签 ICIR 做 DSR（有效日数/20）和 BH（q=0.10，p≤0.05，shortlist≤10），不是对未经校准的 ΔIC 做 DSR。`N_factor` 取该 campaign 所有 started trial，ICIR 方差只取有限终态值；缺指标不减少 N。续开同一搜索的 campaign 要绑定原 search_group 并累计其 N，不能重置折扣；程序搜索与 LLM 搜索分别建组，不互相稀释。缺少可估计的方差时不产统计通过结论。
- `N_strategy` 仍为 `strategy_trial_count(scope="A",tier="dev")` 的全部 started，折扣仍用 `training.run.search_discount` 的 IR 减零假设最优值。不能用 factor 的 200 替代 strategy N，也不能把 5 个因子算成 5 个 strategy trial。批次 lineage 同时展示每个来源 campaign/search_group 的 N、DSR/BH 和 A 的 N/上限/deflated IR；两种折扣不相加，策略折扣也不能证明因子搜索偏差已消失。
- 当前没有可供入模的 A 名额。本设计默认预留 0 个 strategy trial；有 5 个候选也只能锁批。人工要新增或明确划拨名额并留下决策记录，代码检查预算与用途后才可 plan/start；docs/mandates/a-cost-aware 已批准的两个用途不变。失败、abandoned、修复重跑消费 started，`void` 仅处理重复结果，不减预算。
- 有结果后改 L2 算法、阈值、baseline、周期或批次规则必须新版本、新研究计划；不得合并旧指标排序。完整策略 trial 仍按 A 原验收与当次预注册的稳健性/容量规则判断，人工决定是否保留参考版本。过 IC gate 不触发模型发布、holdout 或 paper。

## 5. 最小实现顺序

以下是后续工程清单，不是本次改动。工程改动需人工 review；先用合成 fixture，不读真实受限数据。

| 顺序 | 文件 / 函数 / schema | 最小交付与 focused tests |
|---|---|---|
| 1 | `contracts/models.py::Campaign`、新增 baseline/batch 契约；`state/db.py::MIGRATIONS` | 增上述表、campaign 两外键列、search_group 元数据及唯一约束；旧 NULL 行可加载。合成旧库迁移、重复插入、冻结后禁止改绑定、非法 mandate 和显式周期冲突 |
| 2 | 新 `campaigns/baselines.py::freeze_baseline/load_baseline`；`service.create_campaign`；`training/samples.py` 抽出可复用变换 | 源 bundle/实际列/score/provenance 必须一致；生成 dev 缓存和冻结比较库 ICIR。合成源缺失/多源、digest/列序/panel 不符、晚于 dev 的源在任何 panel/score 读取前拒绝；省略值继承与旧领域推导兼容 |
| 3 | 新 `evaluation/mandate_marginal.py::prepare_baseline/marginal_for_mandate`；`core.EvalInputs/l1_metrics/l2_metrics` | paired monthly ridge 与成本代理。合成 OLS/ridge 金标误差≤1e-6；常数候选无增量、植入残差信号有增量；复制列的 Schur 补与直接 ridge 解一致（重复列会改变惩罚，不能要求零增量）；候选缺失不改样本；cold/warm 与分块计算一致；(label_end=p) 不可训练；改未来价格/标签不改此前预测 |
| 4 | `evaluate.build_job/compute_job/evaluate_spec/_summary`；`service.DirQueue.claim/run_worker/run_bridge`；`gates/policy.py`、新 policy profile | context、物理 panel 路由、digest 和完整 paired 结果传输；人工 review 后发布 v3。合成 CSI 500 候选只加载 CSI 800 panel、同步/队列结果一致、错误 baseline 返回拒绝、成本记录项失败不拦截、NaN 边际不通过、旧无绑定 v2 行为相同；不同算法/方向/列序不能命中旧缓存 |
| 5 | `campaigns/lifecycle.py::l3_screen/conclude`、新 `batches.py::lock_batch/plan_batch`；`ledger/ledger.py` 的预留/查询辅助 | 上下文状态替代绑定路径的全局 state；L3 started N 与 search_group；固定 5 个顺序、无自动 holdout/library 写入。合成两 mandate 相反结论、未终态/失败计数、续组不重置、4/5 个边界、并发超预算与同批重复消费 |
| 6 | `training/task.py::TrainingTask`；`run.resolve_features/make_bundle/start_trial/complete_trial`；`cli/commands_campaign.py/commands_train.py` | batch refs/源 bundle 固定，不解析实时 library；启动前校验人工计划、预算用途、每批一次。合成 library 更新不影响新 bundle、默认字段旧 hash 不变、全批次只计 1 strategy、失败重跑拒绝、agent/system 不能 plan 或审批、旧 ledger 验证通过 |
| 7 | `agents/integrity.py`、agent dev 提示/白名单；`web/app.py`、四个既有只读页面 | dev 输出隐藏路径/数组，显示 baseline 与双层预算/lineage；无需审批 UI。合成角色隔离、非 dev 输出遮蔽、digest 篡改审计；不新增网页写 API |

通过 focused tests 后运行仓库要求的 `uv run pytest`。性能另做人工安排的 dev 工程测量：相同 panel/worker、固定非搜索候选、冷/热各列加载/计算/排队/端到端秒数和峰值 RSS；需要真实候选评估就照常入 factor ledger，不以基准测试逃记账。本次只校验文档，不执行这些测试或测量。

## 6. 人工待决定的问题

1. **是否首期只启用 A？** 推荐是。绑定 A v4 / S-1f2df27729ff；B/C 先拒绝，D 不独立开；旧 campaign 保持无绑定。
2. **是否接受 aligned ridge 作为 L2 gate？** 推荐采用 (a)，固定 alpha=10、月度折、20 日 L1 与 5/20 日联合 L2；明确线性代理，不按 campaign 结果选算法。v3 阈值先 review 合成校准报告，成本代理只记录。
3. **批次大小与不足额处理？** 推荐 5 个、按 started seq、结题后每 campaign 最多一批；不足额等待，不跑子集，不因 IC 大小改组。继续跨 campaign 搜索须明确 search_group 与累计折扣。
4. **何时给入模 trial 预算？** 推荐现在为 0；工程完成后人工为一个锁批明确批准 1 个新的 A strategy 名额并登记用途，累计预算按当时真实 ledger 增加。不能使用 docs/mandates/a-cost-aware 的两个成本名额。
5. **真实策略 trial 是否增加独立增益门槛？** 推荐保持 A 原验收和预注册稳健性/容量规则，并记录相对 v4 的 Δ净 IR；首期不再用未校准的“ΔIR≥0.02”自动晋升。人工若要求增益门槛，须在首个 batch plan 前冻结，不看结果再选。
6. **dev 入模资格是否需要单因子 holdout？** 推荐不需要。L3 dev shortlist 可进入人工策略研究，holdout 只留给锁定的 mandate 策略；因子不因此成为 approved_for_shadow/paper。任何 holdout/review/paper 决定仍由人作出。
