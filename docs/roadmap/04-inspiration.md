# 04 · 开源灵感地图

[← 返回总览](README.md)

> 调研日期：2026-11-03。版本号和日期以 PyPI、GitHub Release 页为准；star 数是约数，只用来感受热度。
> 标了"（未核实）"的条目只有二手来源，用之前请再确认一次。

---

## 先看大势：三条结论

调研了几十个项目和二十多篇 2025–2026 年的论文，归纳下来有三条，它们直接塑造了这份计划：

1. **"LLM 负责想，代码负责算和管"已经是共识。** 现在最受欢迎的几个开源 LLM 交易系统（TradingAgents、ai-hedge-fund、Vibe-Trading、FinRobot）都在往同一个方向走：数字全部交给 Python 算，下单要过确定性的闸门，每一步都留审计记录。
2. **通用能力强 ≠ 会交易。** 多个实盘 / 前向基准（LiveTradeBench、StockBench、DeepFund、Agent Market Arena）得出了同样的结论：模型榜单排名预测不了交易表现；大多数 LLM Agent 跑不赢"买入持有"；**Agent 的架构设计比底座模型更重要**。
3. **前视偏差是 LLM 回测的头号陷阱。** 模型在训练时可能已经"见过"回测区间的行情和新闻。有研究发现，测试区间一旦挪到模型知识截止日之后，Agent 的夏普比率普遍会掉一半以上。

---

## 一、LLM 多智能体交易系统

| 项目 | 一句话介绍 | 我们借什么 | 落到哪个任务 |
|---|---|---|---|
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) · Apache-2.0 · v0.7.1（2026-10） | 目前最火的开源"AI 交易公司"，基于 LangGraph：分析师并行 → 多空辩论 → 交易员 → 风控团队 → 组合经理 | 角色分工与辩论；**决策日志自动打分 + 复盘**；按日期限定数据的网格回测（下一根收盘价入场）；"快 / 慢"两档模型分别配置 | P4-03、P4-04、P4-09 |
| [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) · MIT · v0.1.18（2026-10） | 港大 HKUDS 的个人交易 Agent，CLI + Web + 桌面端 | **整套安全设计**：按能力分级的券商配置（甚至直接有 `mt5-paper` / `mt5-live-readonly` / `mt5-live-trade` 三档）、实盘下单需要授权书、急停、审计账本、订单先落盘再发送、**接地校验**（LLM 给出的数字在数据里找不到就拒绝）、每次运行的 token 统计 | P3-06、P3-10、P4-14 |
| [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) · MIT | "投资大师人格"Agent → 风控 → 组合经理，现在打包成了 `aihf` 终端应用 | 基金授权、**纸面交易前展示完整决策并人工审批**、急停、**哈希链账本**、对基准的回测 | P3-11、P3-12 |
| [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) · Apache-2.0 | 股票研究 Agent，V2 基于 PydanticAI，带多空 + 裁判辩论 | 核心原则：**计算全部是带出处的 Python 代码，LLM 只负责推理和写作** | 设计原则 6、ADR-002 |
| [NOFX](https://github.com/NoFxAiOS/nofx) · AGPL-3.0 | Go 写的交易系统，分"逐笔确认"和"自动驾驶"两种模式 | 两种运行模式；止损止盈直接挂在交易所端；TWAP 拆单 | P3-11 |
| [nof0](https://github.com/wquguru/nof0) · MIT | nof1 Alpha Arena 的开源复刻（Go + Next.js） | 每个模型一个独立账户、实时收益排行榜、能看到每个模型的推理过程 | P8-02、控制台"模型擂台"页 |
| [ContestTrade](https://github.com/FinStep-AI/ContestTrade) · Apache-2.0 | 数据 Agent 产出文本"因子"，研究 Agent 各持一套"信念"，内部竞赛选优 | **按已实现成绩给分析师加权**，而不是固定权重 | P4-08 |
| [QuantAgent](https://github.com/Y-Research-SBU/QuantAgent)（现名 QuantHarness）· MIT | LangGraph 实现，趋势分析师直接"看 K 线图片" | 多模态技术分析师（P2 探索） | P4-03 的扩展 |
| [RD-Agent(Q)](https://github.com/microsoft/RD-Agent) · MIT | 微软的研发 Agent，在 Qlib 上让因子和模型共同演化（NeurIPS 2025） | 离线挖因子，产出的因子喂给快照 | P2 探索 |
| [Agentic Trading Lab](https://github.com/Open-Finance-Lab/AgenticTrading) · OpenMDW | Agent API + 回测 + **每晚的前向测试排行榜** | 前向排行榜的设计 | P8-02 |
| [ValueCell](https://github.com/ValueCell-ai/valuecell) · Apache-2.0 | 多智能体金融应用（研究、策略、新闻） | API 密钥只存在本机 | 配置原则 |
| [TradingAgents-CN](https://github.com/hsliuping/TradingAgents-CN) | TradingAgents 的中文 A 股版，FastAPI + Vue 3 | Web UI 可以参考。**注意它是混合许可，部分代码闭源且禁止商用，不要拷代码** | 只看不抄 |

**老项目（只借思路，代码基本不维护了）：** [FinMem](https://github.com/pipiku915/FinMem-LLM-StockTrading)（分层记忆）、[FinCon](https://arxiv.org/abs/2407.06567)（通过自我批判更新投资信念）、[FinGPT](https://github.com/AI4Finance-Foundation/FinGPT)（2023 年后基本停更）。

---

## 二、评测基准与研究结论

| 基准 / 论文 | 怎么测的 | 结论 | 对我们的启发 |
|---|---|---|---|
| [LiveTradeBench](https://github.com/ulab-uiuc/live-trade-bench)（arXiv 2511.03628） | 21 个 LLM 实盘跑 50 天 | 模型在 LMArena 上的排名预测不了交易表现 | 选模型靠自己的前向测试（模型擂台） |
| [StockBench](https://github.com/ChenYXxxx/stockbench)（arXiv 2510.02209） | 选在模型截止日之后的多个月 | 大多数 Agent 跑不赢买入持有 | 买入持有必须是回测的默认基线 |
| FINSABER（arXiv 2505.07078，KDD 2026） | 20 年数据、更大的股票池 | 时间拉长、范围放大以后，LLM 的优势消失；牛市太保守、熊市太激进 | 回测要覆盖多种行情 |
| Agent Market Arena（arXiv 2510.11695） | 4 种 Agent 架构 × 5 个底座模型，持续实盘 | **架构比底座模型更重要** | 精力优先投在架构上 |
| [DeepFund](https://github.com/HKUSTDial/DeepFund)（NeurIPS 2025） | 只用每个模型截止日之后的数据 | 连前沿模型也亏钱 | 截止日防护是刚需 |
| [AI-Trader](https://github.com/HKUDS/AI-Trader)（arXiv 2512.10971） | 美股、A 股、加密货币实盘 | 风控决定稳健性 | 风控优先（阶段 3 排在 Agent 2.0 前面） |
| [nof1 Alpha Arena](https://nof1.ai) | 真金白银，每个模型 1 万美元 | 第一季各模型结果高度分化，盈亏差距非常大 | 短期排名噪声很大，前向测试至少要 4 周 |
| Profit Mirage（arXiv 2510.07920） | 跨越截止日前后对比 | 过了截止日，Agent 的夏普衰减超过 50% | 核心指标只看截止日之后 |
| Lopez-Lira 等（arXiv 2504.14765） | 让模型回忆历史数据 | 模型能背出截止日前的宏观数据、收益和新闻标题；在 prompt 里说"请尊重日期"没用；把公司名打码也没用，模型能从上下文还原出来 | 不能靠提示词解决前视偏差 |
| Look-Ahead-Bench（arXiv 2601.13770） | 在 ai-hedge-fund 框架里比较样本内外 alpha | 普通模型普遍有明显的前视偏差，**DeepSeek 3.2 是落差最大的之一** | P0-12、llmbt 的截止日防护 |
| HindsightBench（arXiv 2607.18867） | 只用 API 做审计，四种 prompt 变体对比 | 能估出模型"行为上的有效截止日"；服务端的量化部署甚至会改变结果 | llmbt 的泄漏探针 |
| 时点模型（ChronoGPT、PiT LMs 等） | 按年份训练的一系列模型 | 天然没有前视偏差 | 作为回测的对照组 |
| Poisoning Agentic Alpha（arXiv 2608.24069） | 在多智能体交易系统里注入对抗信号 | **多数投票非但挡不住，反而会把被投毒的决定"焊死"** | P4-04、P4-15 |
| RL 交易综述（arXiv 2512.10913，167 篇） | 系统性回顾 | 实现质量和对市场微观结构、风险的理解，比选什么算法更重要 | RL 重新定位 |
| When Valid Signals Fail（arXiv 2604.10996，ICML'26） | LLM 特征喂给 PPO | 宏观冲击期间反而比只看价格更差 | 任何 LLM 特征都要分行情做消融 |
| 时序大模型的收益率预测（arXiv 2511.18578 等） | Chronos、TimesFM 零样本预测日收益 | 样本外 R² 为负，打不过 LightGBM | 只拿来做波动率特征 |

---

## 三、Agent 框架与基础设施

### Agent 框架（2026-11 版本）

| 框架 | 版本 | 特点 | 适合我们吗 |
|---|---|---|---|
| [PydanticAI](https://github.com/pydantic/pydantic-ai) | 2.x（v2 于 2026-06 发布） | 原生类型化输出，多厂商（含 DeepSeek），内置 Temporal / DBOS / Prefect 持久化，可延迟执行的工具做审批，自带 Evals，OTel + genai-prices 算成本 | ✅ 推荐（见 02 的 ADR-002 预案） |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 1.2.x | 图编排，checkpoint 落 SQLite / Postgres，可回放、可分叉，`interrupt()` 审批 | ✅ 备选，TradingAgents 用的就是它 |
| OpenAI Agents SDK | 0.24 | guardrails、session、tracing；通过 LiteLLM 支持其他厂商；Temporal 集成已 GA | 可以，但多厂商是"二等公民" |
| Claude Agent SDK | 0.2.x | 只支持 Claude；有预算上限、会话恢复、进程内 MCP 工具 | 适合单独做"研究助手"，不适合当主框架 |
| Google ADK | 2.13 | 2.0 引入图工作流；Gemini 优先 | 不推荐 |
| Microsoft Agent Framework | 1.24 | AutoGen + Semantic Kernel 的继任者，Anthropic / Gemini 连接器还在 beta | 不推荐 |
| Agno / CrewAI | 3.1 / 1.15 | 平台化、角色化，较重 | 不推荐 |
| DSPy | 3.4 | 自动优化 prompt（如 GEPA） | **以后有了足够多已结算的决策数据**，可以拿来自动调 prompt |

### 协议

- **MCP**：最新规范是 [2026-07-28 版](https://blog.modelcontextprotocol.io/posts/2026-07-28/)。变化比较大：协议改成无状态、Tasks 成为正式扩展、旧的 HTTP+SSE 传输弃用（还有 12 个月过渡期）。MCP 现在归 Linux 基金会旗下的 Agentic AI Foundation 管理。
- **A2A**：v1.0 已在 2026 年 3 月发布，但单进程系统用不上。
- **MT5 相关的 MCP**：
  - **MT5 终端自带**：[build 6060（2026-07-23）](https://www.metatrader5.com/en/releasenotes/terminal/2447)起原生支持 MCP，Codex、Claude Code 这类外部 Agent 可以直接连。
  - 第三方：[ariadng/metatrader-mcp-server](https://github.com/ariadng/metatrader-mcp-server)（**没有鉴权，自带的"交易助手"会不经确认直接下单，只能用只读工具**）、[Qoyyuum/mcp-metatrader5-server](https://github.com/Qoyyuum/mcp-metatrader5-server)。
  - 其他券商：Alpaca 有官方 MCP；IBKR 官方 MCP 在 2026 年 7 月对外开放。

### 模型网关与成本

| 工具 | 现状 | 建议 |
|---|---|---|
| [LiteLLM](https://github.com/BerriAI/litellm) | 功能最全的代理：虚拟 key、花费统计、预算 | **2026 年 3 月出过供应链投毒事件**（1.82.7 / 1.82.8 已从 PyPI 撤下）。要用就锁死版本 + 哈希 |
| [Portkey](https://github.com/Portkey-AI/gateway) | 2026 年 3 月起网关完全开源 | 备选 |
| OpenRouter | 托管服务，每次返回都带成本和缓存信息 | 想少维护几个 key 时可以用 |
| Langfuse v4 | 自带 AI Gateway | 已经用 Langfuse 的话可以顺手用 |
| TensorZero | **2026 年 6 月已归档** | 不要选 |

### 各家结构化输出的支持情况

| 厂商 | 怎么用 |
|---|---|
| OpenAI | Responses API 的 `json_schema` 严格模式；工具也支持 strict |
| Anthropic | `output_config.format`（JSON Schema）+ 工具上的 `strict: true` |
| Google Gemini | `response_json_schema`；工具用 `parameters_json_schema` |
| DeepSeek | **只有 `json_object` 模式，不强制 Schema**，偶尔还会返回空内容——必须保留校验和重试 |

### 可观测性、评测、持久化

| 类别 | 选项 | 备注 |
|---|---|---|
| 观测 | Langfuse v4（MIT 核心，可自托管）、Pydantic Logfire（托管）、Arize Phoenix | Phoenix 是 Elastic-2.0 许可；OTel 的 GenAI 语义约定还标着 Development |
| 评测 | promptfoo、Inspect AI、DeepEval、Pydantic Evals | promptfoo 2026 年 3 月被 OpenAI 收购，仍然开源 |
| 持久化执行 | DBOS（一个库，跑在 SQLite / Postgres 上）、Temporal、Restate | 单机场景 DBOS 最轻 |

---

## 四、回测引擎与量化工具

> 详细的竞品分析和 llmbt 的定位见 [llm-backtest-framework 的 ROADMAP.md](https://github.com/Caesarcph/llm-backtest-framework/blob/master/ROADMAP.md)。

| 项目 | 版本 | 一句话 | 许可证 | 我们怎么用 |
|---|---|---|---|---|
| [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) | 1.231 / 2.0.0rc8 | 事件驱动、纳秒级、Rust 内核；v2 去掉了 Cython，只支持 Python 3.13+；**没有官方 MT5 适配器** | LGPL-3.0 | llmbt 的可选高保真后端；Broker 接口设计的参考 |
| [vectorbt](https://github.com/polakowo/vectorbt) | 1.2.0 | 向量化，1.0 起有可选的 Rust 引擎 | **Apache-2.0 + Commons Clause**（限制商用） | 只用于对缓存好的决策流做参数扫描 |
| [LumiBot](https://pypi.org/project/lumibot/) | 4.6.x | **能在回测里跑 LLM Agent，带 `as_of` 上下文和回放缓存**，支持外汇 | GPL-3.0 | **llmbt 最直接的竞品** |
| [FINSABER](https://github.com/waylonli/FINSABER) | 2.0.1 | 专门的 LLM 策略回测器，有执行时机、滑点、流动性上限和 LLM 成本统计 | Apache-2.0 | 竞品，方法论值得参考 |
| [ziplime](https://github.com/Limex-com/ziplime) | 2.11 | 用 Polars + Parquet + asyncio 重写的 Zipline，自带 MCP | AGPL-3.0 | 参考 |
| [freqtrade](https://github.com/freqtrade/freqtrade) | 2026.10 | 加密货币；自带 `lookahead-analysis`、`recursive-analysis` 两个防偏差命令 | GPL-3.0 | 防偏差工具的设计参考 |
| [hftbacktest](https://github.com/nkaz001/hftbacktest) | 2.4.4 | tick 级订单簿、排队位置、延迟模型 | MIT | 延迟建模的参考 |
| [Qlib](https://github.com/microsoft/qlib) | 0.9.7 | 横截面机器学习工作流 | MIT | 参考 |
| backtrader | 2023 年后停更 | — | GPL-3.0 | 避开 |
| backtesting.py | 0.6.6 | 简单好用 | AGPL-3.0 | 许可证太严，只参考 |

**统计检验**：[skfolio](https://pypi.org/project/skfolio/)（walk-forward、组合清洗交叉验证）、[RiskLabAI](https://pypi.org/project/RiskLabAI/)（PBO、PSR、DSR，目前维护最完整的 López de Prado 工具箱）、[arch](https://pypi.org/project/arch/)（SPA、StepM、MCS，比较多个 prompt / 模型变体时做多重检验）。mlfinlab 已经闭源。

**绩效报告**：[quantstats](https://pypi.org/project/quantstats/)（2026 年重新活跃，新增蒙特卡洛）、pyfolio-reloaded、empyrical-reloaded。

**数据栈**：Polars 2.x（2.0 默认开启外存计算）、DuckDB 2.0 LTS、pandas 3.x、PyArrow。FX 历史数据：MT5 导出（券商自己的点差）+ [dukascopy-python](https://pypi.org/project/dukascopy-python/)（免费 bid/ask tick）。OpenBB 的 SDK 已更名为 Open Data Platform；yfinance 只适合原型。

---

## 五、RL、时序大模型、本地模型

> 详细分析见 [llm-rl-mt5-trading 的 UPGRADE_PLAN.md](https://github.com/Caesarcph/llm-rl-mt5-trading/blob/main/docs/UPGRADE_PLAN.md)。

- **RL 库**：Stable-Baselines3 2.9.2（2026-10）、Gymnasium 1.4（1.0 起 autoreset 语义和 VectorEnv 都变了）、TorchRL 0.14、RLlib（单品种场景太重）。
- **FinRL 的转向**：[FinRL-X](https://github.com/AI4Finance-Foundation/FinRL-Trading) 把深度强化学习降级成"资产配置"环节里可替换的一种方法，和等权、均值方差并列。
- **时序大模型**：

  | 模型 | 许可证 | 备注 |
  |---|---|---|
  | [Chronos-2](https://github.com/amazon-science/chronos-forecasting) | Apache-2.0 | 有 CPU 友好的小版本 |
  | [TimesFM](https://github.com/google-research/timesfm) 2.5 / 3.0 | 2.5 是 Apache-2.0；**3.0 权重禁止商用** | — |
  | [Toto](https://github.com/DataDog/toto) | Apache-2.0 | 4M 到 2.5B 多种尺寸 |
  | [Kronos](https://github.com/shiyu-coder/Kronos) | MIT | 专门用 K 线训练，最小版 4M 参数 |
  | [TiRex](https://github.com/NX-AI/tirex) | NXAI 社区许可 | xLSTM 架构 |

  共识：**预测收益率的证据很弱，预测波动率还有一定价值。**
- **本地模型运行时**：Ollama、llama.cpp server、vLLM、SGLang、LM Studio 都提供 OpenAI 兼容接口和 JSON Schema 约束输出，接入方式和云端模型完全一样。

---

## 六、MT5 生态

| 事项 | 现状 |
|---|---|
| 官方 Python 包 | `MetaTrader5` 5.0.6231（2026-09），**只有 Windows x86-64**，支持到 Python 3.14 |
| 终端自带 MCP | build 6060（2026-07）起支持；后续版本还能让 AI 读取策略测试器报告 |
| MQL5 里跑 ONNX | build 3620 起支持，策略测试器里也能用 |
| Linux / Mac | 官方 Wine 脚本、[mt5linux](https://pypi.org/project/mt5linux/) 1.1.x、[gmag11 Docker 镜像](https://github.com/gmag11/MetaTrader5-Docker-Image)、siliconmetatrader5（Apple Silicon） |
| EA31337 | 上一个稳定版是 2023 年 8 月，半停更 |
| 外汇方向的替代 | cTrader（2025 年起支持原生 Python，官方提供 Linux Docker 镜像） |

---

## 七、模型版图（2026-11）

- OpenAI、Anthropic、Google 在 2026 年下半年都发布了新一代旗舰，并且都提供"旗舰档 / 快速档 / 低价档"三档，外加**推理强度调节、prompt 缓存、Batch API、低价服务档（flex）**。具体 ID 变化很快，统一放进配置里的模型注册表，不要写死在代码里。
- **DeepSeek**：V4（Flash / Pro 两档，2026-04 发布，1M 上下文）接替 V3.2。**多家托管商已经下线 V3.2，DeepSeek 官方的旧模型名也已停用**，需要尽快迁移（P0-12）。
- **开源权重**：Qwen 3.5 / 3.6 系列、Gemma 4、GLM-5.x、Kimi K3、gpt-oss 等。注意其中有几个用的是自定义许可证，商用前要看清楚。
- 最后再强调一次：上面所有基准都说明，**模型的通用能力和交易盈亏没有稳定关系**，最终选谁要看自己的前向测试。

---

## 八、本计划从开源社区借来的 15 个点子

| # | 点子 | 来自 | 对应任务 |
|---|---|---|---|
| 1 | 原生结构化输出，取代正则提取 + LLM 自修复 | 各家 API | P0-13、P4-02 |
| 2 | 计算交给 Python，LLM 只推理；数字必须能在数据里找到出处 | FinRobot、Vibe-Trading | P4-14 |
| 3 | 能力分级的账户配置 + 实盘授权书 + 急停 | Vibe-Trading | P3-05、P3-10 |
| 4 | 订单先落盘再发送 | Vibe-Trading | P3-06 |
| 5 | 逐笔确认模式 / 自动驾驶模式 | NOFX、ai-hedge-fund | P3-11 |
| 6 | 哈希链审计账本 | ai-hedge-fund | P3-12 |
| 7 | 分析师并行 + 分歧时多空辩论 + 裁判 | TradingAgents、FinRobot、Poisoning Agentic Alpha | P4-04 |
| 8 | 决策日志自动打分 + 复盘记忆 | TradingAgents | P4-09 |
| 9 | 按已实现成绩给模型加权 | ContestTrade | P4-08 |
| 10 | 模型擂台：每个模型一个影子账户 + 排行榜 | Alpha Arena、nof0、Agentic Trading Lab | P8-02 |
| 11 | LLM 调用账本：录制 / 精确回放 / 重采样 | LumiBot、TradingAgents | P5-01 |
| 12 | 知识截止日防护：核心指标只看截止日之后 | Profit Mirage、DeepFund、HindsightBench | P5-03 |
| 13 | 回测 / 实盘同一套代码，Broker 用 adapter 模式 | NautilusTrader | P2-01、P5-04 |
| 14 | 防偏差命令行工具 | freqtrade 的 `lookahead-analysis` | llmbt |
| 15 | RL 只做仓位 / 风险覆盖，权重分配用 bandit | FinRL-X、RL 综述 | P6-03 |
