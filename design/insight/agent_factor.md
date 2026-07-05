我建议不要把 QuantMind 改成“LLM 随机写策略代码然后回测”的系统，而是改成一个 **开放式因子研究工厂**：

> **QuantMind 继续做本地数据、Qlib、训练、回测、推理、实盘底座；
> 新增一个 `Factor Lab / Factor Factory` 层，由本地 Codex `exec` 负责生成、修改、解释、诊断候选因子；
> 但所有执行、评分、入库、反过拟合、权限和安全边界都由 QuantMind 的确定性工具控制。**

核心目标是：**从“固定 Alpha158 / 固定策略模板”扩展到“任意可验证的因子表达、特征组合、模型残差、行业中性化、时序/截面混合、 regime-aware 因子、组合因子、因子族演化”。**

---

## 1. 对 QuantMind 当前形态的判断

QuantMind 已经有几个很适合扩展的基础。

它本身是本地量化平台，README 里明确定位为打通模型训练、回测、推理、实盘全流程，内核是 Qlib + LightGBM + Alpha158，并且有 Qlib/Pandas 双回测、模型训练、推理、投研平台、实盘交易、风控、高级分析等模块。([GitHub][1])

后端也已经拆成 `api / engine / trade / stream` 四组服务，其中 `engine` 负责 AI 策略生成、模型推理和 Qlib 回测。这个结构很适合把“因子挖掘”放到 `engine` 内部，但不要污染 `trade` 和实盘链路。([GitHub][2])

目前策略侧仍然有比较明显的“模板化”痕迹：`strategy_templates` 里有 `momentum`、`alpha_cross_section`、`deep_time_series`、`long_short_topk`、`score_weighted`、`value_growth` 等固定模板。([GitHub][3]) 训练侧则有 Alpha158 / 152 维特征体系，文档里写了特征目录、分类、预设特征和当前特征集版本。([GitHub][4])

但好消息是，QuantMind 的 Qlib 策略开发规范已经不是完全固定模板。它明确说 V2 不要求固定 `_rule_based_policy`，策略入口支持 `STRATEGY_CONFIG`、`get_strategy_config()`、`get_strategy_instance()`，后端会通过 `StrategyBuilder / StrategyFormatterService / StrategyAdapter` 做自动适配。([GitHub][5]) 这说明你不是从零开始，而是可以把现有“策略适配器”往更通用的“因子适配器 / 信号适配器 / 实验适配器”扩展。

---

## 2. 关键改造方向：不要让 Codex 直接变成 QuantMind 的大脑

本地 Codex 的正确角色应该是：

> **开放式候选生成器 + 代码实现 worker + 诊断解释 worker。**

它不应该直接拥有以下权限：

* 直接写生产策略目录。
* 直接连数据库修改数据。
* 直接提交实盘交易。
* 直接把某个因子标记为“有效”。
* 直接覆盖模型、策略、回测配置。

Codex CLI 现在支持本地终端运行，并能读取、修改、运行当前目录代码；`codex exec` 是官方支持的非交互模式，适合脚本、CI、scheduled jobs 和 pipeline。官方文档也说明 `codex exec` 默认是只读沙箱，自动化时应显式设置权限，写文件用 `--sandbox workspace-write`，`danger-full-access` 只应在隔离容器等受控环境使用。([OpenAI 开发者][6]) ([OpenAI 开发者][7])

所以在 QuantMind 里应该新增一个 **CodexRunner**，把 Codex 当成可插拔 worker：

```text
QuantMind Orchestrator
  ├── 生成研究任务
  ├── 准备数据说明 / 因子目录 / 已有实验结果
  ├── 调用 codex exec 生成候选因子实现
  ├── 收集候选代码 / JSON spec / 解释报告
  ├── 静态校验
  ├── 沙箱执行
  ├── 回测评分
  ├── 入库或淘汰
  └── 生成下一轮任务
```

Codex 可以写代码，但 **QuantMind 决定哪些代码能执行、哪些结果可信、哪些因子能进入注册表**。

---

## 3. 总体架构

建议新增一个独立子系统：

```text
backend/services/engine/factor_lab/
  ├── orchestrator/
  │   ├── factor_mining_loop.py
  │   ├── task_planner.py
  │   ├── codex_runner.py
  │   ├── artifact_manager.py
  │   └── promotion_policy.py
  │
  ├── factor_ir/
  │   ├── factor_spec.py
  │   ├── factor_ast.py
  │   ├── factor_plugin_api.py
  │   ├── qlib_expression_adapter.py
  │   ├── python_vector_adapter.py
  │   └── sql_duckdb_adapter.py
  │
  ├── validation/
  │   ├── static_analyzer.py
  │   ├── leakage_checker.py
  │   ├── shape_checker.py
  │   ├── determinism_checker.py
  │   ├── dependency_checker.py
  │   └── sandbox_runner.py
  │
  ├── evaluation/
  │   ├── factor_calculator.py
  │   ├── ic_analyzer.py
  │   ├── quantile_analyzer.py
  │   ├── neutralization.py
  │   ├── turnover_analyzer.py
  │   ├── correlation_checker.py
  │   ├── walkforward_validator.py
  │   ├── portfolio_backtester.py
  │   └── overfit_guard.py
  │
  ├── registry/
  │   ├── factor_registry_service.py
  │   ├── experiment_registry_service.py
  │   ├── lineage_service.py
  │   └── memory_retriever.py
  │
  ├── mcp/
  │   └── quantmind_factor_mcp.py
  │
  └── prompts/
      ├── factor_discovery_system.md
      ├── factor_implementer.md
      ├── factor_reviewer.md
      ├── factor_mutator.md
      └── failure_diagnoser.md
```

其中最重要的是 **Factor IR**。不要让系统只支持固定 Alpha158 或固定模板，也不要直接允许任意 Python 自由运行。应该把所有候选都收敛到统一的 `FactorSpec`。

---

## 4. FactorSpec：把“因子”抽象成统一中间表示

为了 general，你需要先定义一个统一因子协议。

一个候选因子不应该只是 Python 文件，也不应该只是 Qlib 表达式，而应该是一个结构化对象：

```json
{
  "factor_id": "auto_mom_vol_resid_20260630_001",
  "name": "Residualized momentum adjusted by downside volatility",
  "version": 1,
  "author": "codex",
  "hypothesis": "短期动量在低下行波动环境中更稳定，且剔除行业与规模暴露后更能保留 alpha。",
  "family": ["momentum", "volatility", "residualized", "cross_sectional"],
  "implementation_type": "python_vectorized",
  "implementation_ref": "factors/generated/auto_mom_vol_resid_001.py",
  "required_fields": [
    "close",
    "volume",
    "ind_ret_20d",
    "style_ln_mv_float",
    "vol_downside_20"
  ],
  "lookback": 60,
  "horizon": 5,
  "universe": "csi800",
  "frequency": "daily",
  "neutralization": ["industry", "size"],
  "expected_direction": 1,
  "constraints": {
    "no_future_data": true,
    "max_missing_ratio": 0.25,
    "max_turnover": 0.8,
    "max_corr_to_existing": 0.75
  },
  "status": "candidate"
}
```

这样一来，因子可以有多种实现类型：

| 类型                      | 用途                                           | 是否建议支持 |
| ----------------------- | -------------------------------------------- | ------ |
| `qlib_expression`       | 类似 Alpha158 / Alpha101 的表达式因子                | 必须支持   |
| `python_vectorized`     | pandas/numpy 横截面或时序向量化因子                     | 必须支持   |
| `sql_duckdb`            | 基于 DuckDB/Parquet 的快速批量因子                    | 建议支持   |
| `model_signal`          | LightGBM / Ridge / ElasticNet / TabNet 等模型输出 | 建议支持   |
| `residual_signal`       | 对行业、规模、Beta、中证风格因子做残差化                       | 必须支持   |
| `ensemble_factor`       | 多个已有因子的线性/非线性组合                              | 必须支持   |
| `regime_conditional`    | 市场状态切换因子                                     | 建议支持   |
| `event_factor`          | 财报、公告、资金流、异动事件驱动                             | 后续支持   |
| `microstructure_factor` | 高频/盘口/成交结构                                   | 取决于数据  |

QuantMind 当前已有 152 维特征目录，覆盖基础行情、动量、波动率、成交量、资金流、风格、行业、微观结构等分类。([GitHub][4]) 这套目录正好可以作为 Codex 生成新因子的“原材料 catalog”。

---

## 5. 从“固定几种形式”扩展到“开放式搜索空间”

我会把因子搜索空间拆成 8 类，让 Codex 按类别系统探索，而不是随便想。

### 5.1 表达式因子

这类最接近 Alpha158/Alpha101。

例子：

```text
Rank(Delta(Log(close), 5)) * (-1 * Rank(Std(Return(close), 20)))
```

适合用 Qlib expression 或自定义 DSL 实现。

优点是安全、可解析、可批量生成；缺点是表达力有限。

### 5.2 Python 向量化因子

让 Codex 写一个受限函数：

```python
def compute_factor(panel: pd.DataFrame, ctx: FactorContext) -> pd.Series:
    ...
```

输入是 MultiIndex `[date, instrument]` 的 panel，输出也是 `[date, instrument]` 的 Series。

这类可以表达更复杂逻辑，比如：

* 分行业截面标准化。
* 市场状态条件切换。
* 非线性 clipping。
* groupby rolling。
* cross-sectional residualization。
* 多信号 gated combination。

### 5.3 残差化因子

这是非常值得扩展的方向。很多粗糙因子其实只是行业、规模、Beta、波动率暴露。可以让 Codex 生成原始 alpha，然后系统自动做：

```text
raw_factor ~ industry_dummies + size + beta + volatility + momentum
residual = raw_factor - fitted
```

最后评估 residual IC，而不是原始 IC。

### 5.4 因子组合 / 因子族演化

不要只挖单个因子，还要挖因子族：

```text
new_factor = 0.35 * factor_A + 0.25 * factor_B - 0.20 * factor_C + regime_gate * factor_D
```

Codex 负责提出组合假设，QuantMind 负责用 walk-forward 或 nested CV 搜权重，避免它直接用全样本拟合。

### 5.5 模型型因子

把候选因子变成 LightGBM / Ridge / ElasticNet 的输入子集，而不是只评估单因子。

QuantMind 本身已有 LightGBM + Qlib Model Framework，并已有训练/推理/模型管理闭环。([GitHub][1]) 所以可以新增：

```text
Feature subset search
  -> train small model
  -> produce pred.pkl
  -> evaluate IC/RankIC/portfolio
  -> record feature importance
  -> mutate subset
```

### 5.6 Regime-aware 因子

不同市场状态下有效因子不同。可以让 Codex 生成 regime detector：

```text
market_regime = trend_up / trend_down / high_vol / low_vol / liquidity_stress
```

然后不同 regime 使用不同 factor formula。

### 5.7 事件/资金流因子

QuantMind 的 152 维特征里已经包含资金流、行业和微观结构分类。([GitHub][4]) 这部分可以扩展出：

* 大单净流入反转。
* 资金流与价格背离。
* 行业相对强度变化。
* 微观结构异常后的短期反转。
* 资金流动量与波动率交互。

### 5.8 策略型信号

最后一类不是纯因子，而是直接生成 `pred.pkl` 或权重序列。

这类应该谨慎，先放在较高风险等级里。QuantMind 的策略规范已经支持 `"<PRED>"` 或 `.pkl` 作为信号来源。([GitHub][5]) 因此系统可以允许 Codex 生成信号构造器，但必须走更严格的 sandbox 和 OOS 验证。

---

## 6. Codex exec 如何嵌入 QuantMind

建议新增三个执行入口。

### 6.1 一次性候选生成

```bash
python backend/services/engine/factor_lab/cli.py discover \
  --theme "low turnover residual momentum factors for CSI800" \
  --budget 20 \
  --implementation python_vectorized \
  --universe csi800 \
  --horizon 5
```

内部调用：

```bash
codex exec \
  --sandbox workspace-write \
  --json \
  --cd research/factor_runs/{run_id}/workspace \
  "$(cat prompts/factor_implementer.md)"
```

Codex 输出必须是机器可读的：

```json
{
  "candidates": [
    {
      "factor_spec": {...},
      "files": [
        {
          "path": "factors/generated/f001.py",
          "content": "..."
        },
        {
          "path": "factors/generated/f001.md",
          "content": "..."
        }
      ]
    }
  ]
}
```

`codex exec --json` 可以输出 JSONL 事件流，便于脚本捕获执行过程；官方文档也说明 `stdout` 会变成 JSON Lines stream，包含 `thread.started`、`turn.started`、`turn.completed`、`item.*`、`error` 等事件。([OpenAI 开发者][7])

### 6.2 失败诊断和修复

某个因子失败后，不直接丢弃，而是生成 `failure_packet.json`：

```json
{
  "factor_id": "f001",
  "failure_stage": "shape_check",
  "error": "Output index does not match input panel index",
  "sample_input_schema": "...",
  "current_code": "...",
  "allowed_api": "..."
}
```

然后调用：

```bash
codex exec --sandbox workspace-write \
  "Fix this factor implementation. Preserve the hypothesis. Do not add imports outside whitelist."
```

Codex 修复后重新走 validation。

### 6.3 批量研究总结

每晚或每轮结束后调用：

```bash
codex exec --sandbox read-only \
  "Summarize these experiment results, identify factor families worth further exploration, and propose next tasks."
```

注意这里用 `read-only`，因为总结不需要改代码。

---

## 7. 强烈建议加 MCP，而不是只加 exec

`codex exec` 适合让 Codex 生成代码、修复代码、总结实验。但让 Codex 直接读数据库、跑回测、查因子注册表，最好通过 MCP 工具暴露。

Codex 官方文档说明 Codex CLI 和 IDE extension 都支持 MCP；MCP 可用 STDIO 或 HTTP server，配置可以放在 `~/.codex/config.toml` 或项目级 `.codex/config.toml`。([OpenAI 开发者][8])

QuantMind 应该新增一个 `quantmind-factor-mcp`：

```text
tools:
  list_feature_catalog
  get_feature_schema
  list_existing_factors
  get_factor_report
  submit_factor_candidate
  validate_factor_code
  run_factor_smoke_test
  run_factor_backtest
  run_factor_ic_analysis
  run_factor_neutralized_analysis
  run_walkforward_validation
  check_factor_correlation
  check_leakage_risk
  register_factor
  promote_factor_to_model_feature
```

这样 Codex/Claude Code 的关系会变成：

```text
Codex / Claude Code
  -> 通过 MCP 查 feature catalog
  -> 通过 MCP 查已有因子和失败记忆
  -> 用 codex exec 写候选实现
  -> 通过 MCP 提交候选
  -> QuantMind 后端验证、回测、评分
  -> MCP 返回结构化报告
  -> Codex 继续下一轮
```

这比“Codex 直接跑 Python 脚本并解析日志”稳定很多。

---

## 8. 安全与隔离：这是能不能 general 的关键

QuantMind 当前策略开发规范里已经写了安全限制：自定义代码静态检查严禁使用 `os`、`sys`、`subprocess`、`requests`、`urllib`、`socket`、`__subclasses__`、`__globals__`、`__builtins__`，并限制任意写文件行为。([GitHub][5])

要支持更开放的因子挖掘，安全策略不能放松，反而要升级。

### 8.1 三层执行环境

```text
Level 0: read-only Codex
  用于读代码、总结实验、生成计划。

Level 1: workspace-write Codex
  只能写 research/factor_runs/{run_id}/workspace。
  不能写 backend/、models/production/、strategy_templates/。

Level 2: sandbox factor execution
  Docker 容器执行候选因子。
  network=none
  read-only rootfs
  cap_drop=ALL
  cpu/mem/time limit
  只挂载只读样本数据和可写 /tmp/run_id。
```

QuantMind 的 engine README 已经在 AI-IDE smoke image 里采用过更严格的沙箱参数，例如只读文件系统、`network_mode=none`、内存/CPU 限制、`cap_drop=["ALL"]`、`no-new-privileges`。([GitHub][9]) 这个现有思路可以直接复用到 Factor Lab。

### 8.2 静态 AST 检查

对 Codex 生成的 Python 因子，执行前做 AST 检查：

禁用：

```text
Import(os/sys/subprocess/socket/requests/urllib/shutil/pathlib)
eval / exec / compile
open
getattr on dangerous objects
dunder access
global mutation
threading / multiprocessing
```

只允许：

```text
numpy
pandas
scipy.stats 部分函数
sklearn.linear_model 部分模型
statsmodels 回归可选
backend.services.engine.factor_lab.safe_ops
```

### 8.3 数据权限

候选因子只能看到：

```text
sample_panel.parquet
feature_schema.json
universe_membership.parquet
calendar.parquet
industry_map.parquet
```

不要让它直接连 PG/Redis/Qlib 全库。真正全量计算由 QuantMind 的 `factor_calculator` 在验证通过后执行。

---

## 9. 评估流水线：开放式搜索必须靠多阶段 gating

开放式因子挖掘最大风险是：**搜索空间变大后，过拟合会指数级变严重**。所以不能只看某次回测收益。

建议做 7 阶段筛选。

### Stage 0：语法与形状检查

检查：

* 是否能 import。
* 输出是否是 `[datetime, instrument] -> float`。
* 缺失率是否合理。
* 是否全常数。
* 是否极端离群。
* 是否使用未来数据。
* 是否 deterministic。

### Stage 1：小样本 smoke test

只用：

```text
100 只股票
6 个月数据
1 个 horizon
```

快速过滤明显坏代码。

### Stage 2：单因子 IC / RankIC

计算：

```text
daily IC
daily RankIC
IC mean
IC std
ICIR
positive IC ratio
t-stat
coverage
missing ratio
factor autocorr
turnover proxy
```

QuantMind engine 已经有深度投研分析能力，包括 Rank IC、ICIR、五档分层收益、风险风格归因。([GitHub][9]) 这些可以直接变成 Factor Lab 的基础指标。

### Stage 3：分层收益与单调性

做 5 分组或 10 分组：

```text
Q1, Q2, Q3, Q4, Q5
long-short spread
monotonicity score
top-bottom t-stat
```

如果 IC 还行但分层不单调，优先降级。

### Stage 4：中性化与归因

分别评估：

```text
raw factor
industry-neutral factor
size-neutral factor
industry + size neutral
industry + size + beta + vol neutral
```

很多“好因子”中性化后会消失，这能快速识别伪 alpha。

### Stage 5：相关性与新颖性

和已有因子库做：

```text
Pearson/Spearman corr
rolling corr
top-k overlap
factor family similarity
expression similarity
feature dependency overlap
```

只有在“有效且不重复”时才进入候选池。

### Stage 6：Walk-forward / Purged split

不要只做单一 train/test。建议：

```text
2017-2019 train / 2020 val / 2021 test
2018-2020 train / 2021 val / 2022 test
2019-2021 train / 2022 val / 2023 test
2020-2022 train / 2023 val / 2024 test
2021-2023 train / 2024 val / 2025 test
```

并在相邻区间之间加入 embargo，尤其是 horizon > 1 的标签。

QuantMind 的训练 README 已经有训练、验证、测试划分和 T+N 标签口径。([GitHub][10]) Factor Lab 要把这套口径推广到所有因子评估。

### Stage 7：组合回测

最终才进入策略回测：

```text
topk long-only
long-short topk
score-weighted
volatility-weighted
industry-neutral portfolio
turnover-constrained portfolio
```

QuantMind 当前回测底层是 Qlib，文档里说明 Qlib backtest loop 会基于 signal 生成订单，执行撮合、扣费、更新账户，并输出 portfolio metrics 和交易指标。([GitHub][11]) 这一步直接复用现有 engine。

---

## 10. 数据库设计：必须有 Factor Registry

没有 registry，持续挖掘会退化成“很多实验文件”。

建议新增这些表。

### 10.1 `qm_factor_definitions`

```sql
factor_id
name
version
family
implementation_type
implementation_hash
implementation_path
required_fields
lookback
horizon
neutralization
hypothesis
created_by
created_at
status
parent_factor_ids
lineage_type
```

### 10.2 `qm_factor_experiments`

```sql
experiment_id
factor_id
run_id
universe
start_date
end_date
horizon
eval_config_hash
data_snapshot_hash
code_hash
metrics_json
passed_gates_json
failure_reason
created_at
```

### 10.3 `qm_factor_daily_values`

```sql
factor_id
trade_date
instrument
value
normalized_value
neutralized_value
data_version
```

这个表可以很大，不一定一开始进 PG。可以先用 Parquet + registry metadata。

### 10.4 `qm_factor_correlations`

```sql
factor_id_a
factor_id_b
period
pearson_corr
spearman_corr
topk_overlap
family_similarity
```

### 10.5 `qm_factor_promotions`

```sql
factor_id
promotion_level
promoted_to
approved_by
reason
artifact_uri
created_at
```

promotion level 可以是：

```text
candidate
validated
watchlist
model_feature
paper_signal
production_disabled_by_default
```

默认永远不能直接生产启用。

---

## 11. Codex 任务设计：用多 agent 分工，而不是单 agent 通吃

Codex 支持 subagents，可以并行启动 specialized agents，并汇总结果；官方文档说这适合代码库探索或多步骤功能计划等复杂并行任务。([OpenAI 开发者][12])

你可以设计 5 类 agent。

### 11.1 Research Planner

职责：

* 读取已有因子 registry。
* 找出空白区域。
* 定义本轮主题。
* 生成 10–30 个候选假设。

输出：

```json
{
  "themes": [...],
  "candidate_hypotheses": [...]
}
```

### 11.2 Factor Implementer

职责：

* 把假设实现为 `FactorSpec + code`。
* 不能评估自己。
* 不能修改已有 registry。

### 11.3 Static Reviewer

职责：

* 读实现。
* 找潜在未来函数、数据泄漏、API 违规、复杂度过高。
* 给出 patch 建议。

### 11.4 Result Diagnoser

职责：

* 读取失败报告或弱结果。
* 判断失败原因：无效、过拟合、换手过高、行业暴露、数据问题、实现 bug。
* 提出变异方向。

### 11.5 Family Curator

职责：

* 管因子族。
* 合并相似因子。
* 淘汰重复。
* 维护“研究记忆”。

不要让 agent 自己决定 promotion。promotion 由 deterministic policy 做。

---

## 12. 推荐的 MCP 工具定义

下面这组是我认为最小可用版本：

```python
list_feature_catalog(category: str | None = None) -> FeatureCatalog

list_factor_registry(
    family: str | None = None,
    status: str | None = None,
    min_icir: float | None = None
) -> list[FactorSummary]

get_factor_report(factor_id: str) -> FactorReport

submit_factor_candidate(spec: FactorSpec, files: list[FilePatch]) -> CandidateId

validate_factor_candidate(candidate_id: str) -> ValidationReport

run_factor_smoke_test(candidate_id: str, sample_config: SmokeConfig) -> SmokeReport

run_factor_analysis(candidate_id: str, eval_config: EvalConfig) -> FactorAnalysisReport

run_walkforward_validation(candidate_id: str, wf_config: WalkForwardConfig) -> WalkForwardReport

run_portfolio_backtest(candidate_id: str, portfolio_config: PortfolioConfig) -> BacktestReport

check_factor_correlation(candidate_id: str, registry_filter: dict) -> CorrelationReport

diagnose_failure(candidate_id: str) -> FailurePacket

mutate_factor(candidate_id: str, mutation_goal: str) -> NewCandidateTask

register_factor(candidate_id: str, promotion_level: str) -> RegistryResult
```

MCP 返回值必须短而结构化，不要把完整日志塞给 Codex。完整日志放 artifact，返回 URI。

---

## 13. 开放式因子实现 API

建议先支持这个 Python ABI：

```python
# factors/generated/factor_xxx.py

from quantmind_factor_api import FactorContext, FactorResult

FACTOR_META = {
    "name": "industry_neutral_momentum_vol_adjusted",
    "family": ["momentum", "volatility", "neutralized"],
    "required_fields": ["close", "volume", "style_ln_mv_float", "industry"],
    "lookback": 60,
    "horizon": 5,
    "expected_direction": 1,
}

def compute_factor(panel, ctx: FactorContext) -> FactorResult:
    """
    panel:
      MultiIndex DataFrame [date, instrument]
    required columns are guaranteed by ctx.
    return:
      FactorResult(values=Series indexed by [date, instrument])
    """
    close = panel["close"]
    ret20 = close.groupby("instrument").pct_change(20)
    vol20 = close.groupby("instrument").pct_change().rolling(20).std()
    raw = ret20 / (vol20 + 1e-6)

    values = ctx.cross_sectional_zscore(raw)
    values = ctx.neutralize(values, by=["industry", "size"])
    return FactorResult(values=values)
```

`ctx` 提供安全操作，不让 Codex 自己乱写复杂 groupby：

```python
ctx.ts_rank(series, window)
ctx.ts_zscore(series, window)
ctx.cs_rank(series)
ctx.cs_zscore(series)
ctx.neutralize(series, by=["industry", "size"])
ctx.winsorize(series, limits=(0.01, 0.99))
ctx.fill_by_industry_median(series)
ctx.safe_div(a, b)
ctx.shift(series, periods=1)
```

关键点：**所有与时间有关的操作默认必须 backward-looking**。如果 Codex 想用 `shift(-1)`、`pct_change(-n)`、`rolling(..., center=True)`，直接拒绝。

---

## 14. Prompt 设计

不要给 Codex 一个宽泛任务，比如“帮我挖因子”。要给它结构化上下文。

### 14.1 Implementer Prompt 骨架

```text
You are implementing a candidate alpha factor for QuantMind Factor Lab.

You must output:
1. factor_spec.json
2. factor_impl.py
3. rationale.md

Allowed imports:
- numpy as np
- pandas as pd
- scipy.stats only if needed
- quantmind_factor_api

Forbidden:
- os, sys, subprocess, socket, requests, urllib, pathlib, open, eval, exec
- reading external files
- writing files outside current candidate directory
- future-looking operations

Input schema:
{feature_catalog}

Existing factor families:
{factor_memory_summary}

Task:
{hypothesis}

Evaluation target:
- Horizon: T+5
- Universe: CSI800
- Prefer low turnover
- Must be industry and size neutralizable
- Avoid high correlation to known momentum_20d and volatility_20d factors

Return only JSON manifest plus files.
```

### 14.2 Diagnoser Prompt 骨架

```text
You are diagnosing a failed factor candidate.

Failure packet:
{failure_packet}

Your job:
- classify the failure
- decide whether to repair, simplify, mutate, or discard
- if repairable, propose minimal patch
- if overfit/redundant, propose a different family direction

Do not claim a factor works unless validation metrics support it.
```

---

## 15. 研究循环

完整 loop 应该是：

```text
1. Load memory
   - 已有因子
   - 高相关失败因子
   - 最近有效因子族
   - 数据覆盖与字段 catalog

2. Generate hypotheses
   - Codex planner 生成候选方向
   - deterministic filter 去重、限制主题

3. Implement candidates
   - Codex implementer 写 FactorSpec + code
   - 每轮 10–50 个候选

4. Static validation
   - AST
   - schema
   - future leakage
   - dependency
   - complexity

5. Smoke execution
   - small universe
   - short period
   - no Qlib full backtest

6. Factor analysis
   - IC / RankIC
   - quantile return
   - neutralized IC
   - turnover
   - coverage
   - stability

7. Redundancy check
   - corr to registry
   - family overlap
   - feature overlap

8. Walk-forward
   - rolling OOS
   - embargo
   - hidden holdout

9. Portfolio simulation
   - topk
   - long-short
   - turnover constrained
   - cost-aware

10. Register / reject / mutate
   - 入库
   - 记录失败原因
   - 生成下一轮 mutation task
```

---

## 16. Promotion policy

我建议用明确 gate，而不是靠 agent 判断。

例如：

```text
candidate -> validated:
  smoke_pass = true
  missing_ratio < 25%
  abs(mean_rank_ic) > 0.015
  icir > 0.25
  positive_ic_ratio > 52%
  max_corr_existing < 0.75

validated -> watchlist:
  walkforward_pass_windows >= 60%
  neutralized_ic_retention >= 50%
  top_bottom_monotonicity > 0.6
  turnover_proxy < threshold

watchlist -> model_feature:
  improves model OOS ICIR by X
  does not worsen turnover materially
  survives recent 12-month holdout

model_feature -> paper_signal:
  passes portfolio backtest with costs
  capacity/turnover acceptable
  human approval required
```

实盘永远单独审批，不让自动挖掘直接进实盘。

---

## 17. 和 QuantMind 现有模块的集成方式

### 17.1 Engine

放核心计算：

```text
backend/services/engine/factor_lab
backend/services/engine/routers/factor_lab.py
backend/services/engine/tasks/factor_mining_tasks.py
```

复用 Celery，因为 QuantMind engine 现在已经用 Celery 处理耗时回测和参数优化任务。([GitHub][9])

### 17.2 API

新增用户态 API：

```text
POST /api/v1/factor-lab/runs
GET  /api/v1/factor-lab/runs/{run_id}
GET  /api/v1/factor-lab/candidates
GET  /api/v1/factor-lab/factors/{factor_id}
POST /api/v1/factor-lab/candidates/{id}/validate
POST /api/v1/factor-lab/candidates/{id}/promote
```

### 17.3 前端

新增页面：

```text
Factor Lab
  ├── Research Runs
  ├── Candidate Factors
  ├── Factor Detail
  ├── IC / Quantile / OOS
  ├── Correlation Graph
  ├── Family Map
  └── Promotion Queue
```

### 17.4 Strategy / Training

当一个因子进入 `model_feature` 后，才进入训练特征集：

```text
qm_feature_set_custom_{user_id}_{version}
```

不要直接修改系统默认 152 维 feature catalog。

---

## 18. 推荐落地路线

### Phase 0：基线冻结

先不要上 Codex。先把现有能力基线化：

* 现有 Alpha158 / 152 维特征集。
* 现有模板策略。
* 现有 Qlib 回测。
* 现有 RankIC / ICIR / 分层收益 / 风格归因。
* 现有训练 OOS 表现。

产出：

```text
baseline_factor_report_YYYYMMDD.json
baseline_model_report_YYYYMMDD.json
baseline_strategy_report_YYYYMMDD.json
```

### Phase 1：FactorSpec + Registry

实现：

```text
FactorSpec
FactorRegistry
ExperimentRegistry
ArtifactManager
```

这一步不需要 Codex，但会决定系统能否长期演进。

### Phase 2：Python vectorized factor ABI

实现：

```text
quantmind_factor_api
static_analyzer
sandbox_runner
shape_checker
leakage_checker
```

先手写 5 个因子验证 ABI。

### Phase 3：CodexRunner

接入：

```python
class CodexRunner:
    def run_exec(
        self,
        prompt: str,
        workspace: Path,
        sandbox: Literal["read-only", "workspace-write"],
        json_output: bool = True,
        timeout_sec: int = 1800,
    ) -> CodexRunResult:
        ...
```

默认：

```bash
codex exec --json --sandbox workspace-write --cd {workspace} {prompt}
```

并记录：

```text
prompt_hash
codex_version
stdout_jsonl
stderr_log
generated_files_hash
exit_code
duration
```

### Phase 4：MCP server

实现 `quantmind-factor-mcp`，先暴露只读工具和 submit/validate 工具。

`.codex/config.toml` 可以项目级配置 MCP server。Codex 官方文档支持在项目级 `.codex/config.toml` 配置 MCP，且 CLI 与 IDE extension 共享配置。([OpenAI 开发者][8])

### Phase 5：多阶段评估

先实现：

```text
smoke
IC
RankIC
quantile
neutralization
correlation
walk-forward
```

再接 portfolio backtest。

### Phase 6：持续挖掘

加 scheduler：

```text
nightly_discovery
weekly_deep_validation
monthly_factor_pruning
```

夜间跑便宜搜索，周末跑深度验证。

### Phase 7：UI

最后做 UI。否则容易先做漂亮页面，核心研究闭环不稳。

---

## 19. 最小可行版本

我建议 MVP 只做这些：

```text
1. FactorSpec
2. Python vectorized factor ABI
3. Static analyzer
4. Docker sandbox runner
5. IC / RankIC / 分层收益 / missing / turnover proxy
6. Factor registry
7. Codex exec 生成候选
8. Codex exec 修复失败候选
9. 简单 UI 或 CLI 查看结果
```

暂时不做：

```text
实盘
自动 promotion 到生产模型
复杂 ensemble
事件因子
高频因子
多 agent swarm
```

MVP 的命令可以长这样：

```bash
python -m backend.services.engine.factor_lab.cli discover \
  --theme "industry-neutral low-turnover alpha from momentum, volume, and volatility interactions" \
  --budget 20 \
  --universe csi800 \
  --horizon 5 \
  --codex

python -m backend.services.engine.factor_lab.cli evaluate \
  --run-id 20260630_001 \
  --stage smoke,ic,quantile,neutralization,correlation

python -m backend.services.engine.factor_lab.cli report \
  --run-id 20260630_001 \
  --format markdown
```

---

## 20. 我认为最重要的设计原则

第一，**Codex 负责扩大搜索空间，QuantMind 负责约束和验证。**
不要把 Codex 输出直接当策略。Codex 只是研究助理和代码 worker。

第二，**先做统一 FactorSpec，再谈智能挖掘。**
否则很快会变成一堆无法比较的 Python 文件。

第三，**开放实现，但封闭执行。**
因子表达可以 general，执行环境必须严格。

第四，**评估比生成更重要。**
在量化里，生成 1000 个候选不难，难的是避免数据泄漏、重复因子、过拟合和不可复现。

第五，**从单因子扩展到因子族。**
真正有价值的是：某类假设在不同窗口、不同 universe、不同中性化后仍然有稳定弱信号，而不是某个单次回测漂亮的因子。

---

## 最终方案一句话

把 QuantMind 改造成 general factor mining 系统，核心不是“加一个 Codex 聊天窗口”，而是新增：

> **FactorSpec + Factor Registry + Sandbox Runner + Multi-stage Evaluation + CodexRunner + QuantMind MCP tools + Research Memory。**

这样它就可以从当前的 **Alpha158 / 固定策略模板 / 固定训练特征**，升级成一个 **Codex/Claude Code 可驱动、但由 QuantMind 确定性验证的开放式因子研究工厂**。

[1]: https://github.com/qusong0627/QuantMind/tree/master "GitHub - qusong0627/QuantMind: QuantMind 开源版 是一款面向个人量化研究者的本地化金融量化交易平台，基于微软 Qlib 量化框架构建，提供从模型训练，回测，推理，实盘交易的完整研究闭环。 平台深度集成 LightGBM 等主流机器学习模型，支持 146 维量化因子训练与推理，用户可快速构建 Alpha 策略并在历史数据上验证效果。核心功能涵盖智能策略生成、模型训练、回测中心、QuantBot 助手及多模型管理，全部功能无使用限制。 开源版采用本地单机部署，通过 docker compose 一键启动，无需依赖云服务，数据与模型完全本地化，保障研究隐私。适合个人开发者、学术研究者及小团队进行量化策略原型验证与二次开发，是进入金融量化领域的理想起点。 · GitHub"
[2]: https://raw.githubusercontent.com/qusong0627/QuantMind/master/backend/README.md "raw.githubusercontent.com"
[3]: https://github.com/qusong0627/QuantMind/tree/master/strategy_templates "QuantMind/strategy_templates at master · qusong0627/QuantMind · GitHub"
[4]: https://github.com/qusong0627/QuantMind/blob/master/docs/QuantMind_152%E7%BB%B4%E7%89%B9%E5%BE%81%E6%96%B9%E6%A1%88%E8%A7%84%E8%8C%83.md "QuantMind/docs/QuantMind_152维特征方案规范.md at master · qusong0627/QuantMind · GitHub"
[5]: https://github.com/qusong0627/QuantMind/blob/master/docs/Qlib%E5%86%85%E9%83%A8%E7%AD%96%E7%95%A5%E5%BC%80%E5%8F%91%E8%A7%84%E8%8C%83.md "QuantMind/docs/Qlib内部策略开发规范.md at master · qusong0627/QuantMind · GitHub"
[6]: https://developers.openai.com/codex/cli "CLI – Codex | OpenAI Developers"
[7]: https://developers.openai.com/codex/noninteractive "Non-interactive mode – Codex | OpenAI Developers"
[8]: https://developers.openai.com/codex/mcp "Model Context Protocol – Codex | OpenAI Developers"
[9]: https://github.com/qusong0627/QuantMind/tree/master/backend/services/engine "QuantMind/backend/services/engine at master · qusong0627/QuantMind · GitHub"
[10]: https://github.com/qusong0627/QuantMind/blob/master/docs/alpha158%E8%AE%AD%E7%BB%83%E8%AE%A1%E5%88%92.md "QuantMind/docs/alpha158训练计划.md at master · qusong0627/QuantMind · GitHub"
[11]: https://github.com/qusong0627/QuantMind/blob/master/docs/Qlib%E6%9E%B6%E6%9E%84%E4%B8%8E%E5%9B%9E%E6%B5%8B%E5%8E%9F%E7%90%86.md "QuantMind/docs/Qlib架构与回测原理.md at master · qusong0627/QuantMind · GitHub"
[12]: https://developers.openai.com/codex/subagents "Subagents – Codex | OpenAI Developers"
