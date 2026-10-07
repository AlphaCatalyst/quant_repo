# 25 · 组合层风险模型 v1

状态：风险报告步骤 1、2、4 已实现并生成 dev 报告；闭环、约束和 QP 未授权。日期 2026-10-04。风险报告仅使用 dev 证据，不读取 holdout/fresh，不运行新策略 trial。

## 1. 目标与非目标

定义逐日行业与六个风格暴露，报告目标和实际持仓的偏离；提供可选线性暴露约束；用固定参数的因子模型报告事前跟踪误差（TE）；设计从实际持仓出发的闭环优化。风险计算由后端完成，agent 只经 JSON CLI 消费 dev 报告。

不改预测模型、冻结分数、成本、调仓频率、A 验收或搜索折扣。不实现实盘下单、动态风险预算、风险参数网格、自动 holdout 审批或 paper 晋升。事前 TE 是估计，不是成交保证或新的验收通过证据。A v4 仍为未通过 dev 验收的参考；目前没有 mandate 通过 dev 验收。

## 2. 当前实现与缺口

| 文件 / 函数 | 已有行为 | 精确缺口 |
|---|---|---|
| `strategy/portfolio_lp.py::_solve/build_weights_lp` | HiGHS LP；行业 ±2 个百分点、市值 ±0.2 标准差、组合原始 beta 0.95–1.05、个股主动上限、单边换手；可选四段 PWL 成本 / ADV 主动上限 | `prev=w` 是前目标；没有动量、波动率、流动性、估值暴露或协方差；无解时先去掉换手限制重求 |
| `strategy/portfolio.py::_size_z/enforce_*/weight_diagnostics` | 启发式行业、主动个股、市值、beta 修复；目标暴露与目标间换手报告 | `_size_z` 用总体标准差，诊断用 pandas 样本标准差；启发式修复不等价于同时满足全部约束；没有实际持仓风险报告 |
| `strategy/execution.py::simulate/trailing_liquidity` | T 收盘目标，T+1 开盘成交；ADV/波动率截至 T 的 20 日、至少 5 日窗口；10% ADV 截断；持仓开盘/收盘漂移 | 实际前仓只在执行循环内；未成交留在原持仓/现金，到下一调仓重新计算，不是每天续单；仅汇总截断率，没有逐名未成交原因 |
| `training/mandates.py::index_enhancement/robustness` | 先一次构建目标，再分别模拟各规模；TE 是日净超额样本标准差 ×√252；固定时期从连续路径切片 | TE 仅事后报告；A v4 为 4.88%/年，位于 4%–6%/年；`TE_TARGET` 不在 `ACCEPTANCE["A"]` 内 |
| `training/run.py::run_cross_sectional`、`training/samples.py::rolling_beta` | beta 是股票 `ret_1d` 对价格指数收益的 60 日滚动估计，至少 30 日；传入组合层 | beta 不是完整风险模型；缺失值在 LP 中填 1，不能解释成已观测暴露 |
| `training/task.py::PortfolioLink/LATER_FIELDS/config_hash` | 严格 schema；后来增加的字段取默认值时从 hash 输入剔除 | 新行为必须显式入 hash；报告参数不能混进策略配置使历史身份变化 |

[backtest.md](../research/backtest.md) §4.4 的风险惩罚是设计目标；[task-layers.md](../research/task-layers.md) §4 P-6 和 [mandate-specs.md](mandate-specs.md) G-3 的完整暴露尚未实现。以 D-31–D-33、[a-portfolio.md](a-portfolio.md) §1/§2/§6 和 [a-cost-aware.md](a-cost-aware.md) 为当前行为与预算依据，不能把早期文档的“未实现”表格当最新状态。

## 3. 设计

### 3.1 暴露与时间口径

决策时点是交易日 T 收盘后。`U_T` 为该任务的 predict universe mask，A 为 CSI 500 PIT 成员；标准化不按模型分数是否缺失筛选，不使用训练池。令 `c_i=circ_mv_i`（池内、有限且 >0），其余为 0；`b_i=c_i/Σ_j c_j`，是流通市值加权全收益成员代理。分母为 0 时该日不可用。现金的股票因子暴露为 0。实际股票权重以包含现金的 NAV 为分母，不把股票部分重新归一成 100%。

对每个风格在 `U_T` 的有限原始值上独立计算截面统计。除市值外，先按当日 1%/99% 分位截断（线性分位插值），用截断后的当日截面中位数填缺失，再按填充后总体标准差 `ddof=0` 标准化。市值不截断，池内有限正市值的 z 严格复用 `_size_z`；缺市值填 z=0 并记缺失。统计量只拟合当日池内股票；仍持有的池外股票使用同一截断边界、均值和标准差转换，不能重新拟合。新版报告对有效池外市值也做转换，区别于旧 `_size_z` 的池外置 0；旧约束列另留 `legacy_size_z`，不暗换输入。

| 暴露 | 原始定义（交易日窗口，均截至 T） | 总是报告 / 可约束 |
|---|---|---|
| 行业 | PIT 生效行业的 0/1 哑变量，含 `unknown`；暴露为权重和 | 总是；现有 ±2 个百分点保留，新增行业口径须另注册 |
| 市值 `size_z` | `log(circ_mv_T)`，按上述例外标准化 | 总是；沿用主动 ±0.2 标准差，改变上限为新 trial |
| beta `beta_z` | 60 日配对有效股票全收益 / 任务价格指数日收益的 OLS 斜率（含截距），至少 30 对，分母为配对市场收益总体方差 | 总是报告 z、原始组合/基准 beta 和缺失权重；现有原始 beta 0.95–1.05 继续使用旧 `rolling_beta`，不暗换估计器 |
| 动量 `momentum_z` | 后复权 `log(close_{T-20}/close_{T-252})`，跳过最近 20 日；两个端点有效，上市历史 ≥253 个交易日 | 总是；可选主动上下限，单位标准差 |
| 波动率 `volatility_z` | `log(std(ret_1d[T-59:T], ddof=0)*√252)`，至少 40 个有限收益，标准差 >0 | 总是；可选主动上下限，单位标准差 |
| 流动性 `liquidity_z` | `log(mean(amount[T-19:T]))`，至少 15 日有限且非负成交额，均值 >0，原额单位人民币元；越大越流动 | 总是；可选主动上下限，单位标准差；与执行 ADV 至少 5 日的口径分开命名 |
| 估值 `value_z` | 当日历史 `pb_mrq>0` 的 `log(1/pb_mrq)`；不混 PE、不对非正 PB 取倒数；越大越便宜 | 总是；可选主动上下限，单位标准差 |

每个风格记录原始有效覆盖率、填充值、均值、标准差和缺失权重。覆盖率 <80%、有效股票 <100 或标准差为 0 时，该风格报告 `unavailable`，不填零掩盖。beta 原始回归分母为 0 同样无效；新版配对 beta 与旧约束 beta 分列，完整样本时应一致。报告旧约束的原始组合/基准 beta 时沿用缺失填 1，并单列其缺失权重。池外风格缺值按池内截断后中位数填充并标记，市值例外如上；没有可用截面统计时，该日事前 TE 无效。未知行业占组合或基准权重 >5% 时整体质量为 provisional。

PIT 要求覆盖价格、成分、行业和估值的实际可得日期。财报字段按公告日后的第一个交易日生效，不能用以后修订值回填；v1 估值只取历史日频 PB，不现算后来报表的账面净资产。面板 signature 与源版本冻结，供应商历史修订及流通市值代理 warnings 随报告保留。

申万官网行业历史已可免费取得，panel 可用 `sw1_pit`、`sw2_pit` 按日期关联；默认 `industry_source=csrc` 仍是当前证监会快照，非 PIT。申万记录从计入日期与更新日期两者较晚者之后的首个交易日起使用；更新日期较晚可能表示事后重述，且不是首次发布日期证明，现存文件不能还原更早发布的分类，严格的当时可得性仍未证实。新版风险报告只能经 `industry_asof(T)` 消费具可用时间的分类，不调用全期最后一行；缺名称或未到可用日记 `unknown`，并报告覆盖率。选用当前快照时，dev 报告可附 `industry_basis=current_snapshot_non_pit` 的兼容列和临时协方差，但整体 `pit_complete=false`，不得称为严格 PIT 风险模型，也不得用于新增约束或 TE 控制。旧行业约束继续原口径以保复现；启用申万口径须另注册新 trial。

每个日期/持仓种类报告 `E_p=X' w`、`E_b=X' b`、`E_active=X'(w-b)`。目标只在决策日有新值；实际收盘持仓逐日报告。另报目标减实际暴露、最大行业偏离、现金、原始 beta、约束余量与违反标记。所有风格均常规报告，默认没有新增风格约束。可选四个风格约束为 `lower_k <= X_k'(w-b) <= upper_k`，不通过惩罚项近似；不同时追加 `beta_z` 限制。

### 3.2 固定因子协方差与事前 TE

模型版本为 `rm1`，全部常数随代码冻结，不作为结果驱动的可调参数。股票日全收益 `r_s=close_s/close_{s-1}-1`；暴露回归必须用 `X_{s-1}` 与 `U_{s-1}`，最晚可用收益日为 T。缺股票收益则剔除该回归行，不能改回 s−1 的标准化样本。

逐日估计 `r_{i,s}=f_market,s+Σ_g industry_{i,g,s-1}*f_g,s+Σ_k z_{i,k,s-1}*f_k,s+epsilon_{i,s}`。WLS 最小化 `Σ_i omega_i*epsilon_i²+1e-6*Σ_k f_k²`，其中有效回归行的 `omega_i=sqrt(circ_mv_{i,s-1})/Σsqrt(circ_mv)`。市场列为 1，六个风格列为 z。行业哑变量保留，约束 `Σ_g b_{g,s-1}*f_g,s=0` 去掉与市场的共线性；当日回归没有股票的行业固定系数为 0，不能留下未识别自由变量。未知行业独立成组。至少 100 个有效收益股票且六个风格通过覆盖检查才保存该日；基准有权重但没有有效收益股票的行业使该日无效。

行业维度只在历史分类首次可用时扩展，记录列顺序；出现前的行业收益列补结构性 0，不从未来分类建立历史特征。当前组合/基准涉及的每个行业须在协方差窗口内至少有 126 个实际回归观测日，否则 TE 只能为 provisional；不得把补 0 算作行业历史。

| 参数 / 对象 | 固定值与计算 |
|---|---|
| 因子收益历史 | 最近 504 个交易日日历窗口，至少 252 个有效回归日；缺日跳过但衰减按真实交易日间隔 |
| 因子协方差 F | EW 半衰期 60 个交易日；权重 `q_s∝2^{-(T-s)/60}`；减 EW 均值后，除以 `1−Σq_s²` 得协方差；`F=0.9*F_EW+0.1*diag(F_EW)+1e-12*I` |
| 原始特异方差 v_i | 同一 504 日日历窗口，EW 半衰期 90 个交易日，至少 126 个有效残差；同样居中并做权重分母修正 |
| 特异方差收缩 D_i | `max(0.5*v_i+0.5*v_group,1e-8)`，单位日收益率平方；`v_group` 为 T 当日行业内有效 v 的等权中位数，至少 20 只，否则用池内中位数 |
| 缺特异历史 | 用 `v_group`，记录 prior 标记；全池没有有效 v 时该日无效，不使用未来或固定乐观方差 |
| 年化 | 252 个交易日；使用日协方差，不能再次年化输入 |

`Σ_T=X_T F_T X_T'+diag(D_T)`，其中 X 含市场、行业和六个风格。不用构建股票 N×N 矩阵；对主动权重 a，`sigma_a²=(X'a)'F(X'a)+Σ_i D_i*a_i²`，`TE_exante=√(252*sigma_a²)`。同时保存因子项、特异项和各因子 Euler 方差贡献；因子贡献可为负，不把相关项强制改成非负贡献。池外持仓缺特异历史使用上述行业 prior。

每日分别计算目标与实际 TE，基准为当日全收益成员代理，不是价格指数。现金会产生市场主动暴露；不得因 `Σa_i!=0` 去掉市场项。任一风格填充涉及的组合权重或基准权重 >5%，或特异 prior 涉及组合/基准权重任一 >5%，该日 `quality=provisional`；正常 warm-up 不输出虚假 TE。非 PIT 行业也只能给 provisional TE。价格指数差异继续由 `proxy_tracking()` 单列，不相加两个 TE 当作总风险。

dev 验证固定为 2016–2022，沿用 `robustness.PERIODS`、逐年与三个固定子段，不能按结果换窗口。主配对使用 T 收盘实际持仓的方差预测，比较下一日实际毛超额 `e_{T+1}=gross_ret_{T+1}−proxy_ret_{T+1}`；只取下一日没有目标执行、NAV 正、输入有效且 `quality=valid` 的日期。这些日子实际持仓只漂移，收益与 T 已知前仓匹配。`gross_ret=net_ret+同日cost` 沿用执行扣款口径；不重新模拟毛轨迹。

固定 bias statistic 为 `B=sample_std(e_{T+1}/sigma_a,T, ddof=1)`：理想为 1，>1 表示低估风险；同时报 `B−1`、配对日数、覆盖率，以及同日的 realised TE 与 `√(252*mean(sigma_a²))`。`sigma_a²<=1e-12` 的日期单列零风险异常，不参与除法。覆盖率分母为声明 OOS 窗口内全部下一日无目标执行的日期，warm-up/缺输入算覆盖损失，不能事后缩小分母。

至少 252 对、覆盖 ≥80%、总体 `0.8<=B<=1.2` 且三个子段各至少 126 对 / `0.7<=B<=1.3`，才称 dev 校准检查通过。它只评估模型，不改 A gate。provisional 样本可单独给探索性 B，不能据此通过检查。调仓日另列目标/实际 TE、成交偏差和全日净 realised TE，不混入上述匹配检验；不以筛掉调仓日后的 TE 替代 A headline 4.88%。缺真实持仓轨迹只报告缺失，不补开旧回测。

### 3.3 求解器选择

**v1 保留 LP，不加 QP 目标或硬 TE 约束。** 报告协方差只需现有 NumPy/SciPy。HiGHS 的 scipy 接口是 LP/MILP，不能接二次项；OSQP 可处理凸二次目标加线性约束，硬 TE 上限通常要 cvxpy+Clarabel 等二阶锥路径，增加依赖、数值容差和失败处理成本。A 当前收益/容量验收不通过，TE 已在范围内，增加依赖尚无必要。

未来触发条件固定为：模型通过上述验证并具 PIT 输入，且连续 20 个有效交易日实际事前 TE >6%/年，或人工明确要求上线前的硬 TE 上限。触发只生成研究请求，人工批准新预算后才预注册 QP/锥求解器方案。偏差检验失败先修风险模型，不通过调 lambda 遮住误差。低于 4%/年只报告；硬 TE 下限是非凸约束，不为凑目标强迫增加风险。未来二次目标还须把 alpha 和风险罚项统一到同一持有周期的收益单位。

### 3.4 从实际持仓出发的最小闭环

新增显式 `holdings_mode="actual_close"`，默认 `"previous_target"`。新路径只支持冻结 dev 分数的 A LP；不把它插入 docs/mandates/a-cost-aware 两配置。预测层仍输出分数；组合和执行通过一个小状态对象逐日交互，不把执行规则写进 LP。

1. `ExecutionState` 保存按 code 顺序的股票持仓市值、cash、NAV、日期、是否已首次建仓、上一目标（仅留作诊断）。决策前归一得 `p_T=holdings_value_T/NAV_T`，含此前开盘/盘中漂移、实际成交、费用和未成交留下的资产。
2. 新 `simulate_closed_loop(..., target_at_close)` 按日运行：执行 T−1 目标 → 算 T 收盘状态/收益 → 回调 T 的组合 → 排队 T+1 目标。首次分数决策日以全现金状态调用；调仓日历严格复用旧 `active[::rebalance_every]`，不按成交情况重排。
3. `portfolio_lp.solve_at_close(T, p_T, snapshot_T)` 调用原 LP 核心，换手和净成本增量统一从 p_T 计算；候选股票为当日成员并集实际非零持仓，退出成分只卖。目标仍全投资、只做多。按旧股票口径限制 `0.5*Σ|w−p_T|<=turnover_cap`；另报含现金的换手，不暗改上限定义。仅首次建仓豁免，不能因后续全现金状态再次豁免。
4. 求解只用 T 及以前的分数/价格/ADV；不读取 T+1 开盘价、涨跌停或成交掩码。T+1 用执行层的实际开盘前仓和原参与率裁剪，目标保持 T 决策值。闭环能消除前目标误差，不能消除隔夜价格跳变或保证风险约束在实际成交后满足。
5. 保存每笔 `goal−current_open`、最终成交增量、差额及原因：禁买、禁卖、ADV 截断、现金调整，原因可多值；按实际执行顺序归因。未成交仍到下一调仓重算，不新增续单。费用、现金与漂移继续原数值顺序；原路径可能出现的微小负现金只报告，不能借此重写旧结果。
6. 新闭环/新约束路径不静默放宽约束：无解或清除 `<1e-6` 权重后误差 >1e-6，保持实际持仓和现金、不发新订单，记录失败；该真实 trial 不合格且占预算。所有实际约束违反单列。旧路径的换手放宽与权重清理行为完整保留。

闭环下规模会改变前仓，从而改变后续目标。每个新预注册 trial 固定 headline 规模，并事前声明无 AUM、1 亿、5 亿、20 亿各自连续的闭环路径，计同一个 trial 的诊断；这是“各规模独立闭环”的容量口径。同时可用 headline 目标在其他规模做固定目标执行对照，两表分开，不按容量结果选规模。docs/mandates/a-portfolio、docs/mandates/a-cost-aware 既有“同目标各规模模拟”的口径不追溯改写。

### 3.5 对象、产物与 CLI

| 对象 / 表 | 字段与存储 |
|---|---|
| `RiskSnapshot` | T、代码/因子顺序、X、F、D、b、标准化统计、coverage、PIT/quality；float64；模型版本与输入 digest |
| `HoldingsSnapshot` / `TradeRecord` | 日期/阶段（close、open_before、open_after）、持仓值、cash、NAV；订单、成交、未成交原因；由执行观察器复制状态，保存 `holdings.parquet/trades.parquet/daily_execution.parquet`，后者含 net_ret、cost、bench 和是否执行目标 |
| `risk_exposures.parquet` | `(date,book,factor)`、portfolio/benchmark/active、单位、缺失权重、约束上下限/余量；book 为 target / actual_close |
| `risk_te.parquet` / `risk_validation.json` | 日期/book 的因子/特异方差、年化 TE、quality；固定 bias、切片、覆盖和失败理由 |
| 独立 risk artifact | parent trial/artifact id、旧配置/结果 digest、panel/holdings digest、risk spec hash、code version、tier/window、上述表；不更新旧 `metrics.json` 或 manifest |

不新增 SQLite 表或 ledger 列，复用 artifact service 与审计 events；risk artifact id 由自己的 manifest 决定。现有 `artifacts.py::write_artifact` 不接收 Parquet，新增独立 `write_risk_artifact` 服务按同一 canonical manifest/hash/临时目录原子发布协议写整份报告，不能发布后再追加表。manifest 含各表 digest；风险表不能经过 `training/run.py::write_outputs` 的统一 float32 转换，保留键/字符串类型和 float64 数值。

已实现命令：`alphasieve risk report --trial S-1f2df27729ff --model rm1 --tier dev --json`；`alphasieve risk validate --risk-artifact <id> --json`；`alphasieve risk show --artifact <id> --json`。report 只消费已经保存的目标轨迹和匹配 panel，禁止调用 `simulate`、优化器、训练或补读其他 tier。旧产物没有实际持仓时，仅生成目标报告并标记 `actual_unavailable`，validate 返回证据不足。实际持仓观察器属于尚未实施的步骤 3；现阶段没有实际持仓 TE 或 bias 配对。

agent/human 可运行上述 dev 命令；system 可作为已登记运行的后端产出同一报告，不能借角色扩大本命令的数据范围。报告生成写 artifact 与审计事件，不新增研究 trial。未来新策略仍走 `train validate/run` 的唯一入口和预算检查；holdout 申请/批准、review 决定、paper promotion 仅 human，risk 命令没有审批副作用。前端只读展示留待后续，不设计新的写操作页面。

## 4. 防泄漏、ledger 与预算

- 数据边界为 dev 2012–2022。CLI 在解析路径和读取文件前校验 tier、日期范围、parent manifest、panel signature；拒绝非 dev 或任何晚于 2022-12-31 的输入，不先读完整文件再过滤。收益端点为 s≤T，回归暴露端点为 s−1；验证的 T+1 必须仍在 dev 内。禁止读取未来标签、未来估值修订或全期标准化。
- 协方差参数和 bias 阈值在首份报告前冻结。报告检验失败不能按 A 收益改半衰期、收缩或暴露定义；修正版生成新 risk version 和独立 artifact，保留旧报告，人工工程评审。若用于组合，所用版本/参数必须进入策略身份。
- 单纯观测/报告不改变订单、NAV、已有指标、config hash、ledger 记录或验收。缺历史轨迹不允许免费重跑 v4/P1；完整策略重跑经原入口计 strategy trial。历史数值不能补写覆盖，非 PIT 临时报表不能追认为严格 PIT。
- 新约束、新目标、闭环前仓、变更市值/beta 定义或容量重优化都是新 A strategy trial。`PortfolioLink` 拟增 `risk_model_version: Literal["rm1"]|None=None`、`exposure_constraints: dict[str,tuple[float,float]]`（默认工厂生成空 dict）、`holdings_mode: Literal["previous_target","actual_close"]="previous_target"`；约束只允许上表四个新增风格，有限 lower≤upper，非空时要求 rm1、PIT 可用、A 冻结 dev 分数和 LP。闭环与非空约束都须另行授权，非默认字段进 hash；report 的 rm1 选择只在独立 manifest，不写进原任务。
- 默认字段加入 `LATER_FIELDS`，缺省和显式默认 hash 相同；默认走旧两遍构建/执行，保留旧求解输入、归一化、迭代顺序和旧诊断。v4 `18fc9302845d11b2`、P1 `488b1f8c2b00ddfa` 不变；原 ledger 和记录配置不可追溯更新。未来新策略冻结完整风险 spec、代码、成本、分数 provenance 和输入 digest。
- 当前 `STRATEGY_TRIAL_BUDGET` 为 A 13、B 5、C 4、D 4，A 13 已耗尽。docs/mandates/a-cost-aware 仅批准两个成本感知配置，计划累计 N≤15，代码尚未提高上限；本文新增名额 **0**，不能占用或替换这两个配置。任何风险约束/闭环/QP 的真实试验须人工另批预算与预注册，先记 started，失败/abandoned 也计数。`void` 不能减少失败尝试；模型报告不是策略胜出或 promotion。

## 5. 最小实现计划

步骤 1、2、4 已作为独立报告路径实现；步骤 3、5、6 未实施。涉及受保护评测、gate、ledger 计算的变更须人工工程 review；本次不修改这些模块。

| 顺序 / 文件 | 函数、schema 与验收 |
|---|---|
| 1 新 `strategy/risk.py`；`tests/test_risk.py` | `industry_asof/exposures_at_close/standardize/pit_quality`、`RiskSnapshot`；PIT 行业输入表为 `(code,industry,effective_date,known_at,source_version)`，要求两个时间 ≤T；暂不迁移现有 panel/SQLite。合成 fixture 验证总体标准差、池内市值与 `_size_z` 一致、原始/配对 beta 差异、窗口端点、缺失/常数截面、池外应用与未知行业；不用全期 `Panel.industry()` |
| 2 同模块 | `fit_factor_returns/estimate_covariance/exante_te/validate_bias`；手算 EW 权重/分母、50% 特异收缩、行业 prior、市场现金项和 TE 年化；行业约束消除共线性、PSD、零主动权重 TE=0；已知方差合成序列验证 B 与低估方向、coverage/252/126 边界 |
| 3 `strategy/execution.py` | 给旧 `simulate` 增加默认关闭的只读观察器，复制 close/open 状态与逐笔成交；打开观察器只增加独立产物；用停牌/涨跌停/参与率/现金夹具验证差额归因和持仓现金守恒 |
| 4 新 `training/risk_report.py`、`cli/commands_risk.py`、`cli/main.py` | `build_report/write_risk_artifact` 和三条 risk 命令；不改 SQLite schema；合成 parent store 验证拒绝错 digest/重复键/受限 tier，缺实际轨迹不调用模拟，类型/精度保存、幂等 artifact 和角色/审计 |
| 5 `training/task.py`、`strategy/portfolio_lp.py` | 三个行为字段、`_portfolio_rules/LATER_FIELDS`；新 `solve_at_close/_solve_risk` 用于新约束或 actual_close，新约束也允许 previous_target 模式独立检验；新增暴露矩阵行并硬拒绝数据质量不合格。默认继续旧 `build_weights_lp/_solve`，不借抽取改变浮点计算；验证上下限、解后约束和无解不放宽 |
| 6 `strategy/execution.py`、`training/mandates.py::index_enhancement`、`training/run.py` | `ExecutionState/simulate_closed_loop` 与新分支；用旧执行公式实现小型日步进函数，新路径使用，旧循环保留；保存 headline 与各规模独立状态，不改 `robustness` 切片/`ACCEPTANCE`/搜索折扣或现有 trial budget |

focused tests 均用合成 fixture、临时 artifact/ledger，不接真实研究库：修改 T+1 及以后价格/成交额/行业生效记录，T 及以前 X/F/D/目标不变；截断面板结果一致。未来数据用 mock 在打开前拒绝，不读取真实受限文件。

闭环夹具必须植入漂移、部分成交、池外残留和现金；断言下一调仓 p 等于实际收盘快照，而非旧目标；T+1 隔夜后成交前仓可不同于 p_T，费用/截断按旧公式；失败日无新目标，首次建仓豁免只出现一次。不同 AUM 状态不串用；子段切片不重置仓位。新约束 off/on 各测一组，不能只验证代码返回的诊断。

旧路径复现要求同一冻结运行环境下 **bit-for-bit**：所有旧 YAML 的 config hash、目标数组、日收益/成本、已有指标完全相同，分别检查无观察器和有观察器；NaN 位置也一致。保留旧黄金夹具，不用新版结果更新基线。报告文件可新增，旧结果文件不重写。工程完成后先跑 focused tests，再按仓库约定 `uv run pytest`；本次文档检查不代替这些工程验收。

## 6. 人工决定（2026-10-04）

1. 允许临时 dev 报告。行业输入采用申万历史，经 panel 的 `industry_source: sw1_pit` 和逐日 `industry_asof(T, "sw1")` 读取。申万记录的 `更新日期` 可能晚于 `计入日期`，属于可能的事后重述；报告必须保留此警告，质量为 provisional，绝不称为严格 PIT。
2. rm1 暴露、60/90 日半衰期、10% 因子协方差对角收缩、50% 特异方差收缩和 bias 门槛按 §3.1–3.2 冻结；没有参数网格。
3. v1 不引入 QP 或硬 TE 控制。
4. 不增加 A 试验名额，不运行实际持仓闭环；仅报告锁定参考 trial 的已存证据。
5. 全部新增风格上下限保持关闭。
