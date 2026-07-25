# Architecture

ChatGPT connects to one fixed OpenAI Secure MCP Tunnel. The tunnel forwards Streamable HTTP to
the loopback-only gateway. The gateway freezes its MCP tool registry at process startup and routes
calls to isolated adapters.

The core owns MCP compatibility, lifecycle, result envelopes, error mapping, health, SQLite state,
idempotency, audit summaries, redaction, and administration. Adapters own only connectivity and
translation for one application. They cannot manage the gateway or tunnel and cannot call another
adapter.

SQLite uses WAL and stores operational state only. Plans, workouts, routes, sleep records, journal
content, and Foxlink history remain in their source applications.

`/healthz` means the process is alive. `/readyz` means MCP can accept requests; an offline or failed
adapter degrades module health but does not make the gateway unready.

## Compatibility boundary

Only `mcp/sdk_compat.py` imports and initializes FastMCP. SDK upgrades must be isolated from
business changes and validated by comparing tool/resource lists, Inspector output, and Tunnel calls.

## Network boundaries

- `127.0.0.1:8760/mcp`: Streamable HTTP MCP
- `127.0.0.1:8761`: health, metrics, and administration
- `127.0.0.1:8877`: tunnel-client health and local UI
- WatchIntervals phone and watch: trusted LAN HTTP with pairing authentication
