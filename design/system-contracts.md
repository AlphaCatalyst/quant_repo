# System Contracts and State Machines

本文件定义低频量化研究 OS 的核心模块、对象和状态机。

## 1. 模块边界

```text
workspace
  -> question registry
  -> data contract service
  -> factor registry
  -> validation service
  -> evaluation service
  -> robustness service
  -> model/portfolio service
  -> review gate
  -> promotion service
  -> memory service
  -> report/card generator
```

### Workspace

职责：

- 保存研究文件、notebook、报告、图表、trace。
- 给 agent 提供可恢复上下文。
- 不直接负责指标计算。

### Data Contract Service

职责：

- 管理数据源、口径、PIT、lag、复权、universe。
- 在 factor evaluation 前做 data availability check。
- 输出 data contract 和 provenance。

### Factor Registry

职责：

- 保存 factor spec、版本、lineage、状态。
- 防止静默覆盖。
- 做 duplicate fingerprint。

### Validation Service

职责：

- parser。
- operator whitelist。
- leakage check。
- complexity check。
- lag check。
- sandbox shape check。

### Evaluation Service

职责：

- 计算 factor values。
- 计算 IC/RankIC、group return、turnover proxy、coverage。
- 输出 fast evaluation artifact。

### Robustness Service

职责：

- rolling/walk-forward。
- neutralization。
- regime/year/industry/size split。
- cost-adjusted evaluation。
- overfit risk。

### Model / Portfolio Service

职责：

- baseline comparison。
- feature snapshot materialization。
- model training。
- top-k / long-short backtest。
- portfolio metrics。

### Review Gate

职责：

- 把多个 report 汇总成 review packet。
- 根据 policy 产生 passed/warning/failed/insufficient_data。
- 记录人工审批。

### Promotion Service

职责：

- shadow feature。
- materialization。
- shadow training。
- approval request。
- rollback。
- paper handoff。

### Memory Service

职责：

- 保存失败、重复、泄露、低质量、过拟合、已接受模式。
- 给下一轮 candidate generation 提供约束。
- 不应该替代 registry；memory 是搜索辅助，registry 是事实记录。

## 2. 核心对象

### ResearchQuestion

```yaml
id:
title:
market:
universe:
frequency:
horizon:
hypothesis_family:
expected_direction:
evaluation_period:
constraints:
created_by:
created_at:
status:
```

### DataContract

```yaml
id:
market:
universe:
provider:
tables:
fields:
adjustment:
pit_policy:
lag_policy:
missing_policy:
tradability_policy:
provenance:
blocked_fields:
warnings:
```

### FactorSpec

```yaml
factor_id:
version:
name:
hypothesis:
family:
expression:
implementation_type:
data_sources:
required_lag_days:
universe:
frequency:
horizon:
direction:
neutralization:
missing_policy:
created_from:
owner:
status:
```

### ExperimentRun

```yaml
run_id:
factor_id:
question_id:
code_version:
data_contract_id:
start_time:
end_time:
run_type:
params:
metrics:
artifacts:
status:
failure_reason:
```

### ReviewPacket

```yaml
review_id:
factor_id:
question_id:
validation_report:
fast_evaluation_report:
robustness_report:
model_or_portfolio_report:
warnings:
failed_checks:
passed_checks:
recommendation:
reviewer:
decision:
decision_reason:
```

### PromotionRecord

```yaml
promotion_id:
factor_id:
source_review_id:
shadow_feature_version:
materialization_status:
training_run_id:
baseline_run_id:
approval_status:
paper_status:
rollback_status:
audit_log:
```

## 3. Factor 状态机

```text
draft
  -> validating
  -> validation_failed
  -> validated
  -> evaluating
  -> evaluation_failed
  -> evaluated
  -> robust_evaluating
  -> robust_failed
  -> reviewable
  -> rejected
  -> needs_repair
  -> approved_for_shadow
  -> shadow_promoted
  -> materialized
  -> shadow_trained
  -> approved_for_paper
  -> paper_active
  -> retired
  -> rolled_back
```

关键规则：

- `validation_failed` 不能直接进入 `evaluating`，必须 repair 或 override。
- `evaluated` 不能直接 `shadow_promoted`，必须经过 review。
- `approved_for_shadow` 不等于生产启用。
- `paper_active` 不等于 live。
- `rolled_back` 不能删除历史 artifact。

## 4. Gate Policy

Gate 分三层。

### Static Gate

阻断项：

- future returns as features。
- full-sample normalization。
- non-PIT fundamentals。
- unknown data lag。
- unsupported operator。
- expression parse failure。

### Evaluation Gate

默认指标：

- min coverage。
- max missing ratio。
- min RankIC mean。
- min RankICIR。
- min positive IC ratio。
- max turnover proxy。
- max correlation with existing factor。

### Robustness Gate

默认指标：

- min yearly positive ratio。
- min OOS RankIC。
- neutralized IC not collapsed。
- cost-adjusted long-short not collapsed。
- max drawdown within policy。
- no single-year dependence。
- no single-industry dependence。

Gate 输出：

```text
passed
warning
failed
insufficient_data
```

`passed` 只代表可以进入人工 review。

## 5. Artifact Contract

每次 run 至少保存：

- config snapshot。
- input data signature。
- factor values reference。
- metrics JSON。
- charts。
- logs。
- code version。
- environment summary。
- report markdown。

artifact 不应只存在 notebook 输出里。

## 6. Agent Boundary

Agent 可以做：

- 提 research question 草稿。
- 生成 factor candidate。
- 修复 validation error。
- 运行 evaluation。
- 总结 report。
- 查询 memory。

Agent 不可以默认做：

- 删除失败实验。
- 覆盖 factor 定义。
- 修改 data contract。
- 跳过 gate。
- 直接 promotion。
- 发布 live signal。
- 下单。

任何需要越权的动作都必须显式审批、记录 reason，并进入 audit log。

## 7. Storage 建议

SQLite / Postgres：

- questions。
- data_contracts。
- factor_specs。
- experiment_runs。
- review_packets。
- promotion_records。
- memory_items。

Parquet：

- market data。
- factor values。
- feature snapshots。
- model predictions。
- portfolio/signal panels。

Markdown：

- factor cards。
- experiment reports。
- review packets。
- postmortems。

JSON/YAML：

- configs。
- gate policies。
- run manifests。
