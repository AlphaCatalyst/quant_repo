# open_source Quant Repo Architecture Analysis

本文档组分析 `/data/codebase/quant_repo/open_source` 下 43 个开源量化/金融 agent 相关仓库的架构形态、问题域和量化研究思路。

分析口径：

- 基于本地仓库快照；最近一次全量同步上游为 2026-09-26，详见 [landscape-update-2026-09.md](landscape-update-2026-09.md)。
- 重点看 README、核心源码目录、agent/MCP/回测/风控/数据入口，而不是只按项目名归类。
- 目标不是评价哪个项目“最好”，而是抽象出可复用的系统设计模式。

## 文档地图

1. [architecture-taxonomy.md](architecture-taxonomy.md)
   - 从系统架构角度把 43 个仓库归成 9 类（含 2026-09 新增的 Minimal Research Harness 与 Evaluation Integrity 两类）。
   - 说明每类面向的问题、核心边界、典型数据流和代表项目。

2. [repo-inventory.md](repo-inventory.md)
   - 逐 repo 清单。
   - 每个仓库说明：定位、架构、量化思路、可借鉴点、主要风险。

3. [quant-patterns.md](quant-patterns.md)
   - 按量化研究方法归纳：因子挖掘、agent 研究循环、回测与 OOS、实盘执行、MCP 数据层、skill/SOP。
   - 适合用来设计自己的研究 OS。

4. [reference-priorities.md](reference-priorities.md)
   - 给出后续研究/复用优先级。
   - 区分“值得深读源码”“适合参考产品形态”“只适合做 skill 或 API schema 参考”。

5. [agent-platform-interfaces.md](agent-platform-interfaces.md)
   - 横向比较平台型工具如何接入 Codex、Claude Code、MCP、JSON CLI 和 LLM server。
   - 说明 LLM 在研究规划、候选生成、工具调度、结果解释、风控审查中的合理位置。

6. [agent-quant-os-blueprint.md](agent-quant-os-blueprint.md)
   - 抽象 agent + quant OS 的核心组件、标准 pipeline、human-in-the-loop 节点和可持续路线。
   - 给出合格形态、最小版本、成熟版本以及对 AlphaQuant 的落地建议。

7. [landscape-update-2026-09.md](landscape-update-2026-09.md)
   - 2026-09 生态刷新记录：同步结果、新增 13 个仓库及理由、刻意未收录的项目、honest evaluation 论文结论、网页描述与代码不符的勘误。

8. [factor-model-co-optimization-research.md](factor-model-co-optimization-research.md)
   - 因子与模型联合优化的研究综述：agent 框架的交替调度、挖掘与组合联合方法、模型侧证据（目标设计、复杂度争议、TSFM、增量学习、regime 失效）、已发表系统中的评估泄漏。

## 总体结论

这些仓库不是同一类东西。它们大致分成三条主线：

1. **研究引擎主线**
   - 目标是发现、验证、管理 alpha。
   - 代表：`RD-Agent`、`QuantaAlpha`、`QuantGPT`、`QuantMind-yj_exp`、`QuantMind-qm2`、`FactorMiner`、`AlphaEvo`、`QuantEvolver`、`AlphaAgent`、`EvoQuant`、`AgentQuant`、`llm-quant`、`automated-quant-research`；极简形态是 `Auto-Quant`、`autoresearch-trading`；评估诚实性侧有 `kph`、`deflated-sharpe`、`AlphaBench`。

2. **交易工作台/执行平台主线**
   - 目标是把策略从研究推进到 paper/live，并处理账户、订单、风控、审计。
   - 代表：`lumibot`、`QuantMind`、`QuantMind-yj_exp`、`QuantDinger`、`mmr`、`OpenAlice`、`QuantDesk`、`Vibe-Trading`、`AI-Trader`、`deepseek-harness-quant`、`TradingAgents`、`ai-hedge-fund`。

3. **agent 工具化主线**
   - 目标是让 AI agent 正确调用金融数据、平台 API 或领域 SOP。
   - 代表：`data-mcp`、`qlib-mcp`、`quantcontext-mcp-server`、`quantconnect-mcp-server`、`joinquant-skill`、`finlab-ai`、`worldquant-skill`、`kis-ai-extensions`、`revolut-x-api`。

最有研究系统参考价值的是 `RD-Agent`、`QuantaAlpha`、`QuantMind-yj_exp`、`QuantDesk`、`AgentQuant`、`llm-quant`、`Vibe-Trading`、`langalpha`。最值得作为执行/交易边界参考的是 `lumibot`、`mmr`、`OpenAlice`、`QuantMind`、`QuantDinger`。平台 skill 类项目的价值不在算法，而在“把平台约束做成 agent 可执行规则”。

2026-09 刷新后的补充结论：新进展集中在评估诚实性（`QuantMind-qm2`、`kph`、`mmr` 研究核、honest evaluation 论文）、带记忆的挖掘器（`FactorMiner`）和 code-agent 原生 harness（`Auto-Quant`、`deepseek-harness-quant`）三块，没有出现能整体取代 `QuantMind-yj_exp` / `QuantDesk` 的完整研究 OS；它们共同印证了“agent 只提案、确定性后端裁决”的路线。
