# Personal MCP Gateway

Personal MCP Gateway is a local-first, modular MCP server for connecting trusted personal
applications to ChatGPT through one OpenAI Secure MCP Tunnel.

The gateway owns protocol adaptation, health, diagnostics, idempotency, and Windows service
operation. Each application remains the source of truth for its own business data.

## Development

Requirements: Python 3.12 and `uv`.

```powershell
uv sync --all-extras --locked
$env:PERSONAL_MCP_SECRET_WATCH_PHONE_TOKEN = "your-local-pairing-token"
uv run personal-mcp-gateway doctor
uv run personal-mcp-gateway serve
```

Endpoints:

- MCP: `http://127.0.0.1:8760/mcp`
- Health and administration: `http://127.0.0.1:8761`

Configuration examples are in `modules/`. Never commit pairing tokens, device IDs, tunnel IDs,
network addresses, routes, health records, or runtime logs.

See `docs/architecture.md`, `docs/module-development.md`, and
`docs/deployment-windows.md` for design and deployment details.

## Status

Version `0.1.0.dev0` is a development release. A local health response or one successful tunnel
call is not production acceptance; the release checklist includes Windows restart, sleep/wake,
real ChatGPT calls, fault injection, and long-running tests.
