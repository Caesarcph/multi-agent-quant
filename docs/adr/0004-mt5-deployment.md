# ADR-0004：MT5 部署——Windows VPS 跑 Gateway，开发和 CI 用 Wine

- 状态：✅ 已采纳（2026-11-03）
- 日期：2026-11-03

## 背景

官方 `MetaTrader5` Python 包只有 Windows wheel，而且要连一个正在运行的终端。现在整个系统都被绑在 Windows 上。

## 选项

1. **Windows VPS 跑一个很薄的 MT5 Gateway 服务，其余部分跑在任意平台（推荐）**：Gateway 独占 MT5 连接，通过 HTTP / WebSocket 提供行情和下单；开发和 CI 用 Wine 方案（官方 Wine 脚本、mt5linux、gmag11 Docker 镜像）连模拟盘。
2. **全部跑在 Wine / Docker 里**：一台 Linux 机器搞定，但实盘稳定性没有原生 Windows 有把握。
3. **MetaApi 等云服务**：省事，但要把账户凭证交给第三方，而且收费。不推荐。

## 决定

选项 1。真金白银的账户放在原生 Windows 上；需要 Windows 的只有 Gateway。

## 后果

- 这一轮已经做到"没有 Windows 也能开发和跑测试"：`tests/fakes/mt5.py` 模拟终端，CI 同时跑 Ubuntu 和 Windows。
- 阶段 2 要实现 Gateway（P2-02）和 Broker 契约测试（P2-01）。
- **拍板时补充：眼下先不在 Windows 上跑。** 阶段 2、3 的代码全部在 Linux 上开发，MT5 相关部分用假终端（`tests/fakes/mt5.py`）测；Gateway 代码照样写好、测好，等真要连账户时再部署到 Windows。依赖真实终端的验收项（P2-02 的"24 小时不掉线"、P2-03 的"模拟盘上契约测试全过"、P0-11 的基线采集）顺延到那时。
- MT5 终端从 build 6060 起自带 MCP Server，只用于运维查询，系统里禁用它的下单工具。
