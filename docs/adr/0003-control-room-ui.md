# ADR-0003：控制台技术栈——Next.js + shadcn/ui，先用 Streamlit 过渡

- 状态：🟡 提议中（等你拍板，最晚第 8 周）
- 日期：2026-11-03

## 背景

现在没有任何界面，只能翻日志和 JSON。阶段 7 要做一个八页的控制台：总览、议会、交易日志、模型擂台、回测实验室、成本与延迟、风控、设置。

## 选项

1. **Next.js + shadcn/ui + Tailwind + TradingView Lightweight Charts v5（推荐）**：上限最高，最好看，前后端分离；代价是要写 TypeScript。
2. **NiceGUI 3（纯 Python）**：不用碰前端，WebSocket 实时更新也方便；定制空间小一些。
3. **只用 Streamlit**：最快，但实时性和交互（逐笔确认、急停）不够好。

## 决定（建议）

选项 1 做正式控制台；在它做好之前，先用 Streamlit 搭一个只读看板（直接读 DuckDB / Parquet），marimo 做交互式分析。

## 后果

- 需要一个 FastAPI 后端推送事件（阶段 7 的 P7-01）。
- TradingView Lightweight Charts 是 Apache-2.0，但页面上要保留 TradingView 署名。
