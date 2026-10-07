# 待办与待决定事项

汇总各文档中尚未关闭的事项，2026-10-07 整理。每条注明出处；事项关闭后在出处更新状态，并从本表删除（已作出的决定记入 [decisions.md](overview/decisions.md)）。

## 1. 待人工决定

### 1.1 个人股票账户（[personal-decision-tasks](personal/personal-decision-tasks.md) §9）

| 事项 | 建议 |
|---|---|
| 是否立项 P1–P7 及顺序 | P3 → P5 → P1 → P4 → P7 → P6 → P2；P6 的前瞻预测可先开始积累 |
| agent 挖因子从开放搜索改为预注册假设验证（§8.2） | 改；因子库与模型层保留，模型验收增加校准指标 |
| 合成账户的股票池 | 全 A（`ashare_all` dev panel 已有，含退市股票），按板块与市值分层报告 |
| 各任务的 dev 验收门槛、预测周期、风险厌恶系数、trial 预算与 holdout 读取次数 | 文中均为草案 |
| 历史公告回补（P4）、商品期货日线同步（P6）、全 A 缺失特征（两融历史等） | 真实历史回补需人工审查后运行 |
| 删除误标为 `citic` 的持仓快照（与“代买”重复） | 先备份状态库，临时解除只追加保护，只删这一条及其报告 |

### 1.2 个人账户覆盖与平台定位

| 事项 | 出处 |
|---|---|
| 账户层配置、论点仓位、小盘拥挤预警的立项顺序（建议 1 → 3 → 2）；初始风险预算与回撤阈值；红利税与申购提示是否进入 `book check` | [coverage-review](personal/coverage-review.md) §8 |
| 提醒推送渠道（企业微信、看板）；资产范围扩展先做港股、公募基金还是期货 CTA | [coverage-review](personal/coverage-review.md) §6 |
| 是否把定位扩展为个人广义量化平台，并同步修改 [product](overview/product.md) 的范围与非目标；核心与卫星仓位比例、单论点上限 | [broad-quant-platform](personal/broad-quant-platform.md) §10 |
| 是否立项个人账户 mandate（规模假设、股票池、验收门槛）；新资产类型顺序；是否接入实盘执行 | [personal-account](personal/personal-account.md) §6 |

### 1.3 研究、验收与数据

| 事项 | 出处 |
|---|---|
| 各 mandate 的策略层 holdout 读取（每个 mandate 一次，人工批准）；批准前需在本机构建 holdout 层的期货与 ETF 行业特征 | [acceptance-training](acceptance/acceptance-training.md) §5 |
| 数据采购：中证 500 全收益或官方权重、PIT 行业分类、ETF 历史持仓 | [acceptance-training](acceptance/acceptance-training.md) §5 |
| holdout 区间长度是否足够（Q-2）；L1 在 L3 下的重新校准（Q-3 遗留）；通知渠道（Q-7）；dev panel 进入 Nexus 沙盒（Q-9）；westock 历史指数成分（Q-10）；E3 沙箱实现（Q-11） | [decisions](overview/decisions.md) 待定问题 |
| forward 同机 root 风险是否接受，或换机器、容器隔离 | [forward-paper](mandates/forward-paper.md) §6 第 5 项 |

## 2. 立项后待实现

| 事项 | 出处 |
|---|---|
| PD-1 合成账户生成器、PD-2 决策回测器、PD-3 同频随机基线、PD-4 决策层记账、PD-7 虚拟账户 | [personal-decision-tasks](personal/personal-decision-tasks.md) §7 |
| 用交易记录补出历史持仓快照，使“净值与收益”“收益归因”有数据；快照来源需新增推算标记 | 2026-10-07 讨论 |
| 行为偏差统计（追涨、卖出后追回、频繁换仓、处置效应） | [broad-quant-platform](personal/broad-quant-platform.md) §5.7、[personal-decision-tasks](personal/personal-decision-tasks.md) P5 |

## 3. 文档维护

| 事项 | 说明 |
|---|---|
| 文档状态标注 | 每篇首行写明状态（已实现 / 部分实现 / 草案 / 评估）与日期；部分早期文档缺失 |
