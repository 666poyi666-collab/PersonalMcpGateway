# Personal MCP Gateway

Personal MCP Gateway is a local-first, modular MCP server for connecting trusted personal
applications to ChatGPT through one OpenAI Secure MCP Tunnel.

The gateway owns protocol adaptation, health, diagnostics, idempotency, and Windows service
operation. Each application remains the source of truth for its own business data.

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

Configuration examples are in `modules/`. Never commit application tokens, device IDs, tunnel
IDs, network addresses, personal content, or runtime logs. WatchIntervals runs its own independent
MCP Server and is intentionally not registered here.

See `docs/architecture.md`, `docs/module-development.md`, and
`docs/deployment-windows.md` for design and deployment details.

## Status

Version `0.1.0.dev0` is a development release. A local health response or one successful tunnel
call is not production acceptance; the release checklist includes Windows restart, sleep/wake,
real ChatGPT calls, fault injection, and long-running tests.
