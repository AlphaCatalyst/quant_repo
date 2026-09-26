# Research Workflow

本文件定义低频量化研究 OS 的端到端 workflow。重点是每一步的输入、输出、检查项和失败处理。

## 0. 总流程

```text
Research Question
  -> Data Truth
  -> Candidate Spec
  -> Static Validation
  -> Fast Evaluation
  -> Robust Evaluation
  -> Ledger Gate + Locked Holdout
  -> Model / Portfolio Test
  -> Review Gate
  -> Shadow Promotion
  -> Fresh Forward
  -> Paper / Monitor
  -> Memory / Registry
```

Static Validation 到 Robust Evaluation 只使用 development 窗口，是 agent 可以自由循环的区域；Ledger Gate、Locked Holdout 和 Fresh Forward 按预算消耗，对 agent 不可见。分层 verifier 的完整说明见 [agent-loop-verification.md](agent-loop-verification.md)。

## 1. Research Question

目的：在生成因子前先定义研究边界。

必填：

- market：A 股、港股、美股等。
- universe：沪深 300、中证 500、全 A、行业池、自定义股票池。
- frequency：日频、周频、月频。
- horizon：1d、5d、20d forward return。
- hypothesis_family：价值、质量、动量、反转、资金流、事件、情绪、风险、微结构。
- expected_direction：越大越好、越小越好、非单调。
- evaluation_period：训练、验证、测试区间。
- constraints：禁止未来函数、禁止全样本标准化、交易成本假设、最小覆盖率。

输出：

- `research_question_id`
- research brief
- expected evidence checklist

失败处理：

- 如果 horizon、universe、frequency 未定义，不能进入 candidate。
- 如果 hypothesis 只是“找一个赚钱因子”，应退回重写。

## 2. Data Truth

目的：确认数据可用性和时点可得性。

检查项：

- provider。
- adjustment：前复权、后复权、不复权。
- timestamp：数据何时可见。
- PIT：财报、公告、成分股是否 point-in-time。
- survivorship：是否有幸存者偏差。
- missing policy：缺失、停牌、涨跌停、一字板。
- lag：财报、公告、事件、估值数据的 required lag days。
- provenance：本地缓存、外部 API、人工文件、生成文件。

输出：

- data contract。
- data availability summary。
- blocked fields。

失败处理：

- required field 不存在：candidate blocked。
- PIT 不明确：candidate blocked 或只能 draft。
- fallback 改变语义：必须记录 warning。

## 3. Candidate Spec

目的：把因子从“想法/字符串”变成结构化对象。

建议字段：

```yaml
factor_id:
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
owner:
created_from:
version:
```

AI 可以生成 candidate，但必须遵守 spec。系统不能只保存一段 prompt 或一个表达式字符串。

输出：

- factor spec。
- candidate lineage。
- duplicate fingerprint。

失败处理：

- expression 无法 parse：needs_repair。
- 基础字段超过白名单：rejected。
- 与已有 factor 高度重复：duplicate。

## 4. Static Validation

目的：在跑回测前拦截明显错误。

检查项：

- 未来字段。
- full-sample normalization。
- label 泄露。
- 横截面/时序算子混用。
- 窗口长度和 horizon 冲突。
- 复杂度过高。
- 数据源 lag 不满足。
- implementation 是否可沙箱执行。

输出：

- validation report。
- warnings。
- blocking reasons。

失败处理：

- blocking issue：进入 `needs_repair` 或 `rejected`。
- warning：允许 fast evaluation，但 review packet 必须展示。

## 5. Fast Evaluation

目的：低成本筛掉无效、重复、不可交易的候选。

指标：

- coverage。
- missing ratio。
- factor distribution。
- outlier ratio。
- IC / RankIC。
- ICIR / RankICIR。
- positive IC ratio。
- group return。
- long-short return。
- turnover proxy。
- max correlation with existing factors。
- yearly summary。

输出：

- fast evaluation artifact。
- metric summary。
- initial verdict：`rejected`、`needs_repair`、`robust_eval_candidate`。

失败处理：

- 覆盖率低：needs_data 或 rejected。
- IC 不稳定：needs_repair 或 rejected。
- 与已有因子重复：duplicate。
- turnover 太高：进入 warning 或 rejected，取决于定位。

## 6. Robust Evaluation

目的：验证 alpha 是否能跨时间、市场状态、行业/市值维度存在。

检查项：

- train/valid/test。
- rolling / walk-forward。
- yearly IC。
- regime split。
- industry neutralized IC。
- size neutralized IC。
- cost-adjusted long-short。
- decay / half-life。
- permutation / bootstrap。
- factor pool marginal contribution。

输出：

- robustness report。
- overfit risk score。
- promotion eligibility。

失败处理：

- 只在单一年份有效：rejected 或 watchlist。
- 中性化后消失：标记为 beta/style exposure。
- 成本后失效：只能保留为 research note，不进 promotion。

### 6.1 Ledger Gate + Locked Holdout

目的：对搜索强度做折扣，并用从未被查看过的数据做一次性确认。

检查项：

- DSR / BH-FDR，试验数与方差取自 trial ledger。
- 参数来源：默认参数，或有限次邻域救援；全量搜索得到的参数不合格。
- shortlist 已锁定，failure memory 已冻结。
- holdout 读取次数在预算内。

输出：

- ledger gate report。
- holdout report（只写入 ReviewPacket）。
- 状态：`holdout_passed` / `holdout_failed` / `holdout_contaminated`。

失败处理：

- ledger gate 不过：退回 development 窗口，候选与失败原因写入 memory。
- holdout 失败：`holdout_failed`，不得在同一段 holdout 上 repair 后重测。
- 违规读取或超预算：`holdout_contaminated`，同批证据作废。

## 7. Model / Portfolio Test

目的：验证新因子是否对模型或组合有边际贡献。

三条路径：

1. factor library only
   - 因子通过研究验证，但暂不进入模型。

2. model feature
   - baseline vs baseline + factor。
   - 关注 IC、rank loss、top-k、收益风险、稳定性。

3. portfolio signal
   - 单因子或多因子组合。
   - 关注换手、成本、回撤、容量、行业偏离。

输出：

- baseline comparison。
- model/portfolio artifact。
- marginal value summary。

失败处理：

- 单因子好但模型无边际贡献：保留 library，不 promotion。
- 模型指标好但 portfolio 差：进入 repair 或 strategy-level review。
- 只提高训练集：rejected。

## 8. Review Gate

目的：把指标证据转成人类可判断的 review packet。

Review packet 包含：

- research question。
- factor spec。
- data contract。
- validation report。
- fast evaluation。
- robustness report。
- model/portfolio comparison。
- warnings。
- blocking reasons。
- lineage 和 artifacts。
- 推荐动作。

状态：

```text
draft
  -> validated
  -> evaluated
  -> robust_evaluated
  -> reviewable
  -> rejected / needs_repair / approved_for_shadow
```

review 原则：

- gate passed 只表示可 review，不表示可生产。
- 默认拒绝，证据充分才 promotion。
- 所有人工决定必须记录 reason。

## 9. Shadow Promotion

目的：把研究产物放到生产前缓冲层。

路径：

```text
approved_for_shadow
  -> shadow_feature
  -> materialized_snapshot
  -> shadow_training
  -> baseline_comparison
  -> approval_request
```

检查项：

- feature snapshot 是否物化。
- training 是否读取到了新 feature。
- baseline 是否同窗口同配置。
- approval 是否通过。
- rollback 是否可用。

失败处理：

- 未物化：pending_materialization。
- 训练链路缺数据：blocked。
- baseline comparison 不足：needs_more_evidence。

### 9.1 Fresh Forward

目的：用上线后才产生的数据做最终验证，这是唯一不受历史查看和 LLM 预训练污染的证据。

检查项：

- 不回填历史，观察期至少 60 个交易日。
- cohort 级统计（HAC / BH），而不是逐个因子挑好看的。
- 按决策来源（机器 / 人 / AI）分池，追踪 T+1/5/20/60 等多个 horizon。

输出：

- fresh cohort report。
- 状态：`fresh_supported` / `fresh_failed`。

失败处理：

- `fresh_failed`：退役或降级为 research note，原因写入 memory。
- `fresh_supported` 不自动进入 paper，仍需人工审批 `approved_for_paper`。

## 10. Paper / Monitor

目的：让少量通过 gate 的产物进入真实交易前的观察。

检查项：

- signal drift。
- factor decay。
- hit rate。
- turnover。
- drawdown。
- exposure。
- capacity。
- data health。

输出：

- paper report。
- monitor alert。
- continue / retire / rollback decision。

边界：

- paper 不是 live。
- live 需要单独权限、审批和执行系统。
- 研究 OS 默认不持有 broker credential。

## 11. Memory / Registry

每次 workflow 都要写入：

- accepted factors。
- rejected factors。
- duplicate factors。
- failed hypotheses。
- blocked data fields。
- common repair actions。
- overfit patterns。
- review decisions。
- rollback history。

memory 的目标不是让 prompt 更长，而是减少重复试错，让搜索空间越来越干净。
