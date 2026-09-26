# AlphaSieve

AlphaSieve 是一个由 agent 驱动的低频量化研究系统：agent 大量生成因子与策略候选，分层 verifier 逐级筛选，只有经得住留出与前瞻验证的候选才会被晋升。

核心原则：

- 量化研究容易打分、难以验证。agent 只在便宜的验证层自由循环；搜索折扣、锁定留出、前瞻验证按预算消耗，且对 agent 不可见。
- agent 只提案，确定性后端裁决，人批准预算与资金。
- 第一阶段只做 A 股日频截面选股（指数增强），agent 的自主 loop 对象只有因子候选。

设计文档：

- [../design/agent-loop-verification.md](../design/agent-loop-verification.md)：分层 verifier（L0–L5）、角色边界、因子与模型联合优化
- [../design/system-contracts.md](../design/system-contracts.md)：核心对象、因子状态机、gate 与 artifact 契约
- [../design/strategy-scope.md](../design/strategy-scope.md)：兼容的策略类型与统一策略契约
- [../design/roadmap.md](../design/roadmap.md)：阶段路线

## 目录结构

```text
alphasieve/
  src/alphasieve/
    contracts/    FactorSpec、StrategySpec、TrialLedgerEntry 等 schema
    data/         DataContract、A 股日频 panel、PIT 口径、数据区间划分
    factors/      因子 DSL、算子、表达式树
    evaluation/   IC/RankIC、边际贡献、参考模型组、评估不变量
    ledger/       trial ledger、holdout 读取预算
    gates/        L0–L4 gate 与因子状态机
    backtest/     A 股日频截面执行模拟（T+1、涨跌停与停牌、成本）
    cli/          JSON CLI（agent 与人共用的唯一操作入口）
  tests/
  data/           本地数据（不入库）
  artifacts/      运行产物（不入库）
```

## 开发

```bash
cd alphasieve
uv sync
uv run alphasieve version --json
uv run pytest
```
