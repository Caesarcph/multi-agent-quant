# ADR-0002：Agent 框架——PydanticAI 2 + 自研轻量编排

- 状态：✅ 已采纳（2026-11-03）
- 日期：2026-11-03

## 背景

现在调 LLM 的方式是 CLI 子进程 + 正则提取 JSON + 让模型自己修 JSON，工具调用协议是手搓的。阶段 4 要换成原生结构化输出、原生工具调用、多模型并行、分歧时辩论。需要选一个 Agent 框架。

## 选项

| | PydanticAI 2 + 自研编排 | LangGraph 1.2 |
|---|---|---|
| 谁在用 | FinRobot V2 | TradingAgents、QuantAgent |
| 结构化输出 | 原生 `output_type`，各家（含 DeepSeek）统一接口 | `with_structured_output`，经 LangChain 模型封装 |
| 断点续跑 / 回放 | 交给领域事件日志（本来就要做） | 内置 checkpointer，可回放、可分叉 |
| 人工审批 | deferred tools | `interrupt()` |
| 测试 | 自带 TestModel / FunctionModel | 需要自己注入假模型 |
| 依赖 | 轻 | 带上 langchain-core |

## 决定

PydanticAI 2 做单个 Agent，编排自己写（asyncio + 领域事件日志）。理由：

1. 决策流程是固定 DAG 加有限轮辩论，用不上通用图引擎的大部分能力；
2. 领域事件日志要当唯一事实来源，再加一套 LangGraph checkpoint 等于两套状态；
3. 回测要逐根 K 线跑上万次，框架越薄越好控制录制回放。

想直接复用 TradingAgents 的图结构和 prompt，或者需要 LangGraph Studio 可视化调试时，改选 LangGraph。

## 后果

- 不管选哪个：手数、风控、下单都是确定性的 Python 代码，不进 LLM 回路。
- 这一轮已经有了离线测试 LLM 调用的基础设施（`tests/fakes/llm_server.py`），两个选项都能直接用。
