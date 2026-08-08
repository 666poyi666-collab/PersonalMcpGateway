# Personal MCP Gateway

Personal MCP Gateway is a local-first, modular MCP server for connecting trusted personal
applications to ChatGPT through one OpenAI Secure MCP Tunnel.

The gateway owns protocol adaptation, health, diagnostics, idempotency, and Windows service
operation. Each application remains the source of truth for its own business data.

## 这是什么 / What is this

用一句话说：**这台电脑上所有个人 MCP 项目的「总机房 + 总看板」。**

- **总机房** — 每个独立项目（手表、日记、业务数据……）各自是一个 MCP 服务；本网关把它们
  统一接到 ChatGPT，并负责健康检查、重试、诊断这些共同的脏活。
- **总看板** — 浏览器版提供完整技术监控；桌面版由一个管理窗口、5 张可分别开关和摆放的
  独立卡片、系统托盘组成。打开或关闭管理窗口不会顺带改变卡片。
- **可扩展** — `board-widgets.yaml` 给桌面项目卡片提供数据，也能在浏览器看板的「扩展面板」
  添加日程、项目进度等自定义内容，见 [docs/board-widgets.md](docs/board-widgets.md)。

三步开始 / Quick start:

1. 部署 Windows 服务（含网关与隧道）：见 [docs/deployment-windows.md](docs/deployment-windows.md)。
2. 打开看板确认状态：浏览器访问 `http://127.0.0.1:8761/admin/status`。
3. 想要桌面卡片就双击 `desktop\Install-PersonalMcpDesktop.cmd`（无需管理员权限），
   之后从快捷方式或托盘打开管理面板，在首屏直接开关任意一张卡片。

保活与自愈：全部 4 个项目共 8 个 Windows 服务由 `fleet\` 的 **PoyiFleetWatchdog** 统一看护
（开机自启、崩溃重启、降级自动恢复）。出问题双击 `fleet\Repair-PoyiFleet.cmd` 一键修复，
`fleet\Status-PoyiFleet.cmd` 随时体检，详见 [docs/fleet-operations.md](docs/fleet-operations.md)。

Everything runs on this machine and listens on loopback only; nothing is exposed to the
network except the outbound Secure MCP Tunnel.

The optional Cloudflare layer keeps approved cloud data or the last successful snapshot readable
while this PC is off. FocusLink now has a versioned cloud MCP contract for a minimal
server-readable task/session projection, but the canonical Worker contract copy, staging OAuth,
deployment, and real-device PC-off evidence are still pending. When authority verification fails,
the cloud overview returns `unknown` with no copied revision or status fields, never a false live
state. Device sync credentials and MCP OAuth credentials remain separate.

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

The desktop companion has a management window, five independent card windows and a status tray
icon. Install it for the current user, with no elevation, using
`desktop\Install-PersonalMcpDesktop.cmd`; see `docs/desktop.md`. Project-bound widgets feed the
desktop cards, while the browser dashboard remains the full extensible monitoring surface described
in `docs/board-widgets.md`.

Configuration examples are in `modules/`. Never commit application tokens, device IDs, tunnel
IDs, network addresses, personal content, or runtime logs. WatchIntervals runs its own independent
MCP Server and is intentionally not registered here.

See `docs/architecture.md`, `docs/module-development.md`, and
`docs/deployment-windows.md` for design and deployment details.

## Status

Version `0.1.0.dev0` is a development release. A local health response or one successful tunnel
call is not production acceptance; the release checklist includes Windows restart, sleep/wake,
real ChatGPT calls, fault injection, and long-running tests.
