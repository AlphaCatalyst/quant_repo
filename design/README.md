# Low-Frequency Quant Research OS Design

本目录整理低频量化研究 OS 的产品定位、端到端 workflow、模块边界和演进路线。

核心判断：

- “挖因子 + 模型回归”只是研究 kernel，不是完整系统。
- 真正的系统价值在 kernel 外围：数据口径、实验证据链、反过拟合门禁、研究记忆、人工 review、shadow promotion、paper/live 边界。
- 合理定位不是全自动交易机器人，而是 human-in-the-loop 的低频量化研究工厂。

## 文档地图

1. [roadmap.md](roadmap.md)
   - 定位、非目标、设计原则、阶段路线。
   - 回答“这个 OS 应该先做什么、后做什么、不要做什么”。

2. [research-workflow.md](research-workflow.md)
   - 从 research question 到 paper/shadow 的完整 workflow。
   - 细化每一步的输入、输出、检查项、artifact 和失败处理。

3. [system-contracts.md](system-contracts.md)
   - 模块边界、核心对象、状态机、gate 和数据 contract。
   - 适合后续拆实现任务。

4. [agent-loop-verification.md](agent-loop-verification.md)
   - 让 agent 自主 loop 的路线：量化“容易打分、难以验证”，因此用 L0–L5 分层 verifier 管理 agent 的自由度与验证预算。
   - 说明角色边界、三层节奏、优化目标、问题选择、风险对策，以及与 roadmap 阶段和因子状态机的对应关系。

## 参考来源

本设计基于 `analysis/` 中对 open_source repo 的归纳（2026-09 刷新后为 43 个），尤其参考：

- `RD-Agent`：Qlib-first scenario、experiment、runner。
- `QuantaAlpha`：factor evolution、trajectory、regulator。
- `QuantMind-yj_exp`：Factor Lab、no-execute boundary、shadow feature/signal、promotion/approval/rollback。
- `llm-quant`：research governance、robustness gate、append-only registry。
- `QuantDesk`：Analyst/Risk Manager 分工、run history + code diff review。
- `langalpha` / `Vibe-Trading`：persistent workspace、artifact、tool routing。
- `joinquant-skill` / `finlab-ai` / `worldquant-skill`：平台 SOP、模板、lint。
- `QuantMind-qm2` / `kph`：trial ledger、locked holdout 预算、default-first 优化治理、harness 不重算（2026-09 补充）。
- `FactorMiner`：经验记忆与四级 admission（2026-09 补充）。

## 一句话定位

低频量化研究 OS 应该把 AI 放在“生成候选、执行实验、整理证据、复用记忆”的位置，把系统放在“数据真相、门禁、状态机、审计、promotion 边界”的位置，把人放在“研究方向、风险判断、最终审批”的位置。
