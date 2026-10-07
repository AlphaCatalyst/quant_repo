# 23 · 前瞻验证与 paper 追踪

状态（2026-10-02）：§6 五项政策已由 human 决定（D-36），但逐 cohort 真实启用仍须签名审批。25 个交易日合成链路验收已通过；同机 root 风险未解决，M7 及连续真实交易日运维验收均未完成。未读取真实 holdout/fresh 数据，未运行真实 trial 或 Ray。

## 1. 目标与非目标

把锁定策略与因子 shortlist 放到真实到来的交易日观察。T 收盘记录决策，T+1 开盘模拟成交。保留当时的数据、模型、目标、成交和统计证据。任何人都不能事后改写已经记录的 fresh 日。

推荐先做 mandate 策略追踪，再做 [product.md](../overview/product.md) S6 的因子 cohort。策略直接检验模型、组合、执行和成本的联合结果；因子验证回答信息是否持续存在，两者不能互相代替。当前没有 mandate 通过 dev 验收，因此第一批最多是人工登记的 shadow，不是已批准策略。

不做实盘、broker、自动选参、动态资金分配、regime 门或回撤叠加层。不在本期混入新的机器/人工/LLM 信号池；以后接入也必须先锁定来源与时间戳。连续 20 个交易日无人干预是工程验收，不是统计验收或 paper 晋升。

## 2. 当前状态与准确缺口

截至 2026-10-01，运行状态采用任务给定的已验证事实；本次只核对代码、文档和配置。

| 位置 / 函数 | 已有行为与缺口 |
|---|---|
| `configs/splits.yaml` | dev 为 2012–2022；holdout 为 2023-01-01..2026-09-25；fresh 从 2026-09-28 开始，没有 `end`，`locked=false`。holdout CSI 800 panel 只在本机建好；fresh 未建 |
| `data/panel.py`：`build_long_panel/build_panel/_write_tier` | 带 warmup 历史，按区间尾端做标签 embargo；`_write_tier` 替换整份 panel。`build_panel` 依赖 `splits[t]["end"]`，当前 fresh 无尾端，不能直接沿用；重建还会使旧日和旧标签变化 |
| `data/access.py`：`TIER_READERS/load_panel` | fresh、holdout 原始 panel 只准 system 读，human 也不能直接读。当前 reader 只支持单个 `panel.parquet`，缺少不可变日分区读取 |
| `cli/commands_data.py`：`cmd_data_daily_update`；`deploy/systemd/alphasieve-daily-update.*` | systemd Mon–Sat 18:40 CST 跑 `data daily-update`，只同步 raw；周六同步财务。没有 fresh 构建、打分或 paper 调度；warning 不能当作数据完整 |
| `training/run.py`：`make_bundle/_walk_forward_scores/execute`；`training/holdout.py`：`approve_read` | bundle 锁定任务和特征；holdout 人工批准后换窗口重跑。非 dev 可用窗口前历史训练，但没有逐日模型、在线持仓检查点；不能直接每天全窗重跑 |
| `training/engine.py`：`walk_forward`；`training/score_source.py`：`load_scores/source_bundle` | 前者仍按年在候选网格选参；dev 冻结分数路径 `fits=0`。`source_bundle`（提交 1706d8d，合成测试覆盖）让冻结分数的 trial 在 holdout 上按源训练 bundle 重跑，但仍经过年度选参，不能直接成为无搜索 forward scorer |
| `strategy/execution.py`：`simulate/trailing_liquidity` | 已有 T+1 开盘、禁买禁卖、费用、20 日 ADV/波动率、10% 参与率及平方根冲击；每次从现金开始，只输出聚合及日收益，没有连续状态、逐名成交和费用明细；缺价有归零处理 |
| `gates/state_machine.py`；`campaigns/lifecycle.py`：`decide_review` | 因子已有 `approved_for_shadow` 到 `fresh_observing/fresh_supported/approved_for_paper` 的状态边；review 由 human 决定。缺实际前瞻作业、L5 policy 和 paper 审批服务；这些因子状态不能直接套到策略 |
| `frontend/src/pages/`；`src/alphasieve/web/app.py`：`create_app` | 只有 Overview、Campaign、Factors、Ledger，API 只读；没有 forward 页。支持 `ALPHASIEVE_WEB_AUTH=none`，不能据此声称 agent 无法看见未来指标 |

A v4 `S-1f2df27729ff` 是未通过 dev 验收的参考。[a-portfolio.md](a-portfolio.md) §6 要求冻结 dev 分数不能用于 holdout/fresh。D-21–D-33 规定人工审批、本机保管与分层 ledger；[task-layers.md](../research/task-layers.md) 的早期“模型/组合未实现”不是当前状态。预算以 `training/mandates.py::STRATEGY_TRIAL_BUDGET` 当前 A 13、B 5、C 4、D 4 为准，其他设计中的预算建议不在本文生效。

## 3. 设计

### 3.1 追踪对象、资格与顺序

| 对象 / 模式 | 入组证据与标签 | 能形成什么结论 |
|---|---|---|
| strategy / `diagnostic_shadow` | 已入账的 completed dev trial、完整锁定 bundle、human 登记理由；dev 未通过或 holdout 未通过/未评估也允许 | 固定展示“未批准，仅 shadow”；`promotion_eligible=false`；无 `fresh_supported`，只出诊断与 `shadow_complete` |
| strategy / `validation` | 同一执行身份的 dev acceptance、人工批准的 holdout 通过产物、human review 决定与证据 hash | 可出策略级 fresh verdict；human 再决定正式 paper |
| factor / `validation` | shortlist 锁定成员、版本、方向和 horizon；L3/L4 证据；每成员 human `approved_for_shadow` 决定 | 独立因子 L5 verdict，不证明任何 mandate 策略合格 |

先实现 A 的一套锁定 shadow，默认不自动登记 v4。正式因子 cohort 只取已批准的 shortlist 子集，入组时一次锁定，不能删失败成员。未获因子 review 的观察可另登记 `diagnostic_shadow`，不改因子状态。strategy 的 shadow 与因子正式 `approved_for_shadow` 必须分别显示，不能用同一个“已批准”徽标。

因子正式路径沿用 `approved_for_shadow → shadow_promoted → materialized → shadow_trained → fresh_observing`。每步分别要有人工决定引用、冻结特征产物、固定参考模型训练 manifest；不得只为走状态而空跳。系统只执行已授权步骤，不能产生审批。策略用独立 cohort 事件状态 `locked → observing → supported/failed/inconclusive → closed`；UI 将 verdict 显示为策略级 `fresh_supported/fresh_failed`，不更新 `factor_specs`。

从登记后的下一个完整交易日开始观察；`start >= fresh.start`。2026-09-28 至上线前没有实时快照和决策的日子标为 `not_recorded`，不能补成 fresh 证据。以后 dev/holdout 资格改变，shadow 仍保留原身份，正式 validation 新开 cohort、重新计观察期。

### 3.2 日流程与 fresh panel

`raw 同步 → 封存 T 输入 → 结算前日目标 → 必要重训 → T 打分/目标 → 提交日记录 → 成熟标签/报告`。只在本机 system worker 执行，按交易所日历去重；周六、假日与 Persistent 补触发不能制造观察日。

新增 `data/fresh.py::append_day(T)`。只接受当天尚未提交的交易日，fresh 开始日来自锁定 splits digest，截止日显式为 T。复用 panel 的 PIT、成分、复权与可交易性转换，输入必须先过滤到 `event_date<=T` 且 `observed_at<=cutoff_T`，不能先读全量 raw 再截输出。当前 `_stock_frame` 等 reader 没有此完整时点契约，须拆出快照 reader。核心同步后复制需要的原始行和元数据为内容寻址快照；原 raw 以后被修订不影响已封存视图。

存到 `panel/fresh/<universe>/days/T/` 的不可变 Parquet、benchmark 与 manifest；system 目录 0700、文件 0600。登记 `(universe,T)` 唯一键和前日 digest，使用临时产物加原子提交；相同输入重试返回既有 id，输入不同报 `IMMUTABLE_DAY_CONFLICT`。禁止 fresh 走 `_write_tier` 全量替换；API/CLI、日任务和派生缓存都不能覆盖旧文件或旧行。

当日 panel 不持久化尚未成熟的 `label_h`。T+h+1 到来后，在 `fresh_observations` 追加 `(cohort,signal_date,h,maturity_date)` 的标签与指标，引用当时封存的两个端点；不回填 signal_date panel。训练标签也从受限的不可变端点临时生成，只含已成熟数据。供应商迟报、财报周末才拉到、历史修订只追加审计事件，从后续新日按 observed_at 生效；不重算历史信号、净值、成分或行业。

复权须在首日锁定价值单位，之后以当时已知的公司行动向前链接全收益价格，不能用供应商新复权因子重缩放旧行；除权日原价跳变不能变成虚假收益。无法重建该连续单位时阻塞，不混用两个重定基的端点。warmup 既有非 PIT 行业/报表限制写入 manifest，不能宣称历史 context 全部是当时可得数据。

首日另封存只供 system 的历史 context，不计 fresh 观察数。rolling 5 年任务需覆盖 5 年训练期再向前留出最长特征 lookback、purge/embargo；至少满足原 2 年 warmup、60 日 beta、20 日 ADV（原最少 5 日），取最早所需日期。expanding 任务从原训练起点保留历史。禁止仅用 fresh 的几十行重训；新上市标的按锁定 `min_history/min_train_rows` 检查。

daily-update 后由 system 调 `fresh daily --asof T`；human 手动 daily-update 仍只同步 raw。新增 `data fresh-append` 为 system-only，`data build-panel --tiers fresh` 拒绝。数据质量检查覆盖必需字段、成分快照、交易日、目标/已有持仓价格与基准；缺事件/资金流/ETF/期货同步器时阻塞相应 cohort，不能把 core 更新成功当全部依赖齐备。

### 3.3 锁定模型的前向重拟合

登记 `ForwardConfig`：源 trial/config/bundle digest、解析后的特征及固定顺序、每 horizon 的模型类型/参数、seed、ensemble、预处理、训练窗口、purge、重训日历、组合/执行/成本、基准和 AUM。特征列及变换固定；前向不得重新筛库或按 fresh 覆盖率选择特征。按锁定 missing policy 处理，不足即阻塞。

模型参数取源 dev 最后一次合法选择记录的 `selection[year/h].chosen`；多 horizon 分别锁定，缺完整来源就阻塞，不能偷取候选 0。新增 `training/forward.py::fit_asof/score_asof` 复用 samples、单模型拟合及 ensemble 算法，绕过 `walk_forward` 年度 `_select_job`。每月第一个交易日收盘重拟合；首个观察日 bootstrap 一次，随后沿原 `split.retrain` 日历。月内每天只预测，不每天重拟合。

重训点 p 使用截至 T 的可得历史，但样本仍严格 `date_pos < p-purge_days`、`label_end<=T`，embargo 不缩短；rolling/expanding 和 stride 按锁定配置。只更新模型系数，不更改参数、feature set、seed 或 early-stop 规则；若原规则需要监测集，只能取过去已成熟训练内数据。模型 snapshot 记最大训练日期/标签端点、cutoff、行数、模型 digest 和环境，之后 T 日分数引用唯一 model id。失败暂停新目标，不静默改种子、旧模型或阈值。

这里的历史可能含 2023 年后的已成熟样本。D-36 已批准仅 system、本机、固定参数的 `operational_refit` 部署例外；每个 cohort 仍须单独签名批准并锁定时间范围和用途。它不签发 holdout approval，不生成 holdout verdict，不增加读取机会；正式 validation 仍须先走原一次人工 holdout 流程。此例外见 docs/mandates/training-tasks §1.2，不改变 dev 调参规则。

任何 `score_source` bundle 先于 panel/source 文件读取被 forward 入口拒绝。若跟踪 docs/mandates/a-portfolio/22 的组合配置，须用其源训练 bundle 的 dev 选定参数加锁定组合构造一个明确的 `forward_config_hash`，保留源 hash，登记 `scoring_mode=operational_refit`；不是删除 `score_source` 后冒充原 hash。资格须人工审查该映射，缺少一致训练 provenance 时不得入组。C-to-A 等依赖也需同样因果 scorer，禁止读冻结 dev 分数补未来、外推或向前填充。

### 3.4 paper 账簿与缺失日

前瞻 validation/shadow 都需要模拟账簿作为观察工具，标记 `book_mode=observation`，不能显示 `paper_active`。正式 `book_mode=approved_paper` 需 human 引用 dev/holdout/review/fresh 证据审批；从下一交易日另开全现金 book，引用观察 cohort，不接续其历史收益。因子支持只允许申请，正式 book 还必须关联通过策略验收的锁定 mandate。

T 收盘每天保存目标快照。只在锁定 rebalance 日生成新订单；其他日记录 `no_rebalance` 和持仓漂移，不把每 10 日调仓偷偷改成每天交易。组合继续原前目标/风险约束语义，不换成尚未在 dev 验证的实际持仓优化。T 的目标必须在 T+1 09:25 CST 前封存；超时记 `missed_signal`，不能按已知开盘补生成目标。日线到达 T+1 收盘后，系统按已封存目标回放 T+1 开盘模拟成交，记录 fill event time 与计算时间；不是声称当时取得了实时成交回执。

从 `execution.simulate` 提取 `initial_state/step_day`，批量模拟也调用同一核。保留其后复权价值持仓、分数权重、现金、隔夜/日内顺序、禁买禁卖、资金不足缩放、费用与平方根冲击。第一期使用虚拟价值单位，不声称是真实整数股/整手交易；原现金再投资全收益口径不额外重复加分红。未成交现金留存；参与率截断和交易限制的剩余目标只在下一锁定调仓日重试。ADV/vol 严格截止决策 T，不能使用成交日成交额。

每 book/day 保存 NAV、现金、逐名价值/实际权重、目标、模拟开盘成交价与价值、禁买/禁卖/截断原因、目标与实际单边换手、佣金/卖出印花税/滑点/冲击各项、费用金额与 NAV 比例、基准收益和主动暴露。`actual_turnover=0.5*(buys+sells)`；首次建仓单列，成本从实际开盘 NAV 扣。所有日记录、fills、checkpoints append-only，不能每天现金重置。

非交易日不出收益。数据到 09:25 前仍不足则取消该日新目标；已有合法目标若缺成交日数据，记 `data_gap`，不能假装成交或以 `nan_to_num=0` 当作有效零收益。个股明确停牌可按原规则冻结价值并标价龄；未知缺价不等于停牌。完整缺价、缺基准日冻结交易，记 stale NAV、`ret=null/valid=false`；恢复后的跨日估值变动单列 multi-day，不拆成伪每日收益或进入每日 HAC。不得事后补旧日 fill。

自动恢复仅从当前新日开始，按既有 book 状态和锁定估值规则结转；标签跨缺口失效。目标生成但尚未封存的失败作业可在截止前用同快照重试；已封存后不得更换输入。连续 2 个交易日关键缺口自动暂停，human 决定继续或关组；恢复不删除缺口或重置回撤。

### 3.5 对象、表、manifest 与命令

| 拟新增表 | 最小字段 / 唯一键 |
|---|---|
| `fresh_cohorts/fresh_members` | cohort id、object kind、mode、scope、ForwardConfig/policy digest、锁定时间、起日、parent、资格证据与 human 决定 id；成员键 `(cohort,object_version_hash)`，成员不能增删 |
| `fresh_days` | `(universe,date)`、cutoff、raw/context/partition/benchmark digest、缺口状态、prev hash；只 INSERT |
| `forward_runs/model_snapshots` | run id、cohort/date/kind、started/terminal 事件、输入 digest、模型 id、标签最大端点、环境/seed、资源使用、error；重复 run 的尝试分开保留 |
| `fresh_observations/fresh_verdicts` | `(cohort,member,signal_date,h)` 的成熟标签引用；固定评判端点、coverage、HAC/BH、metrics/policy hash、verdict，缺失原因保留 |
| `paper_books/paper_days/paper_fills` | book mode、cohort/approval id、capital、benchmark id；`(book,date)` 日状态与 manifest；fill 逐名键；prev hash 与 checkpoint digest |
| `forward_ledger` | seq、record kind、cohort/run/book/date、对象/配置/证据 hash、actor、timestamp、prev hash、row hash；锁定、每日提交、缺口、失败、暂停、裁决及审批引用全入账 |

身份、账簿与 ledger 用 INSERT-only 触发器保护；运行中状态从事件投影。文件先写不可变内容寻址产物，SQLite 事务一次提交引用及终态，崩溃产生的未引用产物不算有效日。孤儿文件不能被自动认作成功。

manifest 含代码 commit、依赖锁/运行镜像与数值库版本、完整 ForwardConfig、源 dev/holdout/review 证据 id、splits/policy/cost digest、训练及 warmup 数据快照、当日分区链、列顺序、cutoff/决策/成交时间、seed、模型和初始/前日 checkpoint、全部输出 digest。复现只读这些不可变输入，不读 latest raw；默认加载封存模型复现分数/成交，另可审计重拟合。权重/NAV 绝对误差 ≤1e−10，金额误差 ≤0.01 元；超差记录 `replay_mismatch`，原记录保留，不拿新结果替换。

| 拟新增 JSON CLI | 允许角色与行为 |
|---|---|
| `fresh register --trial ID --mode diagnostic_shadow\|validation --reason TEXT` | human；校验资格、依赖、锁定 ForwardConfig/policy 和 operational_refit 授权，不运行历史 fresh |
| `fresh register --shortlist ID --members LOCKFILE --mode validation --reason TEXT` | human；验证每个成员 review 决定，冻结成员和固定参考模型，不打开 holdout |
| `data fresh-append --asof T`；`fresh daily --asof T` | system；本机原子日任务，幂等，不接受未来/已关闭历史日补录 |
| `fresh list/show --cohort ID`；`paper show --book ID` | human；后端只读 read model 与 manifest，不能直接读 panel；agent 全部拒绝 |
| `fresh pause/close --cohort ID --reason TEXT` | human；终止/暂停有事件，不能删组；机器仅可按冻结故障规则暂停 |
| `paper approve --cohort ID --evidence-hash HASH --reason TEXT` | human；验证资格、锁定金额/基准，写 decisions/promotion 记录；system 只能在下一交易日启动该已批准 book |
| `fresh replay --manifest ID` | system；隔离只读复现，不追加模拟交易、不重算试验次数 |

### 3.6 统计与裁决

注册时锁定 primary horizon、方向、成员集合、benchmark、风险门槛、评判端点与 `forward_policy_v1`。因子至少 60 个有有效成熟标签的预测交易日；h=20 的连续无缺口 cohort 最早还需等最后信号的第 21 个后续交易日。策略至少 120 个有效的单日收益观测，累计净收益、回撤、执行质量从首日持续保留；年化按 252 日标注，仅作短期估计，不套用 dev 的“7 年 5 年为正”。

每日给人报告有效/总交易日、缺口、成熟数、数据/模型龄、每日与累计净收益/基准/超额、IR/TE、绝对及超额回撤、换手、分项成本、现金/截断/拒单率与约束偏离。因子报 RankIC 均值/ICIR、正 IC 比例、覆盖、中性化保留率、十分组 spread、锁定参考模型 base 与 base+factor 的边际 IC；后者模型、训练规则也须入组前冻结，不按 fresh 调权。非 primary horizon 只作描述。

Newey–West HAC 对每日 IC/spread/超额均值给标准误、95% CI 与单侧 p；固定滞后 `L=max(h−1,ceil(4*(n/100)^(2/9)))`，策略 h 取锁定最长持有/调仓跨度。缺失不删日后压紧时间轴，协方差只在相距实际 k 个交易日的有效配对上算；跨缺口收益不参加。cohort 全部锁定因子 primary p 做 BH `q=0.10`；strategy 同一预注册 mandate 批次也做 BH，单配置不折扣成多个日试验。缺失成员不能从检验家族删去（保守 p=1）。

正式因子支持：有效日 ≥60、有效日/已到成熟期限交易日 ≥95%、primary 有方向 RankIC>0、HAC 单侧 p≤0.05 且 BH 通过、固定参考模型边际 IC>0。正式 A 策略支持：有效日 ≥120、有效日覆盖 ≥95%、成本后超额均值>0、HAC p≤0.05 且 BH 通过、超额回撤不低于 −8%、TE 4%–6%/年、无未处置硬约束违规。B/C 不自动套 A 门槛，须另冻结 mandate policy 才能 validation；D 不作为独立追踪策略。

只在预先锁定的首个合格端点判一次。显著负 primary 均值（HAC 95% CI 上界<0），或冻结风险硬门槛失败，出 `fresh_failed`；正向但证据不足、缺口太多或统计不可算，出 `fresh_inconclusive`，不能把未显著当证伪。因子状态机需增加该状态及关闭路径；继续观察须登记固定新端点和多次查看校正，不每天反复测到通过。工程错误只出 `blocked/data_gap`，不出信号失败。shadow 用相同描述统计，但永远 `promotion_eligible=false`，无正式 verdict。

`fresh_supported` 是系统证据判定，不是 review 或 paper approval。达标后系统生成待人工评审材料，不能自己决定晋升；`fresh_failed/inconclusive` 也不自动调整参数。paper 阶段持续报告风险，机器可按已批准故障规则暂停，恢复/退役/回滚由 human 决定并留痕。

## 4. 防泄漏与 ledger / 预算

`TIER_READERS` 不变。raw/context/panel/model/持仓产物只给本机 system；human 经专用 read model 看 forward，不能把 human 参数传给 panel reader绕过权限。agent 不能看 forward 命令、指标、状态、artifact id、日志或 cohort 排名；因子 `shortlist_locked` 后仍显示 `batch_concluded`。资格表和受限结果不能进入 agent 的 memory、directive、trial 摘要、状态报告或 RunLab。

新增 forward HTTP GET 与报告下载必须独立强制 human 认证，`WEB_AUTH=none` 不豁免；旧通用 artifact、factor 状态/history、ledger/events 出口也检查 fresh 权限。agent runtime 不持 human 凭据且阻断本机 Web 与受限 artifact 路径。D-21/D-22 同用户沙箱仍存在直接文件读取风险；开真实 fresh 前须通过独立 system OS 用户或等效容器挂载隔离及红队测试，不能仅靠环境变量角色、0600 或 turn 后扫描宣称隔离已完成。

forward 没有搜索，不调用 `start_trial/complete_trial` 或 `check_budget=False` 来绕 dev 预算；单列 `forward_ledger` 连接原 trial，不新增 `layer='strategy'` started。重训次数、模型数、失败、CPU 时间、峰值内存和存储量仍入账。默认最多 2 个并发本机 worker、每个 8 CPU 线程/32 GiB、单次重训 2 小时、日任务总计 4 小时；初期最多 1 个策略 shadow、1 个最多 10 成员的正式因子 cohort，超额需人工新登记，不上传 Ray。

配置、特征、参数、模型选择、资金、基准、成本或数值行为代码改变都新开 cohort/hash，关联 parent，只统计新起日；定期系数重拟合不是配置改变。旧组与失败记录保留，不倒推新配置旧日。改变模型/组合作研究比较仍须回 dev 按 strategy trial 预算登记；不能用无预算 forward 并列扫参数。隔离复现和同输入故障重试记尝试，不产生新 cohort 或删失败。

forward 结果不自动反馈 dev 搜索、记忆或调参。人工据此另起研究必须登记 `forward_informed=true`、见过的 cohort/时间范围和新预算；不能称已有窗口是未见 fresh，不能重复利用同一窗口为改版签发独立验证。holdout 审批、review 与 paper 晋升始终 human-only。

## 5. 最小实施顺序与合成验收

| 顺序 / 文件、函数与 schema | 最小改动与 focused tests（隔离 SQLite、合成 panel、mock 时钟） |
|---|---|
| 1 · `contracts/forward.py`、`state/db.py`、`fresh/service.py::register` | 建 §3.5 的对象/表、INSERT-only/哈希链、资格与人工决定引用；测 shadow 永不 eligible、错 hash/缺来源拒绝、因子/策略状态不混用、修改配置必新组、forward 不改变 strategy N 或 holdout 计数 |
| 2 · `data/fresh.py::append_day/load_asof`、`data/panel.py` 快照转换、`data/access.py::load_panel` fresh 分区分支；`commands_data.py` | 测旧 fresh 分区字节/hash/行、benchmark、决策在追加新日及 raw 修订后完全不变；成熟标签只新增 observation；截止过滤、晚报、无 end、节假日、旧日补录拒绝、同输入幂等、不同输入冲突、崩溃恢复 |
| 3 · `training/forward.py::fit_asof/score_asof`、`engine.py` 固定参数拟合入口、`samples.py` 固定列/成熟标签路径 | 不改变 dev 年度选择；测月初/首日 bootstrap、purge/label_end、warmup 不计指标、未来价格/标签改写不影响 T 的模型/分数、月内不重拟合、无 `_select_job`、无覆盖率选列、score_source 在任何文件读取前拒绝、多 horizon 来源与依赖齐备 |
| 4 · `strategy/execution.py::initial_state/step_day`、`fresh/paper.py::settle_day/record_target`、`strategy/portfolio*.py` 增量适配 | 合成批量与连续 step 的 NAV/换手/费用误差 ≤1e−10；手算 T+1/涨跌停/停牌/现金/ADV 截断/成本；测分红拆股价值连续且旧行不变、调仓频率、前目标语义、持仓不重置、目标超时、缺价不伪造零收益、跨日估值、逐项成本和重放 digest |
| 5 · `fresh/statistics.py::hac/verdict`、`configs/forward_policy.yaml`、`gates/state_machine.py`、`campaigns/lifecycle.py` 人工 promotion service | 测 59/60、119/120 边界与成熟等待、95% 覆盖、HAC 重叠/缺口实际日距、BH 保留失败成员、supported/failed/inconclusive 分离、shadow 无正式 verdict、固定端点不每日窥探、agent/system 都不能批准 paper |
| 6 · `cli/commands_fresh.py`、`web/app.py`、`frontend/src/pages/Forward.tsx`、`agents/integrity.py`、systemd service | 加 human 只读页、system daily 编排与全出口过滤；测免密模式仍拒 forward、agent shell/HTTP/角色伪装/报告路径泄漏、并发单日提交与断点重试；frontend 明示 shadow/审批/缺口，审批仅 CLI |

以上工程变更逐项人工 review；先跑合成 focused tests，再跑仓库要求的 `uv run pytest`。接真实数据前完成人工 policy/operational_refit 决策、隔离和冻结；本设计不授权启动。部署后以连续 20 个交易日调度与无回填审计验收运维链；满期统计与人工 paper 审批另行验收，不补写过去 20 日冒充连续运行。

## 6. 人工待决问题（每项含推荐默认）

1. **Q-8：paper 资金与基准，已决定（D-36）。** A 的观察与 paper 账簿固定 **100 万元人民币**、初始 NAV=1，全现金、零申赎；headline 为 CSI 500 PIT 成员按前日流通市值加权的全收益代理。sh.000905 价格指数及代理差异单列参考，不能将代理称为官方全收益指数。可得官方全收益指数时另开 cohort。100 万元下参与率截断与平方根冲击接近零，与 dev 的 5 亿/20 亿容量检查不同；前向结果不能证明大资金容量。
2. **首批对象与 shadow，已决定（D-36）。** 先完成策略链路；是否登记 A v4 单一 diagnostic shadow 仍由 human 届时签名决定，本轮不登记。shadow 无晋升含义，不消耗或代替 holdout approval。
3. **部署重拟合的历史用途，已决定（D-36）。** 批准仅 system、本机、固定参数、成熟标签的 operational_refit 例外；rolling 5 年、月度重训，不年度选参。真实 cohort 仍须签名审批。
4. **统计期与 policy，已决定（D-36）。** 因子 60 个成熟有效日、A 策略 120 个有效收益日、覆盖 ≥95%、HAC p≤0.05、BH q=0.10，采用 §3.6 的三态结论；证据不足不自动延长。开组后不改 policy。
5. **隐私与运行额度，已决定（D-36）。** forward Web 强制 human 认证；2 worker、每个 8 线程/32 GiB、重训 2 小时、日任务 4 小时。关键缺口连续 2 日暂停，超时不追补。forward systemd 单元以独立 system 用户运行，fresh/模型目录 0700，并保留 agent 路径约束与审计。本机 agent 以 root 运行，可绕过独立用户与 0700；真正隔离需 agent 进不去的另一台机器或容器。是否接受同机 root 风险（同 D-21/D-22 的 holdout 现状）或换机器，仍待 human 决定；当前未完成真实隔离。
