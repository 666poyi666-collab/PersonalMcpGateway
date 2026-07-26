# Changelog

## 0.1.0-dev

- Establish the independent Personal MCP Gateway repository.
- Add a responsive real-time control center for Personal, Watch, Foxlink, Journal, their Secure MCP
  Tunnels, 24-hour activity, recent calls, and redacted errors.
- Add loopback-only YAML dashboard targets so additional independent MCP projects can be monitored
  without changing Gateway core or the frontend.
- Add shared probe caching, manual refresh, and an in-memory project recovery timeline.
- Add official MCP SDK integration, modular adapters, SQLite state, administration endpoints,
  and Windows service tooling.
- Remove the legacy WatchIntervals Adapter, tools, Resources, credential handling, and service
  coupling after WatchIntervals moved to its own independent MCP Server.
- Remove retired Watch manifest and DPAPI credential files during in-place upgrades.
