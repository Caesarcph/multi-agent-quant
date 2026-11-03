# 05 · 美化与体验

[← 返回总览](README.md)

> 目标是四句话：**打开就想用，截图就想发，两分钟能跑起来，三个仓库一眼看出是一家的。**

---

## 一、控制台（Control Room）

### 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | FastAPI | REST + WebSocket / SSE，订阅事件日志实时推送 |
| 前端框架 | Next.js + React + TypeScript | 生态最成熟 |
| 组件与样式 | shadcn/ui + Tailwind CSS | 好看、好改，暗色主题现成 |
| 数据请求 | TanStack Query / TanStack Table | 缓存、轮询、大表格 |
| K 线与资金曲线 | TradingView Lightweight Charts v5 | v5 支持多窗格（价格 / 资金 / 回撤叠在一起）。Apache-2.0，**需要在页面上保留 TradingView 署名** |
| 实时持仓 / 订单表 | Perspective | 流式表格，适合高频刷新的数据 |
| 纯 Python 备选 | NiceGUI 3 | 不想碰前端时用，代价是天花板低一些 |

**过渡方案**：正式控制台做出来之前（阶段 7 之前），先用 Streamlit 搭一个只读看板，marimo 做交互式分析。两者都直接读 DuckDB / Parquet，不用写后端。

### 八个页面

| 页面 | 核心内容 | 灵感来源 |
|---|---|---|
| **总览** | 资金曲线 + 回撤（同一张图的两个窗格）、今日盈亏、敞口热力图、各道熔断的状态灯、下一轮倒计时、今日 LLM 花费 vs 预算、**急停按钮** | 专业交易终端 |
| **议会** | 每个品种一张卡片：模型 × 买 / 卖 / 观望 × 置信度的投票矩阵、行情类型一致度、融合结果、辩论时间线（推理和工具调用可折叠，一键跳到 Langfuse 的完整 trace）、风控逐项检查结果、最终决定 | Alpha Arena 的"模型对话"、TradingAgents 的终端面板 |
| **交易日志** | 每笔交易的完整链路：快照哈希 → 各分析师观点 → 辩论 → 提案 → 风控 → 订单 → 成交 → 结果 → 复盘；K 线上标出开平仓位置 | — |
| **模型擂台** | 每个模型一个影子账户：收益赛跑图、排行榜（收益、夏普、最大回撤、校准分数、成本） | Alpha Arena、nof0、LiveTradeBench |
| **回测实验室** | 回测列表与对比、多次重采样的收益分布带、扣除 LLM 成本后的指标、截止日前 / 后分开统计、PBO / DSR | — |
| **成本与延迟** | 每个模型每天花了多少钱 vs 赚了多少钱、token 用量、缓存命中率、p50 / p95 延迟、失败率 | — |
| **风控** | 当前限额 vs 代码里的硬上限、熔断状态和历史、授权书、当前放量阶段、审计账本校验 | Vibe-Trading |
| **设置** | 模型注册表、角色 × 模型矩阵、调度、风控参数（界面上**只能往紧了调**）、通知渠道 | — |

### "议会"页长什么样

```text
┌─ EURUSD · M15 · 第 1287 轮 · 14:30:05 ────────────────────────── 已放行 ✅ ─┐
│                                                                              │
│  模型        观点      置信度   行情判断        一句话理由                   │
│  ──────────  ────────  ───────  ──────────────  ─────────────────────────   │
│  模型 A      🟢 看多    0.72     上升趋势        回踩 1.0850 支撑后放量       │
│  模型 B      🟢 看多    0.64     上升趋势        美元指数走弱                 │
│  模型 C      ⚪ 观望    0.41     震荡            21:30 有 CPI，等数据         │
│  模型 D      🔴 看空    0.55     震荡            上方 1.0920 是前高阻力       │
│                                                                              │
│  分歧度 0.38 → 触发辩论（1 轮）          裁判结论：看多，置信度下调到 0.58  │
│                                                                              │
│  风控 ✅止损已设  ✅ATR 距离合理  ✅点差 0.8  ⚠️CPI 前 30 分钟（仓位 × 0.5）  │
│  最终：买入 0.12 手 · 止损 1.0838 · 止盈 1.0912 · 风险 $48（权益的 0.5%）    │
│                                                                              │
│  [展开辩论记录]   [查看完整 trace ↗]   [查看快照]                            │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 视觉规范

| 项目 | 规范 |
|---|---|
| 主题 | 默认暗色（交易终端的习惯），支持亮色 |
| **涨跌配色** | 提供"红涨绿跌（国内习惯）/ 绿涨红跌（国际习惯）"切换，另备一套色盲友好配色（蓝 / 橙） |
| 模型颜色 | 每家模型一个固定颜色，所有图表里保持一致 |
| 字体 | 界面用 Inter，数字用等宽字体（JetBrains Mono）并开启等宽数字；中文回退到思源黑体 / Noto Sans SC |
| 数字 | 右对齐，小数位按品种的报价精度统一（EURUSD 5 位、USDJPY 3 位） |
| 动效 | 克制：价格跳动时闪一下就够了 |
| 手机端 | 至少保证"总览、急停、逐笔确认"三个功能在手机上好用 |

### 交互上的安全设计

- **急停按钮**：长按 2 秒或者输入品种代码才生效，防止误触；每次触发都写进审计账本。
- **逐笔确认卡片**：把要下的单完整列出来（品种、方向、手数、止损止盈、风险金额），带倒计时，超时自动作废。
- **设置页只能收紧风控**：想放宽限额必须改配置文件并重启，界面上做不到。

---

## 二、终端体验

- `maq run`：用 Rich 画一个实时面板，显示当前这一轮的进度、每个模型的状态（思考中 / 已回答 / 休假）、投票结果和最终决定。参考 TradingAgents 的终端界面。
- `maq doctor`：一张清单，逐项打 ✓ / ✗（Python 版本、依赖、API key、MT5 Gateway 连通性、磁盘空间、时钟同步）。
- （可选）Textual 做一个运维终端，SSH 到 VPS 上就能看。

---

## 三、README 改版

### 结构

1. Logo + 一句话介绍 + 徽章（CI、覆盖率、Python 版本、许可证、文档；llmbt 再加 PyPI）
2. 一张控制台的演示 GIF
3. "为什么做这个"三句话
4. 30 秒快速开始（`docker compose up` 看 Demo）
5. 架构图（Mermaid）
6. 功能列表
7. 文档、路线图、贡献指南的链接
8. 免责声明

**中英双语**：`README.md` 写英文，`README.zh-CN.md` 写中文，两个文件顶部互相链接。

### 模板

````markdown
<p align="center">
  <img src="docs/assets/logo.svg" width="96" alt="logo">
</p>

<h1 align="center">multi-agent-quant</h1>

<p align="center">
  <b>A council of LLM analysts that debate, a risk kernel that can't be bypassed.</b><br>
  多个 LLM 分析师辩论出观点，一个不可绕过的风控内核决定能不能下单。
</p>

<p align="center">
  <a href="#"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Caesarcph/multi-agent-quant/ci.yml?branch=master"></a>
  <a href="#"><img alt="Python" src="https://img.shields.io/badge/python-3.13-blue"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-green"></a>
</p>

<p align="center">English · <a href="README.zh-CN.md">简体中文</a></p>

![Control Room](docs/assets/control-room.gif)

## Quick start (demo, no MT5 or API key needed)

```bash
docker compose --profile demo up
```
````

---

## 四、三个仓库的统一品牌

| 项目 | 做法 |
|---|---|
| 命名 | 仓库名保持不变；平台内部包统一叫 `maq-*`（只在 workspace 里用，不发 PyPI——`maq` 这个名字在 PyPI 上已经被别的项目占了）；回测库叫 `llmbt`（2026-11-03 查过 PyPI 上还没人用，**建议尽快发一个占位版本**） |
| Logo | 同一套简洁的几何风格，三个仓库换个颜色区分 |
| 社交预览图 | 每个仓库做一张 1280 × 640 的预览图，分享链接时显示 |
| 仓库门面 | 补齐 topics、简介、网站链接；Issue 模板、PR 模板、`SECURITY.md`、`CODE_OF_CONDUCT.md`；统一的标签（`phase:0`…`phase:9`、`priority:P0`…`P2`） |
| 文档站 | 每个仓库一个 Zensical 站点，主题和配色一致，互相链接 |

---

## 五、Demo 模式

开源项目能不能火，很大程度上取决于"别人能不能两分钟内看到效果"。

- `docker compose --profile demo up`：回放一段录制好的行情和模型回答，完整走一遍"快照 → 议会 → 辩论 → 风控 → 下单（纸面）"，控制台里全程可见。
- 不需要 MT5，不需要任何 API key。
- 用 Playwright 自动录制 README 里的演示 GIF，每次发版自动更新，保证截图永远和最新界面一致。

---

## 六、验收清单

- [ ] 控制台八个页面都接上了真实数据
- [ ] 手机上能完成"看总览、急停、逐笔确认"三件事
- [ ] 红涨绿跌 / 绿涨红跌可切换
- [ ] 中英双语 README，带演示 GIF
- [ ] 全新机器上 `docker compose --profile demo up` 两分钟内能看到效果
- [ ] 三个仓库的 Logo、配色、预览图、标签统一
