# Changelog

## 0.1.0-dev

- Establish the independent Personal MCP Gateway repository.
- Add a responsive real-time control center for Personal, Watch, Foxlink, Journal, their Secure MCP
  Tunnels, 24-hour activity, recent calls, and redacted errors.
- Add loopback-only YAML dashboard targets so additional independent MCP projects can be monitored
  without changing Gateway core or the frontend.
- Add the Poyi Control Center desktop board: a frameless WebView2 window with a status tray icon,
  persisted geometry, dark and light themes, a compact panel, always-on-top, and hide-to-tray. The
  renderer makes no network calls; all gateway traffic runs in Python over the bridge.
- Encode status by shape as well as hue on the dashboard dots and the tray icon, so state survives
  colour vision deficiency at tray size.
- Add a user-scoped desktop installer, verifier, and uninstaller under `desktop/` that need no
  administrator rights, build the private runtime from `uv.lock`, and gate on a real window.
- Add shared probe caching, manual refresh, and an in-memory project recovery timeline.
- Add official MCP SDK integration, modular adapters, SQLite state, administration endpoints,
  and Windows service tooling.
- Remove the legacy WatchIntervals Adapter, tools, Resources, credential handling, and service
  coupling after WatchIntervals moved to its own independent MCP Server.
- Remove retired Watch manifest and DPAPI credential files during in-place upgrades.
