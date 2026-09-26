# Agent Platform Interfaces

本文横向分析 `/data/codebase/quant_repo/open_source` 中偏“平台型”的工具：它们如何接入 Codex、Claude Code 等 code agent，如何接 LLM server，以及如何把 LLM 能力放进量化研究、回测和交易流程。

分析重点不是“哪个模型更强”，而是工程边界：agent 如何获得上下文、如何调用工具、如何运行代码、如何保存结果、如何避免越权交易。

## 1. 总体分类

| 类型 | 代表 repo | 接口形态 | LLM 主要职责 | 确定性系统职责 |
|---|---|---|---|---|
| 原生 code-agent workspace | `QuantDesk`、`OpenAlice` | 启动 `claude` / `codex` CLI，注入 prompt、workspace、MCP config | 写代码、调工具、解释结果、推进实验 | workspace、DB、sandbox、审批、日志 |
| LLM server / agent backend | `langalpha`、`RD-Agent`、`QuantGPT`、`AgentQuant` | 后端直接调用 LLM API、LangGraph、LiteLLM、LangChain 或自定义 planner | 规划、生成代码/因子/参数、反思迭代 | runner、backtest、artifact、memory、权限 |
| MCP tool layer | `data-mcp`、`qlib-mcp`、`quantcontext-mcp-server`、`quantconnect-mcp-server`、`revolut-x-api` | 标准 MCP stdio/HTTP tool server | 由外部 agent 调度工具 | 数据查询、Qlib、回测、平台 API、订单 API |
| JSON CLI surface | `mmr`、部分 `revolut-x-api` | 给 agent 一个稳定 CLI，所有输出是 JSON | 通过 Bash/CLI 查询状态、提出 proposal、读取 diff | 交易服务、risk gate、proposal 状态机 |
| Skill / SOP packaging | `joinquant-skill`、`finlab-ai`、`worldquant-skill`、`kis-ai-extensions`、`Qlib-with-Claudex` | `SKILL.md`、rules、模板、lint、可选 MCP | 遵循平台规则生成策略/因子/命令 | API reference、模板、校验器、脚本 |

这些模式可以组合。比如 `kis-ai-extensions` 同时有 skills、MCP、hooks 和 agent-specific config；`revolut-x-api` 同时提供 typed client、CLI、MCP 和 skills；`langalpha` 同时是 LLM backend 和 MCP consumer。

## 2. 原生 Code Agent Workspace

### QuantDesk

`QuantDesk` 是最清晰的 code-agent 实验工作台形态。服务端不自己实现一个完整 agent loop，而是按 turn 启动原生 CLI：

```text
user / system comment
  -> triggerAgent(experimentId, role)
  -> spawn claude or codex CLI
  -> stdin prompt
  -> JSONL stream back to server/UI
  -> agent calls MCP tools through per-turn config
  -> DB/run/artifact/session_id persisted
```

关键接口：

- Claude 适配器：`claude -p - --output-format stream-json --verbose --mcp-config <per-turn config>`。
- Codex legacy 适配器：`codex exec --json [resume <sessionId>] -`。
- MCP 入口：服务端提供 in-process `POST /mcp`，per-turn config 带 `X-QuantDesk-Experiment` / `X-QuantDesk-Desk` header，确保工具副作用落到当前 desk/experiment。
- 工具契约：`run_script`、`register_dataset`、`run_backtest`、`record_run_metrics`、`request_validation`、`submit_rm_verdict` 等。

LLM 的作用：

- Analyst agent 写 fetcher/strategy/backtest 代码。
- 调 `run_script` 做数据准备，调 `run_backtest` 做最终评估。
- 根据 raw stats 选择要发布的 strategy-specific metrics。
- Risk Manager agent 独立读 run history、code diff、analyst trail，提交 approve/reject verdict。

边界设计：

- agent 写的脚本必须走 `run_script` 或 `run_backtest`，不能直接用 Bash 执行，保证 sandbox 和日志一致。
- 需要生命周期变更或数据获取时走 conversational approval：先问用户，下一 turn 才调用工具。
- RM 审批只解锁 paper trading，不等于直接 live execution。

可借鉴点：这是“个人研究 OS + code agent”的强样本。它把 LLM 放在研究执行层，把状态、审计、回测、审批留在服务端。

### OpenAlice

`OpenAlice` 的思路是把 code agent 当 workspace substrate，而不是把 LLM 包成一个普通 API：

```text
task workspace
  -> git repo / files / terminal session
  -> native claude/codex/opencode CLI
  -> .mcp.json inject tools
  -> Alice process owns research/workspace/UI
  -> UTA service owns broker/trading state/guards
```

LLM 能力主要用于研究、写代码、整理文档、通过 MCP 调工具。交易执行不是 agent 直接下单，而是走 trading-as-git / guard / approval pipeline。

可借鉴点：如果目标是让 Codex/Claude Code 深度参与研究，workspace-first 比“自己重写一个弱版 code agent”更稳。

## 3. LLM Server / Agent Backend

### langalpha

`langalpha` 是最重的平台化 LLM backend。它不是启动外部 Codex/Claude Code，而是在后端组织 agent runtime：

```text
FastAPI thread message
  -> resolve LLM / credit / workspace
  -> deepagents.create_agent()
  -> middleware stack
  -> execute_code in Daytona sandbox
  -> generated MCP wrappers
  -> SSE events / Redis replay / Postgres persistence
```

关键接口：

- 多 provider model layer，支持 OpenAI/Claude/Kimi/GLM/MiniMax 等。
- MCP servers 作为 stdio subprocess 管理，`MCPRegistry` 维护连接。
- `ToolFunctionGenerator` 生成 Python wrapper，上传到 Daytona sandbox。
- Agent 不直接把大表塞进上下文，而是通过 `execute_code` 写 Python，在 sandbox 内调 MCP-backed functions。

LLM 的作用：

- 规划投研任务。
- 写 Python 处理数据、画图、做多步计算。
- 调外部工具和 MCP wrapper。
- 生成报告、artifact、memo metadata、thread 标题等。

边界设计：

- 大数据处理留在 sandbox，不进模型上下文。
- Flash mode 不启 sandbox/MCP/subagent，用于快速回答。
- SSE 事件持久化和 Redis replay 支持长任务/断线恢复。

可借鉴点：适合参考“长期投研 workspace + sandbox + MCP + memory”的完整平台形态，但对个人低频研究 OS 来说实现成本较高。

### AgentQuant

`AgentQuant` 是轻量 LLM planner 型：

```text
regime context
  -> planner proposes parameters
  -> deterministic backtest tournament
  -> reflection
  -> SQLite memory
```

它支持 Gemini/OpenAI/Ollama/LangChain planner，并保留 fallback grid search。LLM 不自由生成任意策略，而是在 canonical parameter grid 内提出候选参数。

可借鉴点：grid-constrained LLM 很适合低频策略调参。它限制搜索空间，避免 agent 生成不可执行或不可比较的东西。

### RD-Agent / QuantGPT

`RD-Agent` 和 `QuantGPT` 更偏因子/模型研究引擎：

- `RD-Agent` 通过 scenario、coder、runner、workspace template，把 Qlib 因子/模型研发变成 LLM 可执行任务；LLM 生成 hypothesis 和代码，runner 执行 Qlib workflow。
- `QuantGPT` 通过表达式 parser、MCP tools、anti-overfit、rolling validation、knowledge base，让 LLM 自动设计、诊断、迭代因子。

可借鉴点：LLM 负责生成和解释，runner 负责真值验证。因子系统必须有 parser/schema/validator/backtest/memory，不能只保存 prompt 产物。

## 4. MCP Tool Layer

MCP 类 repo 的价值不是“它自己有多智能”，而是把金融能力变成 agent 可发现、可校验、可审计的 typed tools。

| repo | MCP 暴露内容 | 适合用途 |
|---|---|---|
| `data-mcp` | wiki、paper、crypto、equity、macro、SEC、13F、ETF、Polymarket 等数据 | 通用金融数据入口 |
| `qlib-mcp` | `qlib_init`、下载数据、列 instruments、取数据、factor analysis、TopK backtest、expression help | Qlib 最小 agent surface |
| `quantcontext-mcp-server` | `screen_stocks -> backtest_strategy -> factor_analysis` | 确定性研究 pipeline |
| `quantconnect-mcp-server` | account/project/files/compile/backtest/optimization/live/object store 等平台工具 | QuantConnect/LEAN 云端操作 |
| `revolut-x-api` | account、market、order、trade、monitor、grid strategy | crypto 交易连接器 |

良好的 MCP 工具应具备：

- 明确输入 schema 和默认值。
- read-only / destructive / open-world 等行为提示。
- 对大结果使用 artifact path 或压缩摘要。
- 返回稳定 JSON，而不是让 agent 解析自由文本。
- 交易、写入、删除类工具有额外确认或 guard。

对 AlphaSieve 这类低频研究 OS，MCP 最适合放在这些边界：

- 数据查询：市场数据、财务数据、公告、研报、宏观。
- 因子分析：IC、RankIC、分组收益、换手、暴露。
- 回测：TopK、组合构建、交易成本、基准对比。
- 实验管理：写入 SQLite、生成报告、读取 artifact。

## 5. JSON CLI Surface

`mmr` 代表另一种更轻的方式：不做 MCP，直接给 code agent 一个稳定 CLI，所有命令返回 JSON。

```text
Claude Code
  -> Bash: mmr --json portfolio-snapshot
  -> Bash: mmr --json portfolio-risk
  -> Bash: mmr --json proposal-create ...
  -> human / policy approval
  -> execution service
```

LLM 的职责是 monitor、analyze、propose、digest。它不能直接绕过 proposal pipeline 下单。

这种方式的优点：

- 对 Codex/Claude Code 极友好，Bash 就能用。
- 易测试、易记录、易做权限隔离。
- 不依赖 MCP 客户端是否支持某些高级特性。

缺点：

- tool discovery 弱于 MCP，需要 `CLAUDE.md` / `AGENTS.md` / help 文档写得很清楚。
- 多工具参数组合复杂时，schema 校验体验不如 MCP。

对 AlphaSieve，JSON CLI 是最小可行接口：`alphasieve factor eval --json ...`、`strategy backtest --json ...`、`experiment report --json ...` 比先做完整平台更务实。

## 6. Skill / SOP Packaging

Skill 类 repo 把平台知识变成 agent 可执行流程：

| repo | 核心资产 | 作用 |
|---|---|---|
| `joinquant-skill` | API reference、模板、lint、MCP tools | 防止生成不存在的 JoinQuant API 和未来函数 |
| `finlab-ai` | 数据表说明、`sim()` 示例、最佳实践 | 让 agent 正确使用 FinLab |
| `worldquant-skill` | factor_backtest、recorder、knowledge search、Rule of 8 | 让 agent 按 WQ BRAIN SOP 迭代 |
| `kis-ai-extensions` | `.codex` / `.claude` / `.cursor` 配置、skills、hooks、MCP | 面向不同 code agent 的平台插件包 |
| `Qlib-with-Claudex` | Qlib/RD-Agent 模板和 Claude workflow | 快速启动 Qlib agent loop |

这类项目说明一个关键点：平台正确性不是 prompt 能解决的，必须通过 reference routing、模板、lint、hooks、MCP/CLI 来约束。

## 7. LLM 能力在量化中的真实位置

这些 repo 的共同经验是：LLM 不应该作为指标真值、回测真值或交易权限本身。它适合放在以下位置：

1. 研究规划：把用户目标拆成数据、假设、实验、指标。
2. 候选生成：因子表达式、策略代码、参数组合、实验配置。
3. 工具调度：决定何时取数、评估、回测、生成报告。
4. 结果解释：解释 IC、回撤、换手、暴露、交易分布、失败原因。
5. 反思迭代：根据失败结果做 mutation、crossover、参数调整。
6. 审查辅助：独立 Risk Manager、overfit review、代码 diff review。
7. 知识沉淀：把实验结果写入 memory、报告、知识库和失败案例。

确定性系统必须负责：

- 数据对齐和 point-in-time 约束。
- 因子 parser/schema/static validation。
- 回测、成本、组合构建、风控。
- 权限、审批、审计日志。
- artifact 和 experiment registry。

## 8. 对低频量化研究 OS 的建议路线

最稳的形态不是直接复制某个大平台，而是分层组合：

```text
Codex / Claude Code / optional LLM server
  -> Skill / AGENTS.md / workflow SOP
  -> JSON CLI first
  -> MCP tools for typed data and evaluation
  -> deterministic AlphaSieve services
  -> Qlib / SQLite / Parquet / reports / artifact registry
```

建议优先级：

1. 先把 CLI JSON surface 做稳：factor eval、strategy backtest、experiment report 都能结构化输出。
2. 再把低频研究流程写成 skills：因子设计、因子评估、策略调参、报告生成。
3. 对高频调用或强 schema 的能力做 MCP：数据查询、因子分析、回测、实验 registry。
4. 只有当需要多用户、Web UI、长任务、断线恢复时，再做 `langalpha` 式 LLM server。
5. 交易执行必须晚于 research OS：先 proposal/paper/shadow，再考虑 live。

一句话总结：平台型 repo 的核心价值不是“LLM 替你交易”，而是把 LLM 放进一个有工具、有边界、有审计、有反馈的研究循环里。
