# AGENTS.md

面向在本目录工作的 code agent（Claude Code、Codex 等）的约束。

## 边界

- 所有研究操作通过 `alphasieve` JSON CLI 完成；不要绕过 CLI 直接读写 ledger、registry 或 artifact。
- 评估、回测、gate 的计算只在后端代码中进行；agent 侧不重算、不改写指标。
- 研究循环中不得修改 `evaluation/`、`backtest/`、`gates/`、`ledger/` 的代码；这些改动属于工程变更，需要人工 review。
- 不得读取 holdout 区间或 fresh 前瞻区间的数据与指标。
- 每一次因子评估都必须经由唯一评估入口写入 trial ledger，不能手工补录或删除。

## 工程约定

- Python ≥ 3.11，src 布局，依赖用 `uv` 管理。
- CLI 命令都支持 `--json`，输出 machine-readable 的 status、metrics、artifact ids、error code；大结果写 artifact 并返回路径。
- 提交前运行 `uv run pytest`。
