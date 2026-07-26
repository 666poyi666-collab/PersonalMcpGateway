# Personal MCP Gateway

Personal MCP Gateway is a local-first, modular MCP server for connecting trusted personal
applications to ChatGPT through one OpenAI Secure MCP Tunnel.

The gateway owns protocol adaptation, health, diagnostics, idempotency, and Windows service
operation. Each application remains the source of truth for its own business data.

## 这是什么 / What is this

用一句话说：**这台电脑上所有个人 MCP 项目的「总机房 + 总看板」。**

- **总机房** — 每个独立项目（手表、日记、业务数据……）各自是一个 MCP 服务；本网关把它们
  统一接到 ChatGPT，并负责健康检查、重试、诊断这些共同的脏活。
- **总看板** — 浏览器版（`http://127.0.0.1:8761/admin/status`）和桌面版（原生窗口 + 托盘
  图标）实时显示每个项目在线与否、调用量和异常。
- **可扩展** — 看板上的「扩展面板」留给你自己：日程、项目进度、新闻、股票、专注统计，
  编辑一个 YAML 文件就能加卡片，见 [docs/board-widgets.md](docs/board-widgets.md)。

三步开始 / Quick start:

1. 部署 Windows 服务（含网关与隧道）：见 [docs/deployment-windows.md](docs/deployment-windows.md)。
2. 打开看板确认状态：浏览器访问 `http://127.0.0.1:8761/admin/status`。
3. 想要桌面窗口就双击 `desktop\Install-PersonalMcpDesktop.cmd`（无需管理员权限），
   之后从桌面快捷方式「Poyi Control Center」启动。

Everything runs on this machine and listens on loopback only; nothing is exposed to the
network except the outbound Secure MCP Tunnel.

## Development

Requirements: Python 3.12 and `uv`.

```powershell
uv sync --all-extras --locked
uv run personal-mcp-gateway doctor
uv run personal-mcp-gateway serve
```

Endpoints:

- MCP: `http://127.0.0.1:8760/mcp`
- Health and administration: `http://127.0.0.1:8761`
- Real-time dashboard: `http://127.0.0.1:8761/admin/status`

The same board is available as a native window with a status tray icon. Install it for the current
user, with no elevation, using `desktop\Install-PersonalMcpDesktop.cmd`; see `docs/desktop.md`.
Both boards default to the light theme (dark stays one click away) and share the extensible
widget area described in `docs/board-widgets.md`.

Configuration examples are in `modules/`. Never commit application tokens, device IDs, tunnel
IDs, network addresses, personal content, or runtime logs. WatchIntervals runs its own independent
MCP Server and is intentionally not registered here.

See `docs/architecture.md`, `docs/module-development.md`, and
`docs/deployment-windows.md` for design and deployment details.

## Status

Version `0.1.0.dev0` is a development release. A local health response or one successful tunnel
call is not production acceptance; the release checklist includes Windows restart, sleep/wake,
real ChatGPT calls, fault injection, and long-running tests.
