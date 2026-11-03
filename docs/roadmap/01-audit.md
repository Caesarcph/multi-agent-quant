# 01 · 现状体检报告

> 体检日期：2026-11-03。对象是三个仓库默认分支上的最新代码。
> 文中的数字都是实际跑出来的，复现命令放在文末。

[← 返回总览](README.md)

---

## 先说结论

| 仓库 | 一句话评价 |
|---|---|
| **multi-agent-quant** | 三个里思路最清楚、也最接近"能跑"的一个。问题在于安全带没系：默认配置下三层风控根本没生效，下单手数由 LLM 直接说了算。 |
| **llm-rl-mt5-trading** | 体量最大（约 4.5 万行），但主程序现在起不来（核心文件损坏），依赖也装不上。整体更像一堆脚手架，真正被验证过的部分不多。 |
| **llm-backtest-framework** | 目前只有 README。不过它的定位是三个里最有价值的：专门给 LLM 策略用的回测器（考虑延迟、token 成本、输出随机性），这块开源圈里还没有公认的标准方案。 |

---

## 总览

| 指标 | multi-agent-quant | llm-rl-mt5-trading | llm-backtest-framework |
|---|---|---|---|
| 最后一次提交 | 2026-01-27 | 2025-10-07 | 2026-02-05 |
| 提交数 | 1 | 2 | 1 |
| Python 代码量 | ≈ 7.9K 行 / 115 个文件 | ≈ 45.4K 行 / 152 个文件 | 0 |
| 测试结果 | 31 通过 / 1 跳过 | 304 通过 / 2 失败 / 18 个收集错误 ¹ | 无 |
| 测试覆盖率 | ≈ 23%（执行器、各 Agent 均为 0%） | 未统计 | — |
| Ruff 问题数 ² | 1,005（738 个可自动修） | 11,119（8,272 个可自动修） | — |
| 依赖管理 | `requirements.txt`，版本锁死但偏老 | `requirements.txt`，全是 `>=` 宽松约束，而且**装不上** | 无 |
| CI / pre-commit / 类型检查 | 都没有 | 都没有 | 都没有 |
| 运行平台 | 仅 Windows（MT5 Python 库限制） | 同左 | — |
| License | MIT | README 写着 MIT，但仓库里**没有 LICENSE 文件** | MIT |

¹ 在 Linux 上只装了轻量依赖时的结果：13 个是缺 TA-Lib，4 个是没装 gymnasium（环境原因），1 个是文件损坏。
² 规则集：`E,F,W,B,UP,SIM,I`。

---

## 一、multi-agent-quant

### 做得好、要保留的

- **研究部 / 交易部分离**：研究员只管方向，交易员查合约规格、算保证金，职责切得很对。
- **结构化输出 + 优雅降级**：所有信号都过 JSON Schema 校验；哪个模型挂了就让它"休假"，系统照常跑。
- **风控模块很全**：单笔 / 当日 / 账户 / 敞口 / 相关性五道闸，外加按回撤分级降仓。
- **研究基础设施有雏形**：审计日志、SQLite 分析库、纸面交易、上线门槛检查、动态权重、成交归因都有了。
- **纯函数部分的测试质量不错**：融合、各类 guard、Schema 校验都有单测。

### 问题清单（按严重度排序）

| # | 级别 | 问题 | 证据 | 后果 |
|---|---|---|---|---|
| 1 | 🔴 | 默认配置下风控不生效 | `config/risk_config.yaml` 中 `enforcement.enabled: false`、`capital_preservation.enabled: false`；`config/execution_config.yaml` 中 `enforce_safety: false` | README 和 CLAUDE.md 宣称的"三层风控 + 50% 回撤硬止损"实际上没在执行 |
| 2 | 🔴 | 下单手数由 LLM 决定 | `use_signal_volume: true`，模型输出的 `volume` 原样下单 | 模型一旦"幻觉"出 10 手，就真的下 10 手 |
| 3 | 🔴 | 主循环强制真下单 | `scripts/run_loop.py:401-402` 写死了 `enabled = True`、`dry_run = False` | 没法安全地演练或调试 |
| 4 | 🟠 | 研究员走 CLI 子进程（codex / claude / gemini） | `agents/cli_agent.py`，单次超时 180 秒，Windows 下还要绕 `cmd.exe` | 慢、容易挂、拿不到 token 用量和成本，也用不上原生结构化输出 |
| 5 | 🟠 | 全程串行 | `run_loop.py` 对最多 20 个品种 × 4 个模型逐个调用 | 一轮可能要几十分钟，15 分钟的周期根本跟不上 |
| 6 | 🟠 | MT5 连接反复初始化 / 关闭 | `run_loop.py` 扫描、研究、交易各自 `initialize()` + `shutdown()` | 又慢又容易出状态问题（MT5 库本身是进程级单例） |
| 7 | 🟠 | 工具调用协议是手搓的，JSON 靠正则提取 + 让模型自己修 | `agents/tool_loop.py`、`agents/json_utils.py`、DeepSeek 的 repair 流程 | 各家 API 早就原生支持 function calling 和 JSON Schema 约束解码了 |
| 8 | 🟠 | API 调用没有重试和退避 | `deepseek/client.py` | 偶发一个 429 / 5xx，模型就直接"休假" |
| 9 | 🟡 | 两套主流程并存 | `scripts/run_loop.py`（673 行）和 `orchestrator/cycle.py`（308 行）逻辑大量重复 | 改一处漏一处 |
| 10 | 🟡 | 不是一个可安装的包 | 脚本里到处 `sys.path.insert`，没有 `pyproject.toml` | 难测试、难复用 |
| 11 | 🟡 | 7 个 YAML 配置没有校验 | `tools/config_loader.py` 直接 `yaml.safe_load` | 键名拼错会静默失效 |
| 12 | 🟡 | 依赖偏老 | `openai==1.40.2`，`httpx==0.27.2`（当初为了绕开 `proxies` 报错锁死的） | 新特性用不上，安全补丁也跟不上 |
| 13 | 🟡 | 状态散在各种 JSON 文件里 | `logs/*.json` + 可选 SQLite | 并发写、回放、分析都不方便 |
| 14 | 🟡 | 文档和代码脱节 | `AGENTS.md` 还写着"仓库目前只有文档"；README 的 `git clone` 地址里留着 `<your-username>` | 新人和 AI 编程助手都会被误导 |
| 15 | 🟢 | 1,005 个 lint 问题 | 大部分是老式类型注解（`Dict`、`Optional`） | 一次 `ruff --fix` 就能清掉大半 |
| 16 | 🟢 | Schema 里把模型名写死成枚举 | `agents/prompts/output_schema.json`：`gpt \| claude \| gemini \| deepseek` | 想换模型、加模型都得改 Schema |

### 模型层面的新情况（2026 年下半年）

除了代码本身，外部环境也变了，有三件事需要尽快处理：

1. **DeepSeek V3.2 正在退场。** 配置里用的是 NVIDIA 端点上的 `deepseek-v3.2`。NVIDIA 是否已经下线还没查到官方说法，但 Baseten、腾讯云等托管商已经在 2026 年陆续下线 V3.2，DeepSeek 官方的旧模型名 `deepseek-chat` / `deepseek-reasoner` 也在 7 月 24 日之后彻底停用了，继任者是 4 月发布的 V4（Flash / Pro 两档）。
2. **V3.2 的前视偏差被点名了。** 2026 年 1 月的 Look-Ahead-Bench（arXiv 2601.13770）在多智能体交易框架里做测试，DeepSeek 3.2 是参测模型里"样本内外 alpha 落差"最大的之一。换句话说，用它在训练截止日之前的行情上回测，结果会明显偏乐观。
3. **"四个模型投票"这个设计本身有安全隐患。** 2026 年 8 月的 *Poisoning Agentic Alpha*（arXiv 2608.24069）发现，在多智能体交易系统里，多数投票非但挡不住被投毒的信号（比如被污染的新闻源、一个特别"能说"的异常 Agent），反而会把这个错误决定"焊死"。

这三条分别对应 [03 · 分阶段任务](03-phases.md) 里的 P0-12、P5-03 和 P4-04。

### 测试现状

- **31 通过 / 1 跳过**（跳过的是需要 MT5 终端的集成测试）。
- **覆盖率约 23%**。下面这些关键模块是 0%：
  - `execution/mt5_executor.py`（294 条语句，真正下单的地方）
  - `agents/trading_agent.py`、`agents/cli_agent.py`、`agents/deepseek_agent.py`
  - `validation/paper_trader.py`
- 缺的东西：主循环端到端测试、假券商（fake broker）、LLM 调用的录制回放测试。

---

## 二、llm-rl-mt5-trading

### 有价值、值得迁移的

| 模块 | 价值 |
|---|---|
| `strategies/base_strategies.py`、`indicators.py`、`custom_indicators.py` | 经典技术策略和多周期分析，正好拿来当"LLM 必须跑赢的基线" |
| `strategies/monte_carlo.py` | 蒙特卡洛与压力测试，可直接并入回测框架的稳健性分析 |
| `strategies/parameter_optimizer.py`、`weight_optimizer.py` | 参数 / 权重优化的思路（实现建议换成 Optuna） |
| `core/fund_manager.py` | 分阶段放量的资金管理（先小仓验证、达标再加仓），和 multi-agent-quant 的上线门槛检查天然互补 |
| `core/economic_event_monitor.py`、`symbol_config_manager.py` | 经济日历、EIA 库存、OPEC 会议、品种交易时段——正好补上"新闻窗口禁开仓"这类风控 |
| `core/risk_control_system.py`、`position_manager.py` | 熔断器、移动止损 |
| `core/trade_recorder.py` | 交易日志 |
| `core/alert_system.py` | Telegram / 邮件告警 |
| `llm/call_optimizer.py` | 自适应限流、结果缓存的思路 |
| `rl/` | 交易环境和训练器的骨架（需要重新定位，见下文） |

### 问题清单

| # | 级别 | 问题 | 证据 |
|---|---|---|---|
| 1 | 🔴 | **主程序一启动就崩** | `src/core/monitoring.py` 从第 60 行起内容是乱码（字符顺序被打乱，连类名都坏了），而 `src/core/trading_system.py:27` 在模块顶层就 import 了它，所以 `python main.py` 会直接报 `SyntaxError` |
| 2 | 🔴 | 另外两个文件整体损坏 | `tests/test_alert_system.py`、`examples/backtest_monte_carlo_integration.py` |
| 3 | 🔴 | **依赖装不上** | `requirements.txt` 里列了 `sqlite3` 和 `asyncio`（都是标准库），PyPI 上根本没有 `sqlite3`，`pip install -r` 直接失败；`talib-binary` 只有 cp34–cp37 的 wheel |
| 4 | 🔴 | 异常处理装饰器自身有 bug | `src/core/exceptions.py:296`：`wrapper` 里的局部 `from .logging import log_error` 遮住了同名参数，结果一旦捕获到异常，装饰器自己先抛 `UnboundLocalError`，原始错误被吞掉 |
| 5 | 🟠 | 和 EA31337 靠 JSON 文件轮询通信 | `src/bridge/ea31337_bridge.py` 读写 `signals.json` / `status.json` / `commands.json`；EA 端的 MQL5 代码不在仓库里 |
| 6 | 🟠 | 用本地 Llama 3.2 1B 做市场分析 | `src/llm/llama_model.py`。1B 模型的推理能力不够做这件事，而为它引入的 llama-cpp + torch + transformers + langchain 依赖又非常重 |
| 7 | 🟠 | RL 用来"优化策略权重"，但没有严格的样本外验证 | `src/rl/*`、`src/strategies/weight_optimizer.py`，过拟合风险很高 |
| 8 | 🟠 | 和 multi-agent-quant 大面积重复 | MT5 连接、风控、回测、报告、LLM 封装两边各写了一套 |
| 9 | 🟡 | 测试里引用了未定义的名字 | `tests/test_ea31337_bridge.py:466` 的 `logging`；`tests/test_risk_manager_agent.py` 的 `PortfolioRisk` |
| 10 | 🟡 | 单机场景硬依赖 Redis | `src/data/cache.py` |
| 11 | 🟡 | 诊断 / 安装脚本有 5 套 | `install.py`、`validate_setup.py`、`diagnose.py`、`src/utils/diagnostic_tool.py`、`src/core/environment.py` |
| 12 | 🟡 | 4 处裸 `except:` | `trade_recorder.py`、`cache.py`、`mt5_connection.py` 等 |
| 13 | 🟢 | 格式问题 | 7,163 处空行带空白、287 处行尾空格等，`ruff format` 一次搞定 |

---

## 三、llm-backtest-framework

- 目前只有 `README.md`、`ROADMAP.md`、`CONTRIBUTING.md`、`LICENSE`。
- README 的集成表把 Yahoo Finance、OpenAI、Anthropic、Gemini 都标成了 "✅ Built-in"，但实际上还没有一行代码。对外开源时这会显得不靠谱，应该改成 "🔜 Planned"。
- 示例里的模型名已经过时（GPT-4 / GPT-4o、2025 年 5 月的 Claude Sonnet 4）。
- `ROADMAP.md` 和 `CONTRIBUTING.md` 里的换行被写成了字面量 `\n`，在 GitHub 上显示成一整行。
- **定位非常好**：LLM 策略的回测必须考虑延迟、token 成本、输出随机性和前视偏差（模型训练时可能"见过"回测区间的行情），这些传统回测框架都不管。详见 [04 · 开源灵感地图](04-inspiration.md)。

---

## 四、三个仓库的共性问题

1. **工程基建全缺**：没有 CI、没有 pre-commit、没有类型检查、没有锁文件。
2. **没有统一的领域模型**：`Signal`、`Order`、`Position` 每个仓库各定义一套。
3. **没有可观测性**：看不到每次决策的完整链路、token 成本和延迟。
4. **没有 UI**：只能翻日志和 JSON 文件。
5. **被 Windows + MT5 终端绑死**：开发、测试、CI 都受限制。
6. **文档是 Markdown 堆出来的**：没有文档站，中英文混杂，部分内容和代码对不上。

---

## 复现命令

```bash
# multi-agent-quant：Linux 下用一个 MetaTrader5 桩模块跑测试
PYTHONPATH=<stub_dir>:. pytest -q --cov=.
ruff check . --select E,F,W,B,UP,SIM,I --statistics
```

```bash
# llm-rl-mt5-trading：确认文件损坏和依赖问题
python -c "import ast; ast.parse(open('src/core/monitoring.py', encoding='utf-8').read())"
```

```bash
uv pip install --dry-run sqlite3
```

```bash
ruff check . --select F821,F823 --output-format concise
```
