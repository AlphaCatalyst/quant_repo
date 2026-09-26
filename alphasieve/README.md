# AlphaSieve

AlphaSieve 是一个由 agent 驱动的低频量化研究系统：agent 大量生成因子与策略候选，分层 verifier 逐级筛选，只有经得住留出与前瞻验证的候选才会被晋升。

核心原则：

- 量化研究容易打分、难以验证。agent 只在便宜的验证层自由循环；搜索折扣、锁定留出、前瞻验证按预算消耗，且对 agent 不可见。
- agent 只提案，确定性后端裁决，人批准预算与资金。
- 第一阶段只做 A 股日频截面选股（指数增强），agent 的自主 loop 对象只有因子候选。

## 文档

实现文档在 [docs/](docs/README.md)：

| 文档 | 内容 |
|---|---|
| [01-product.md](docs/01-product.md) | 用户与角色、核心场景、范围、成功标准 |
| [02-architecture.md](docs/02-architecture.md) | 组件、进程、存储、技术选型、隔离边界 |
| [03-data.md](docs/03-data.md) | 数据源、panel、PIT、dev / holdout / fresh 区间 |
| [04-research-core.md](docs/04-research-core.md) | 研究对象、因子 DSL、指标、gate 阈值、ledger、状态机、回测 |
| [05-agent-harness.md](docs/05-agent-harness.md) | agent 循环、workspace、调度、记忆、可见性 |
| [06-interfaces.md](docs/06-interfaces.md) | JSON CLI、HTTP API、事件流、artifact |
| [07-frontend.md](docs/07-frontend.md) | 前端定位、页面、组件、技术栈 |
| [08-progress-and-interaction.md](docs/08-progress-and-interaction.md) | 进展展示、停滞诊断、指令与审批流程、通知 |
| [09-milestones.md](docs/09-milestones.md) | 里程碑、任务与验收 |
| [10-decisions.md](docs/10-decisions.md) | 决策记录与待定问题 |
| [11-factor-search-space.md](docs/11-factor-search-space.md) | 因子搜索空间：表达能力分级、派生变量、覆盖坐标 |
| [12-testing.md](docs/12-testing.md) | 系统测试 T0–T8 与 gate 校准 |
| [13-backtest.md](docs/13-backtest.md) | 回测 B1–B5 与 A 股成交规则 |
| [14-agent-execution.md](docs/14-agent-execution.md) | agent 执行后端：本机 CLI、Cursor、Nexus Cloud |
| [acceptance-m0-m2.md](docs/acceptance-m0-m2.md) | M0–M2 验收记录 |

设计论证在仓库的 [design/](../design/README.md)，调研依据在 [analysis/](../analysis/README.md)。

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
  docs/           实现文档
  data/           本地数据（不入库）
  artifacts/      运行产物（不入库）
  state/          SQLite 状态库（不入库）
  workspaces/     campaign 工作区（不入库）
```

后续里程碑新增的模块（models、campaigns、agent、memory、review、fresh、api、orchestrator）与前端工程 `web/` 见 [docs/02-architecture.md](docs/02-architecture.md) §2。

## 开发

```bash
cd alphasieve
uv sync
uv run pytest                                   # 合成数据上的全部测试
ALPHASIEVE_RUN_REALDATA=1 ALPHASIEVE_RUN_NETWORK=1 uv run pytest tests/test_real_data.py   # 真实数据核对
uv run ruff check src tests
```

## 快速上手（M0–M2）

存储根目录默认为本地 `/data/alphasieve`（`ALPHASIEVE_HOT_ROOT`）与 Ceph `/mnt/private_felixjjiang/alphasieve`（`ALPHASIEVE_STORE_ROOT`）；角色由 `ALPHASIEVE_ROLE` 指定（默认 `human`）。

```bash
uv run alphasieve init --json                                  # 建目录与状态库
uv run alphasieve data sync --dataset core --workers 6 --json  # BaoStock：成分、日线、指数（可续传）
uv run alphasieve data sync --dataset financials --json        # 季度财务（较慢，可续传）
uv run alphasieve data build-panel --json                      # 构建 dev / holdout panel 与质量报告
uv run alphasieve data status --json
uv run alphasieve library seed --json                          # 载入 18 个经典量价种子因子
uv run alphasieve factor validate examples/reversal_excess_3d.yaml --json
ALPHASIEVE_ROLE=agent uv run alphasieve factor eval examples/reversal_excess_3d.yaml --json
uv run alphasieve ledger stats --json && uv run alphasieve ledger verify --json
uv run alphasieve gate calibrate --random 200 --json            # 阈值校准报告
```

因子候选用 YAML 描述（字段见 `FactorSpec`，示例在 `examples/`）。`factor eval` 的退出码：0 表示通过 L0–L2，3 表示未通过 gate（结果已入账），4 表示角色无权限。
