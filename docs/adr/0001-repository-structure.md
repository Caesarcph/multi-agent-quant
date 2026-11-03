# ADR-0001：仓库结构——一个平台 + 一个引擎

- 状态：🟡 提议中（等你拍板）
- 日期：2026-11-03

## 背景

三个仓库各自为政：multi-agent-quant 能跑通完整链路；llm-rl-mt5-trading 体量最大但主程序起不来，而且和前者在 MT5 连接、风控、回测、报告、LLM 封装上大面积重复；llm-backtest-framework 只有 README。一个人维护三套 CI、三份依赖，成本很高。

## 选项

1. **一个平台 + 一个引擎（推荐）**：multi-agent-quant 升级成 monorepo 主平台；llm-backtest-framework 做成独立开源的 `llmbt` 回测库，主平台依赖它；llm-rl-mt5-trading 把有价值的模块迁走后归档。
2. **三个仓库 + 共享 core 包**：保留三个仓库，抽出一个共享的 `quant-core` 包。适合想把三个仓库分别当作品集展示的情况。

## 决定（建议）

选项 1。主平台已经按 uv workspace 组织好了（`packages/maq-core`、`packages/maq-cli`），后续包继续往 `packages/` 里加；回测引擎是通用工具，独立发布更容易被别人用起来。

## 后果

- 好处：一套 CI、一份锁文件；改跨模块的东西一次提交搞定。
- 代价：llm-rl-mt5-trading 的 star 和历史留在归档仓库里，README 要写清楚去向。
- 如果最后选了选项 2：`packages/maq-core` 可以原样搬成独立仓库，这一轮的工作不会白做。
